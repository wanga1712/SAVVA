"""Windows DPAPI-backed local secrets used by SAVVA."""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


class SecretStoreError(RuntimeError):
    """Safe error raised when protected secret storage is unavailable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


def default_secret_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise SecretStoreError(
            "SECRET_STORE_UNAVAILABLE",
            "Windows local application storage is unavailable.",
        )
    return Path(root) / "SAVVA" / "openrouter-key.dpapi"


class WindowsDPAPISecretStore:
    """Encrypt one local provider key so only this Windows user can decrypt it."""

    _MAGIC = b"SAVVA-OPENROUTER-KEY-V1\0"

    def __init__(self, path: Path | None = None):
        self.path = path or default_secret_path()

    def get(self) -> str | None:
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise SecretStoreError(
                "SECRET_STORE_READ_FAILED", "The protected API key could not be read."
            ) from None
        plain = self._unprotect(encrypted)
        if not plain.startswith(self._MAGIC):
            raise SecretStoreError(
                "SECRET_STORE_INVALID", "The protected API key record is invalid."
            )
        try:
            value = plain[len(self._MAGIC):].decode("utf-8").strip()
        except UnicodeError:
            raise SecretStoreError(
                "SECRET_STORE_INVALID", "The protected API key record is invalid."
            ) from None
        return value or None

    def set(self, value: str) -> None:
        key = value.strip()
        if not key or len(key) > 4096 or any(ord(char) < 32 for char in key):
            raise SecretStoreError("API_KEY_INVALID", "Enter a valid OpenRouter API key.")
        encrypted = self._protect(self._MAGIC + key.encode("utf-8"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=".openrouter-key-", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encrypted)
                Path(temporary_name).replace(self.path)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        except OSError:
            raise SecretStoreError(
                "SECRET_STORE_WRITE_FAILED", "The API key could not be stored securely."
            ) from None

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise SecretStoreError(
                "SECRET_STORE_DELETE_FAILED", "The protected API key could not be removed."
            ) from None

    @staticmethod
    def _blob_type() -> type[ctypes.Structure]:
        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        return DataBlob

    @classmethod
    def _to_blob(cls, value: bytes) -> ctypes.Structure:
        blob_type = cls._blob_type()
        buffer = ctypes.create_string_buffer(value)
        blob = blob_type(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        blob._buffer = buffer
        return blob

    @classmethod
    def _from_blob(cls, blob: ctypes.Structure) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    @classmethod
    def _protect(cls, value: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStoreError(
                "SECRET_STORE_UNAVAILABLE", "Protected API key storage requires Windows DPAPI."
            )
        blob_type = cls._blob_type()
        source = cls._to_blob(value)
        target = blob_type()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not crypt32.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
        ):
            raise SecretStoreError(
                "SECRET_STORE_ENCRYPT_FAILED", "Windows could not protect the API key."
            )
        try:
            return cls._from_blob(target)
        finally:
            kernel32.LocalFree(target.pbData)

    @classmethod
    def _unprotect(cls, value: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStoreError(
                "SECRET_STORE_UNAVAILABLE", "Protected API key storage requires Windows DPAPI."
            )
        blob_type = cls._blob_type()
        source = cls._to_blob(value)
        target = blob_type()
        description = ctypes.c_wchar_p()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), None, None, None,
            0x1, ctypes.byref(target)
        ):
            raise SecretStoreError(
                "SECRET_STORE_DECRYPT_FAILED", "Windows could not unlock the protected API key."
            )
        try:
            return cls._from_blob(target)
        finally:
            kernel32.LocalFree(target.pbData)
            if description:
                kernel32.LocalFree(description)
