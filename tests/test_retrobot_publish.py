"""No-clobber and ownership contracts for Retrobot worktree publication."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.retrobot_publish as publisher
from scripts.retrobot_publish import (
    RetrobotPublishError,
    append_if_matches,
    install_no_replace,
    replace_owned,
)


def test_install_no_replace_copies_content_and_creates_owner_link(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination_directory = tmp_path / "destination"
    transaction_directory = destination_directory / "transaction"
    destination_directory.mkdir()
    transaction_directory.mkdir()
    source.write_bytes(b"validated retrospective\n")
    destination = destination_directory / "retro.md"
    owner_link = transaction_directory / "retro.owner"

    install_no_replace(
        source=source,
        destination=destination,
        owner_link=owner_link,
    )

    assert destination.read_bytes() == source.read_bytes()
    assert destination.samefile(owner_link)


def test_install_no_replace_never_overwrites_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    destination.write_bytes(b"user-owned")

    with pytest.raises(RetrobotPublishError):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert destination.read_bytes() == b"user-owned"
    assert not owner_link.exists()


def test_install_no_replace_rejects_symlink_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    target.write_bytes(b"user-owned")
    destination.symlink_to(target)

    with pytest.raises(RetrobotPublishError):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert target.read_bytes() == b"user-owned"
    assert destination.is_symlink()


def test_owner_link_failure_removes_only_the_created_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError("simulated cross-device owner failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(RetrobotPublishError):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert not destination.exists()
    assert not owner_link.exists()


def test_failed_public_validation_preserves_artifact_and_owner_witness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    same_file_calls = 0
    original_samefile = Path.samefile

    def fail_initial_validation(left: Path, right: Path) -> bool:
        nonlocal same_file_calls
        same_file_calls += 1
        if same_file_calls == 1:
            return False
        return original_samefile(left, right)

    monkeypatch.setattr(
        "scripts.retrobot_publish._same_regular_file",
        fail_initial_validation,
    )
    with pytest.raises(RetrobotPublishError, match="preserved for recovery"):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert destination.is_file()
    assert owner_link.samefile(destination)


def test_failed_public_validation_never_unlinks_a_replaced_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    original_same_file = Path.samefile
    public_unlink_attempted = False

    def replace_before_validation(left: Path, right: Path) -> bool:
        destination.unlink()
        destination.write_bytes(b"user-replacement")
        return original_same_file(left, right)

    original_unlink = Path.unlink

    def observe_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal public_unlink_attempted
        if path == destination and path.read_bytes() == b"user-replacement":
            public_unlink_attempted = True
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        "scripts.retrobot_publish._same_regular_file",
        replace_before_validation,
    )
    monkeypatch.setattr(Path, "unlink", observe_unlink)

    with pytest.raises(RetrobotPublishError, match="preserved for recovery"):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert destination.read_bytes() == b"user-replacement"
    assert owner_link.read_bytes() == b"generated"
    assert public_unlink_attempted is False


def test_append_if_matches_extends_the_owned_timeline_inode(tmp_path: Path) -> None:
    destination = tmp_path / "timeline"
    expected = tmp_path / "expected"
    replacement = tmp_path / "replacement"
    owner_link = tmp_path / "timeline.owner"
    expected.write_bytes(b"header\n")
    destination.write_bytes(expected.read_bytes())
    replacement.write_bytes(b"header\nnew row\n")

    append_if_matches(
        destination=destination,
        expected=expected,
        replacement=replacement,
        owner_link=owner_link,
    )

    assert destination.read_bytes() == replacement.read_bytes()
    assert destination.samefile(owner_link)


def test_append_if_matches_preserves_a_concurrent_public_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "timeline"
    expected = tmp_path / "expected"
    replacement = tmp_path / "replacement"
    owner_link = tmp_path / "timeline.owner"
    expected.write_bytes(b"header\n")
    destination.write_bytes(expected.read_bytes())
    replacement.write_bytes(b"header\nnew row\n")
    original_read = publisher._read_descriptor
    read_calls = 0

    def replace_public_path(descriptor: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        payload = original_read(descriptor)
        if read_calls == 3:
            destination.unlink()
            destination.write_bytes(b"user-replacement")
        return payload

    monkeypatch.setattr(
        "scripts.retrobot_publish._read_descriptor",
        replace_public_path,
    )
    with pytest.raises(RetrobotPublishError, match="preserved for recovery"):
        append_if_matches(
            destination=destination,
            expected=expected,
            replacement=replacement,
            owner_link=owner_link,
        )

    assert destination.read_bytes() == b"user-replacement"
    assert owner_link.read_bytes() == replacement.read_bytes()


def test_replace_owned_rewrites_only_the_witnessed_inode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"new index")
    destination.write_bytes(b"old index")
    owner_link.hardlink_to(destination)

    replace_owned(source=source, destination=destination, owner_link=owner_link)

    assert destination.read_bytes() == b"new index"
    assert owner_link.samefile(destination)


def test_owner_path_replacement_is_never_adopted_for_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    original_open = os.open

    def replace_after_owner_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        descriptor = original_open(path, flags, mode)
        if Path(path) == owner_link and flags & os.O_CREAT:
            owner_link.unlink()
            owner_link.write_bytes(b"user-replacement")
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_owner_open)
    with pytest.raises(RetrobotPublishError, match="ownership inode is invalid"):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert owner_link.read_bytes() == b"user-replacement"
    assert not destination.exists()


def test_owner_fstat_failure_does_not_claim_the_private_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    original_fstat = os.fstat
    fstat_calls = 0

    def fail_owner_fstat(descriptor: int) -> os.stat_result:
        nonlocal fstat_calls
        fstat_calls += 1
        if fstat_calls == 3:
            raise OSError("simulated owner fstat failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", fail_owner_fstat)
    with pytest.raises(RetrobotPublishError):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert not destination.exists()
    assert owner_link.exists()


def test_descriptor_close_failure_rolls_back_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    owner_link = tmp_path / "owner"
    source.write_bytes(b"generated")
    original_close = os.close
    close_calls = 0

    def fail_first_close(descriptor: int) -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise OSError("simulated close failure")
        original_close(descriptor)

    monkeypatch.setattr(os, "close", fail_first_close)
    with pytest.raises(RetrobotPublishError):
        install_no_replace(
            source=source,
            destination=destination,
            owner_link=owner_link,
        )

    assert not destination.exists()
    assert not owner_link.exists()
