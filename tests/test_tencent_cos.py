from __future__ import annotations

import hashlib
import io
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from vtnote.cloud_submissions import CosLocator
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.provider_credentials import TencentCredentialBundle
from vtnote.tencent_cos import (
    CosContext,
    QcloudCosSdkAdapter,
    SensitiveUrl,
    TencentCosStager,
    build_qcloud_cos_sdk,
)


TASK_ID = "11111111-1111-4111-8111-111111111111"
BUCKET = "private-audio-1250000000"
REGION = "ap-guangzhou"
DATA = b"encoded opus audio"
SHA256 = hashlib.sha256(DATA).hexdigest()


class FakeCosSdk:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, object, str]] = []
        self.uploaded_bytes: list[bytes] = []
        self.deletes: list[tuple[str, str]] = []
        self.presigned_url = (
            f"https://{BUCKET}.cos.{REGION}.myqcloud.com/"
            f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg"
            "?q-sign-algorithm=sha1&q-ak=hidden&q-signature=hidden"
        )
        self.presign_calls: list[tuple[str, str, str, int]] = []

    def put_object(
        self,
        *,
        bucket: str,
        key: str,
        body: object,
        content_type: str,
    ) -> None:
        self.uploads.append((bucket, key, body, content_type))
        self.uploaded_bytes.append(body.read())  # type: ignore[union-attr]

    def get_presigned_url(
        self,
        *,
        method: str,
        bucket: str,
        key: str,
        expires_seconds: int,
    ) -> str:
        self.presign_calls.append((method, bucket, key, expires_seconds))
        return self.presigned_url

    def delete_object(self, *, bucket: str, key: str) -> None:
        self.deletes.append((bucket, key))


def audio(tmp_path: Path) -> PreparedAudio:
    path = tmp_path / "encoded.ogg"
    path.write_bytes(DATA)
    return PreparedAudio(
        path=path,
        asset_id="asset-1",
        converted=True,
        media_info=MediaInfo(
            duration_ms=60_000,
            size_bytes=len(DATA),
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )


def context(*, recoverable_copy: bool = True) -> CosContext:
    return CosContext(
        task_id=TASK_ID,
        audio_sha256=SHA256,
        bucket=BUCKET,
        region=REGION,
        recoverable_copy=recoverable_copy,
    )


def locator() -> CosLocator:
    return CosLocator(
        bucket=BUCKET,
        region=REGION,
        object_key=f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg",
    )


def test_put_uses_deterministic_non_title_key_and_streams_recoverable_audio(
    tmp_path: Path,
) -> None:
    sdk = FakeCosSdk()

    created = TencentCosStager(sdk).put(audio(tmp_path), context())

    assert created == locator()
    assert len(sdk.uploads) == 1
    bucket, key, body, content_type = sdk.uploads[0]
    assert bucket == BUCKET
    assert key == f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg"
    assert isinstance(body, io.BufferedReader)
    assert body.closed
    assert sdk.uploaded_bytes == [DATA]
    assert content_type == "audio/ogg"


def test_put_refuses_missing_recoverable_copy_or_hash_mismatch(
    tmp_path: Path,
) -> None:
    sdk = FakeCosSdk()
    stager = TencentCosStager(sdk)

    with pytest.raises(ValueError, match="recoverable"):
        stager.put(audio(tmp_path), context(recoverable_copy=False))
    wrong = CosContext(
        task_id=TASK_ID,
        audio_sha256="0" * 64,
        bucket=BUCKET,
        region=REGION,
        recoverable_copy=True,
    )
    with pytest.raises(ValueError, match="hash"):
        stager.put(audio(tmp_path), wrong)

    assert sdk.uploads == []


def test_presign_is_single_object_six_hours_and_secret_safe() -> None:
    sdk = FakeCosSdk()
    stager = TencentCosStager(sdk)

    signed = stager.presign_get(locator(), timedelta(hours=6))

    assert isinstance(signed, SensitiveUrl)
    assert sdk.presign_calls == [
        ("GET", BUCKET, locator().object_key, 21_600)
    ]
    assert signed.reveal() == sdk.presigned_url
    assert sdk.presigned_url not in repr(signed)
    assert sdk.presigned_url not in str(signed)
    assert "q-signature" not in repr(signed)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        (
            "https://attacker.example/"
            f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg?q-signature=hidden"
        ),
        (
            f"http://{BUCKET}.cos.{REGION}.myqcloud.com/"
            f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg?q-signature=hidden"
        ),
        (
            f"https://{BUCKET}.cos.ap-shanghai.myqcloud.com/"
            f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg?q-signature=hidden"
        ),
        (
            f"https://{BUCKET}.cos.{REGION}.myqcloud.com/"
            f"vtnote-runtime/{TASK_ID}/wrong.ogg?q-signature=hidden"
        ),
        (
            f"https://{BUCKET}.cos.{REGION}.myqcloud.com/"
            f"vtnote-runtime/{TASK_ID}/{SHA256[:16]}.ogg"
        ),
    ],
)
def test_presign_rejects_wrong_transport_host_path_or_unsigned_url(
    unsafe_url: str,
) -> None:
    sdk = FakeCosSdk()
    sdk.presigned_url = unsafe_url

    with pytest.raises(ValueError, match="signed COS URL"):
        TencentCosStager(sdk).presign_get(locator(), timedelta(hours=6))


def test_delete_targets_only_the_validated_locator() -> None:
    sdk = FakeCosSdk()

    TencentCosStager(sdk).delete(locator())

    assert sdk.deletes == [(BUCKET, locator().object_key)]


@pytest.mark.parametrize(
    "ttl",
    [timedelta(seconds=0), timedelta(hours=6, seconds=1)],
)
def test_presign_rejects_nonpositive_or_overlong_ttl(ttl: timedelta) -> None:
    sdk = FakeCosSdk()

    with pytest.raises(ValueError, match="TTL"):
        TencentCosStager(sdk).presign_get(locator(), ttl)

    assert sdk.presign_calls == []


def test_production_sdk_has_no_proxy_redirect_retry_or_domain_switch() -> None:
    sdk = build_qcloud_cos_sdk(
        TencentCredentialBundle(
            secret_id=SecretStr("AKID-example"),
            secret_key=SecretStr("secret-key"),
        ),
        bucket=BUCKET,
        region=REGION,
    )

    assert isinstance(sdk, QcloudCosSdkAdapter)
    client = sdk.client
    assert client._retry == 0
    assert client._session.trust_env is False
    assert client._conf._allow_redirects is False
    assert client._conf._auto_switch_domain_on_retry is False
    assert client._conf._enable_internal_domain is False
    assert client._conf._endpoint == "cos.ap-guangzhou.myqcloud.com"
    assert client._conf.get_host(Bucket=BUCKET) == (
        "private-audio-1250000000.cos.ap-guangzhou.myqcloud.com"
    )
    https_adapter = client._session.get_adapter("https://")
    assert https_adapter.max_retries.total == 0
    assert "secret-key" not in repr(sdk)
