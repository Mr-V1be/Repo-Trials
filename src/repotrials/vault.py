"""Private, content-addressed storage for gold patches and hidden tests.

The vault uses SHA-256 addressing and restrictive filesystem permissions.  It
does not claim to provide encryption; callers that need encryption at rest can
place the vault on an encrypted volume or wrap exported objects with age/KMS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .state_paths import (
    StatePathError,
    absolute_path,
    ensure_managed_directory,
    ensure_root_directory,
    managed_path,
    prepare_managed_file,
    validate_directory_creation,
    validate_root_directory,
)

_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


class VaultError(RuntimeError):
    pass


class VaultObjectNotFound(VaultError, FileNotFoundError):
    pass


class VaultIntegrityError(VaultError):
    pass


@dataclass(frozen=True, slots=True)
class VaultReference:
    digest: str
    size: int
    media_type: str = "application/octet-stream"
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        digest = _digest_hex(self.digest)
        object.__setattr__(self, "digest", digest)
        if self.size < 0:
            raise ValueError("size cannot be negative")
        if self.algorithm != "sha256":
            raise ValueError("only sha256 is supported")

    @property
    def uri(self) -> str:
        return f"sha256:{self.digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "digest": self.digest,
            "uri": self.uri,
            "size": self.size,
            "media_type": self.media_type,
        }


def _digest_hex(reference: str | VaultReference) -> str:
    value = reference.digest if isinstance(reference, VaultReference) else reference
    match = _SHA256_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid sha256 reference: {value!r}")
    return match.group(1).lower()


def _chmod_private(path: Path, mode: int) -> None:
    with suppress(OSError):
        path.chmod(mode)


class ContentAddressedVault:
    """Store immutable private artifacts under their SHA-256 digest."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = absolute_path(root)
        try:
            root_exists = validate_root_directory(self.root)
            if root_exists:
                managed_path(self.root, "objects", expected="directory")
                managed_path(self.root, "objects/sha256", expected="directory")
                managed_path(self.root, "tmp", expected="directory")
            else:
                validate_directory_creation(self.root)
            ensure_root_directory(self.root)
            self.objects_dir = ensure_managed_directory(self.root, "objects/sha256")
            self.temp_dir = ensure_managed_directory(self.root, "tmp")
        except StatePathError as exc:
            raise VaultError(f"unsafe vault path: {exc}") from exc
        _chmod_private(self.root, 0o700)
        _chmod_private(self.objects_dir.parent, 0o700)
        _chmod_private(self.objects_dir, 0o700)
        _chmod_private(self.temp_dir, 0o700)

    def object_path(self, reference: str | VaultReference) -> Path:
        digest = _digest_hex(reference)
        try:
            return managed_path(
                self.objects_dir,
                Path(digest[:2]) / digest[2:],
                expected="file",
            )
        except StatePathError as exc:
            raise VaultError(f"unsafe vault object path for {digest}: {exc}") from exc

    def put_bytes(
        self, data: bytes, *, media_type: str = "application/octet-stream"
    ) -> VaultReference:
        digest = hashlib.sha256(data).hexdigest()
        reference = VaultReference(digest, len(data), media_type)
        destination = self.object_path(digest)
        if destination.exists():
            self.get_bytes(reference)
            return reference
        try:
            destination = prepare_managed_file(self.objects_dir, Path(digest[:2]) / digest[2:])
            temp_dir = ensure_managed_directory(self.root, "tmp")
        except StatePathError as exc:
            raise VaultError(f"unsafe vault object path for {digest}: {exc}") from exc
        _chmod_private(destination.parent, 0o700)
        fd, temporary_name = tempfile.mkstemp(dir=temp_dir, prefix="object-")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _chmod_private(temporary, 0o600)
            if destination.exists():
                temporary.unlink(missing_ok=True)
                self.get_bytes(reference)
            else:
                os.replace(temporary, destination)
                _chmod_private(destination, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def put_text(
        self,
        text: str,
        *,
        encoding: str = "utf-8",
        media_type: str = "text/plain; charset=utf-8",
    ) -> VaultReference:
        return self.put_bytes(text.encode(encoding), media_type=media_type)

    def put_json(self, value: Any) -> VaultReference:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(payload, media_type="application/json")

    def put_file(
        self,
        path: str | os.PathLike[str],
        *,
        media_type: str = "application/octet-stream",
    ) -> VaultReference:
        source = Path(path)
        hasher = hashlib.sha256()
        size = 0
        with source.open("rb") as reader:
            while chunk := reader.read(1024 * 1024):
                hasher.update(chunk)
                size += len(chunk)
        digest = hasher.hexdigest()
        destination = self.object_path(digest)
        try:
            temp_dir = ensure_managed_directory(self.root, "tmp")
            destination = prepare_managed_file(self.objects_dir, Path(digest[:2]) / digest[2:])
        except StatePathError as exc:
            raise VaultError(f"unsafe vault object path for {digest}: {exc}") from exc
        reference = VaultReference(digest, size, media_type)
        if destination.exists():
            self.get_bytes(reference)
            return reference

        fd, temporary_name = tempfile.mkstemp(dir=temp_dir, prefix="object-")
        temporary = Path(temporary_name)
        try:
            copied_hasher = hashlib.sha256()
            copied_size = 0
            with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    copied_hasher.update(chunk)
                    writer.write(chunk)
                    copied_size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if copied_size != size or not hmac.compare_digest(copied_hasher.hexdigest(), digest):
                raise VaultError(f"source file changed while being stored: {source}")
            _chmod_private(destination.parent, 0o700)
            _chmod_private(temporary, 0o600)
            if destination.exists():
                temporary.unlink(missing_ok=True)
                self.get_bytes(reference)
            else:
                os.replace(temporary, destination)
                _chmod_private(destination, 0o600)
            return reference
        finally:
            temporary.unlink(missing_ok=True)

    def has(self, reference: str | VaultReference) -> bool:
        return self.object_path(reference).is_file()

    def open(self, reference: str | VaultReference) -> BinaryIO:
        path = self.object_path(reference)
        if not path.is_file():
            raise VaultObjectNotFound(str(reference))
        return path.open("rb")

    def get_bytes(self, reference: str | VaultReference, *, verify: bool = True) -> bytes:
        with self.open(reference) as handle:
            data = handle.read()
        if verify:
            expected = _digest_hex(reference)
            actual = hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(expected, actual):
                raise VaultIntegrityError(f"vault object {expected} hashes to {actual}")
        return data

    def get_text(
        self,
        reference: str | VaultReference,
        *,
        encoding: str = "utf-8",
        verify: bool = True,
    ) -> str:
        return self.get_bytes(reference, verify=verify).decode(encoding)

    def get_json(self, reference: str | VaultReference, *, verify: bool = True) -> Any:
        return json.loads(self.get_text(reference, verify=verify))

    def verify(self, reference: str | VaultReference) -> bool:
        path = self.object_path(reference)
        if not path.is_file():
            return False
        expected = _digest_hex(reference)
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                hasher.update(chunk)
        return hmac.compare_digest(expected, hasher.hexdigest())

    def materialize(
        self,
        reference: str | VaultReference,
        destination: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        target = Path(destination)
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        data = self.get_bytes(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def iter_references(self) -> Iterator[VaultReference]:
        for prefix in sorted(self.objects_dir.iterdir()):
            if len(prefix.name) != 2:
                continue
            try:
                managed_path(self.objects_dir, prefix.name, expected="directory")
            except StatePathError as exc:
                raise VaultError(f"unsafe vault object prefix: {exc}") from exc
            if not prefix.is_dir():
                continue
            for item in sorted(prefix.iterdir()):
                digest = prefix.name + item.name
                if not _SHA256_RE.fullmatch(digest):
                    continue
                try:
                    managed_path(
                        self.objects_dir,
                        Path(prefix.name) / item.name,
                        expected="file",
                    )
                except StatePathError as exc:
                    raise VaultError(f"unsafe vault object: {exc}") from exc
                if item.is_file():
                    yield VaultReference(digest, item.stat().st_size)

    def verify_all(self) -> Mapping[str, bool]:
        return {reference.uri: self.verify(reference) for reference in self.iter_references()}


# Concise alias for the CLI and user-facing examples.
Vault = ContentAddressedVault


__all__ = [
    "ContentAddressedVault",
    "Vault",
    "VaultError",
    "VaultIntegrityError",
    "VaultObjectNotFound",
    "VaultReference",
]
