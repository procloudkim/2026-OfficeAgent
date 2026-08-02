"""Fail-closed publication primitives for Retrobot-managed files."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


class RetrobotPublishError(RuntimeError):
    """A sanitized Retrobot publication failure."""


def install_no_replace(*, source: Path, destination: Path, owner_link: Path) -> None:
    """Copy to a private owner inode, then hard-link it into a new public path."""

    source_descriptor: int | None = None
    owner_descriptor: int | None = None
    owner_identity: tuple[int, int] | None = None
    destination_installed = False
    try:
        source_descriptor = _open_regular(source, os.O_RDONLY)
        source_before = os.fstat(source_descriptor)
        _validate_new_path(destination)
        _validate_new_path(owner_link)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        owner_descriptor = os.open(owner_link, flags, 0o600)
        owner_stat = os.fstat(owner_descriptor)
        owner_identity = owner_stat.st_dev, owner_stat.st_ino
        owner_path_stat = owner_link.lstat()
        if (
            not stat.S_ISREG(owner_stat.st_mode)
            or not stat.S_ISREG(owner_path_stat.st_mode)
            or owner_identity != (owner_path_stat.st_dev, owner_path_stat.st_ino)
        ):
            raise RetrobotPublishError("Retrobot ownership inode is invalid")

        _copy_descriptor(source_descriptor, owner_descriptor)
        os.fchmod(owner_descriptor, 0o644)
        os.fsync(owner_descriptor)
        source_after = os.fstat(source_descriptor)
        if _file_version(source_before) != _file_version(source_after):
            raise RetrobotPublishError("Retrobot source changed during publication")

        os.close(owner_descriptor)
        owner_descriptor = None
        os.close(source_descriptor)
        source_descriptor = None
        os.link(owner_link, destination, follow_symlinks=False)
        destination_installed = True
        if not _same_regular_file(owner_link, destination):
            raise RetrobotPublishError("Retrobot ownership witness is invalid")
    except (OSError, RetrobotPublishError) as error:
        rollback_complete = _rollback_private_owner(
            owner_link, owner_identity, destination_installed
        )
        if not rollback_complete:
            raise RetrobotPublishError(
                "Retrobot publication is preserved for recovery"
            ) from None
        if isinstance(error, RetrobotPublishError):
            raise
        raise RetrobotPublishError("Retrobot no-clobber publication failed") from None
    finally:
        _close_quietly(owner_descriptor)
        _close_quietly(source_descriptor)


def append_if_matches(
    *,
    destination: Path,
    expected: Path,
    replacement: Path,
    owner_link: Path,
) -> None:
    """Append the exact replacement suffix while preserving the destination inode."""

    expected_payload = _read_regular_snapshot(expected)
    replacement_payload = _read_regular_snapshot(replacement)
    if not replacement_payload.startswith(expected_payload):
        raise RetrobotPublishError("Retrobot replacement does not extend its preimage")
    suffix = replacement_payload[len(expected_payload) :]
    if not suffix:
        raise RetrobotPublishError("Retrobot append suffix is empty")

    descriptor: int | None = None
    owner_installed = False
    try:
        descriptor = _open_regular(destination, os.O_RDWR | os.O_APPEND)
        destination_stat = os.fstat(descriptor)
        destination_identity = destination_stat.st_dev, destination_stat.st_ino
        _validate_new_path(owner_link)
        os.link(destination, owner_link, follow_symlinks=False)
        owner_installed = True
        owner_stat = owner_link.lstat()
        public_stat = destination.lstat()
        if (
            not stat.S_ISREG(owner_stat.st_mode)
            or not stat.S_ISREG(public_stat.st_mode)
            or destination_identity != (owner_stat.st_dev, owner_stat.st_ino)
            or destination_identity != (public_stat.st_dev, public_stat.st_ino)
        ):
            raise RetrobotPublishError("Retrobot timeline ownership changed")
        if _read_descriptor(descriptor) != expected_payload:
            raise RetrobotPublishError("Retrobot timeline preimage changed")

        _write_all(descriptor, suffix)
        os.fsync(descriptor)
        if _read_descriptor(descriptor) != replacement_payload:
            raise RetrobotPublishError("Retrobot timeline changed during append")
        if not _same_regular_file(owner_link, destination):
            raise RetrobotPublishError("Retrobot timeline path changed during append")
    except (OSError, RetrobotPublishError) as error:
        if owner_installed:
            raise RetrobotPublishError(
                "Retrobot timeline inode is preserved for recovery"
            ) from None
        if isinstance(error, RetrobotPublishError):
            raise
        raise RetrobotPublishError("Retrobot guarded append failed") from None
    finally:
        _close_quietly(descriptor)


def replace_owned(*, source: Path, destination: Path, owner_link: Path) -> None:
    """Replace bytes only when destination still matches its ownership witness."""

    payload = _read_regular_snapshot(source)
    destination_descriptor: int | None = None
    owner_descriptor: int | None = None
    try:
        destination_descriptor = _open_regular(destination, os.O_WRONLY)
        owner_descriptor = _open_regular(owner_link, os.O_RDONLY)
        destination_stat = os.fstat(destination_descriptor)
        owner_stat = os.fstat(owner_descriptor)
        if (destination_stat.st_dev, destination_stat.st_ino) != (
            owner_stat.st_dev,
            owner_stat.st_ino,
        ):
            raise RetrobotPublishError("Retrobot owned destination changed")

        os.ftruncate(destination_descriptor, 0)
        os.lseek(destination_descriptor, 0, os.SEEK_SET)
        _write_all(destination_descriptor, payload)
        os.fchmod(destination_descriptor, 0o644)
        os.fsync(destination_descriptor)
        if not _same_regular_file(owner_link, destination):
            raise RetrobotPublishError("Retrobot owned destination path changed")
    except (OSError, RetrobotPublishError) as error:
        if isinstance(error, RetrobotPublishError):
            raise
        raise RetrobotPublishError("Retrobot owned replacement failed") from None
    finally:
        _close_quietly(owner_descriptor)
        _close_quietly(destination_descriptor)


def _open_regular(path: Path, flags: int) -> int:
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    opened_stat = os.fstat(descriptor)
    if not stat.S_ISREG(opened_stat.st_mode):
        os.close(descriptor)
        raise RetrobotPublishError("Retrobot source is not a regular file")
    return descriptor


def _read_regular_snapshot(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = _open_regular(path, os.O_RDONLY)
        before = os.fstat(descriptor)
        payload = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        if _file_version(before) != _file_version(after):
            raise RetrobotPublishError("Retrobot source changed while reading")
        return payload
    except OSError as error:
        raise RetrobotPublishError("Retrobot source read failed") from error
    finally:
        _close_quietly(descriptor)


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while chunk := os.read(descriptor, 64 * 1024):
        total += len(chunk)
        if total > MAX_ARTIFACT_BYTES:
            raise RetrobotPublishError("Retrobot artifact exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _copy_descriptor(source_descriptor: int, destination_descriptor: int) -> None:
    os.lseek(source_descriptor, 0, os.SEEK_SET)
    total = 0
    while chunk := os.read(source_descriptor, 64 * 1024):
        total += len(chunk)
        if total > MAX_ARTIFACT_BYTES:
            raise RetrobotPublishError("Retrobot artifact exceeds size limit")
        _write_all(destination_descriptor, chunk)


def _validate_new_path(path: Path) -> None:
    parent_stat = path.parent.lstat()
    if not stat.S_ISDIR(parent_stat.st_mode) or path.parent.is_symlink():
        raise RetrobotPublishError("Retrobot path parent is invalid")
    if _path_exists(path):
        raise RetrobotPublishError("Retrobot destination already exists")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise RetrobotPublishError("Retrobot artifact write did not progress")
        remaining = remaining[written:]


def _file_version(file_stat: os.stat_result) -> tuple[int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _same_regular_file(left: Path, right: Path) -> bool:
    try:
        left_stat = left.lstat()
        right_stat = right.lstat()
        return (
            stat.S_ISREG(left_stat.st_mode)
            and stat.S_ISREG(right_stat.st_mode)
            and (left_stat.st_dev, left_stat.st_ino)
            == (right_stat.st_dev, right_stat.st_ino)
        )
    except OSError:
        return False


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _rollback_private_owner(
    owner_link: Path,
    owner_identity: tuple[int, int] | None,
    destination_installed: bool,
) -> bool:
    owner_is_owned = False
    if owner_identity is not None:
        try:
            owner_stat = owner_link.lstat()
            owner_is_owned = (
                stat.S_ISREG(owner_stat.st_mode)
                and (owner_stat.st_dev, owner_stat.st_ino) == owner_identity
            )
        except OSError:
            pass

    if destination_installed:
        return False
    if owner_is_owned:
        try:
            owner_link.unlink()
        except OSError:
            return False
    return True


def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--source", required=True, type=Path)
    install.add_argument("--destination", required=True, type=Path)
    install.add_argument("--owner-link", required=True, type=Path)

    append = subparsers.add_parser("append")
    append.add_argument("--destination", required=True, type=Path)
    append.add_argument("--expected", required=True, type=Path)
    append.add_argument("--replacement", required=True, type=Path)
    append.add_argument("--owner-link", required=True, type=Path)

    replace = subparsers.add_parser("replace-owned")
    replace.add_argument("--source", required=True, type=Path)
    replace.add_argument("--destination", required=True, type=Path)
    replace.add_argument("--owner-link", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.operation == "install":
            install_no_replace(
                source=args.source,
                destination=args.destination,
                owner_link=args.owner_link,
            )
        elif args.operation == "append":
            append_if_matches(
                destination=args.destination,
                expected=args.expected,
                replacement=args.replacement,
                owner_link=args.owner_link,
            )
        else:
            replace_owned(
                source=args.source,
                destination=args.destination,
                owner_link=args.owner_link,
            )
    except RetrobotPublishError as error:
        print(f"Retrobot publisher rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
