"""Secret storage boundary; database records contain only opaque references."""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, value: str) -> None: ...

    def delete(self, reference: str) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    @property
    def values_count(self) -> int:
        return len(self._values)

    def get(self, reference: str) -> str | None:
        return self._values.get(reference)

    def set(self, reference: str, value: str) -> None:
        if not value:
            raise ValueError("secret cannot be empty")
        self._values[reference] = value

    def delete(self, reference: str) -> None:
        self._values.pop(reference, None)


class KeyringSecretStore:
    """Windows Credential Manager-backed production store via keyring."""

    def __init__(self, service_name: str = "VtNote") -> None:
        import keyring

        self._keyring = keyring
        self._service_name = service_name

    def get(self, reference: str) -> str | None:
        return self._keyring.get_password(self._service_name, reference)

    def set(self, reference: str, value: str) -> None:
        if not value:
            raise ValueError("secret cannot be empty")
        self._keyring.set_password(self._service_name, reference, value)

    def delete(self, reference: str) -> None:
        try:
            self._keyring.delete_password(self._service_name, reference)
        except self._keyring.errors.PasswordDeleteError:
            pass
