"""Private Tencent COS staging with deterministic keys and secret-safe URLs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID

from vtnote.cloud_submissions import CosLocator
from vtnote.media import PreparedAudio
from vtnote.provider_credentials import TencentCredentialBundle


class CosSdk(Protocol):
    def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: BinaryIO,
        content_type: str,
    ) -> None: ...

    def get_presigned_url(
        self,
        *,
        method: str,
        bucket: str,
        key: str,
        expires_seconds: int,
    ) -> str: ...

    def delete_object(self, *, bucket: str, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CosContext:
    task_id: str
    audio_sha256: str
    bucket: str
    region: str
    recoverable_copy: bool

    def locator(self) -> CosLocator:
        try:
            task_id = str(UUID(self.task_id))
        except (ValueError, AttributeError):
            raise ValueError("invalid COS task ID") from None
        return CosLocator(
            bucket=self.bucket,
            region=self.region,
            object_key=(
                f"vtnote-runtime/{task_id}/{self.audio_sha256[:16]}.ogg"
            ),
        )


class SensitiveUrl:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid signed COS URL")
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SensitiveUrl([redacted])"

    def __str__(self) -> str:
        return "[redacted signed COS URL]"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TencentCosStager:
    def __init__(self, sdk: CosSdk) -> None:
        self.sdk = sdk

    def put(
        self,
        audio: PreparedAudio,
        context: CosContext,
    ) -> CosLocator:
        if not isinstance(context, CosContext):
            raise ValueError("invalid COS context")
        if not context.recoverable_copy or audio.asset_id is None:
            raise ValueError("recoverable encoded audio is required")
        path = Path(audio.path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("recoverable encoded audio is unavailable")
        if _sha256(path) != context.audio_sha256:
            raise ValueError("encoded audio hash mismatch")
        locator = context.locator()
        with path.open("rb") as stream:
            self.sdk.put_object(
                bucket=locator.bucket,
                key=locator.object_key,
                body=stream,
                content_type="audio/ogg",
            )
        return locator

    def presign_get(
        self,
        locator: CosLocator,
        ttl: timedelta,
    ) -> SensitiveUrl:
        if not isinstance(locator, CosLocator):
            raise ValueError("invalid private COS locator")
        seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else 0
        if seconds <= 0 or seconds > 6 * 60 * 60 or not seconds.is_integer():
            raise ValueError("signed COS URL TTL must be 1-21600 seconds")
        value = self.sdk.get_presigned_url(
            method="GET",
            bucket=locator.bucket,
            key=locator.object_key,
            expires_seconds=int(seconds),
        )
        self._validate_signed_url(value, locator)
        return SensitiveUrl(value)

    @staticmethod
    def _validate_signed_url(value: str, locator: CosLocator) -> None:
        try:
            parts = urlsplit(value)
            port = parts.port
        except (TypeError, ValueError):
            raise ValueError("invalid signed COS URL") from None
        expected_host = (
            f"{locator.bucket}.cos.{locator.region}.myqcloud.com"
        )
        query_keys = {
            pair.partition("=")[0].casefold()
            for pair in parts.query.split("&")
            if pair
        }
        if (
            parts.scheme != "https"
            or parts.hostname != expected_host
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or unquote(parts.path.lstrip("/")) != locator.object_key
            or "q-signature" not in query_keys
        ):
            raise ValueError("invalid signed COS URL")

    def delete(self, locator: CosLocator) -> None:
        if not isinstance(locator, CosLocator):
            raise ValueError("invalid private COS locator")
        self.sdk.delete_object(
            bucket=locator.bucket,
            key=locator.object_key,
        )


class QcloudCosSdkAdapter:
    """Narrow wrapper around the pinned COS SDK surface used by VtNote."""

    __slots__ = ("client",)

    def __init__(self, client: object) -> None:
        self.client = client

    def __repr__(self) -> str:
        return "QcloudCosSdkAdapter()"

    def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: BinaryIO,
        content_type: str,
    ) -> None:
        self.client.put_object(  # type: ignore[attr-defined]
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    def get_presigned_url(
        self,
        *,
        method: str,
        bucket: str,
        key: str,
        expires_seconds: int,
    ) -> str:
        return self.client.get_presigned_url(  # type: ignore[attr-defined,no-any-return]
            Method=method,
            Bucket=bucket,
            Key=key,
            Expired=expires_seconds,
        )

    def delete_object(self, *, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]


def build_qcloud_cos_sdk(
    credentials: TencentCredentialBundle,
    *,
    bucket: str,
    region: str,
) -> QcloudCosSdkAdapter:
    """Build a direct-only client for one validated private Guangzhou bucket."""

    if not isinstance(credentials, TencentCredentialBundle):
        raise ValueError("complete Tencent credentials are required")
    CosLocator(
        bucket=bucket,
        region=region,
        object_key=(
            "vtnote-runtime/11111111-1111-4111-8111-111111111111/"
            "0000000000000000.ogg"
        ),
    )
    from qcloud_cos import CosConfig, CosS3Client
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 0
    no_retry = Retry(
        total=0,
        connect=0,
        read=0,
        redirect=0,
        status=0,
    )
    session.mount("https://", HTTPAdapter(max_retries=no_retry))
    endpoint = f"cos.{region}.myqcloud.com"
    config = CosConfig(
        Region=region,
        SecretId=credentials.secret_id.get_secret_value(),
        SecretKey=credentials.secret_key.get_secret_value(),
        Scheme="https",
        Endpoint=endpoint,
        Timeout=30,
        Proxies=None,
        AllowRedirects=False,
        EnableOldDomain=True,
        EnableInternalDomain=False,
        AutoSwitchDomainOnRetry=False,
        VerifySSL=True,
    )
    if (
        config.get_host(Bucket=bucket)
        != f"{bucket}.cos.{region}.myqcloud.com"
    ):
        raise ValueError("invalid COS bucket host")
    logging.getLogger("qcloud_cos").setLevel(logging.WARNING)
    return QcloudCosSdkAdapter(
        CosS3Client(config, retry=0, session=session)
    )
