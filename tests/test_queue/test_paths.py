from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import sms_gateway_v2.queue.paths as queue_paths
from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import QueueItem
from sms_gateway_v2.queue.exceptions import QueueCorrupted
from sms_gateway_v2.queue.paths import (
    QUEUE_SUBDIRS,
    atomic_move,
    atomic_write_json,
    ensure_state_dirs,
    fsync_dir,
    list_items_sorted,
    load_item,
    save_item,
)


def make_item(sample_sms: IncomingSms, item_id: str) -> QueueItem:
    return QueueItem(
        id=item_id,
        sms=sample_sms,
        first_seen_at=datetime(2026, 4, 26, 10, 41, 33, tzinfo=UTC),
    )


def test_ensure_state_dirs_creates_all_subdirs(state_dir: Path) -> None:
    dirs = ensure_state_dirs(state_dir)

    assert set(dirs) == set(QUEUE_SUBDIRS)
    assert all(path.is_dir() for path in dirs.values())


def test_atomic_write_json_writes_via_tmp_then_renames(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")

    final_path = atomic_write_json(item, dirs)

    assert final_path == dirs["pending"] / f"{item.id}.json"
    assert final_path.read_text(encoding="utf-8") == item.to_json()
    assert not (dirs["tmp"] / f"{item.id}.json").exists()


def test_atomic_write_json_fsyncs_tmp_and_pending_directories(
    state_dir: Path,
    sample_sms: IncomingSms,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    synced_dirs: list[Path] = []
    monkeypatch.setattr(queue_paths, "fsync_dir", synced_dirs.append)

    final_path = queue_paths.atomic_write_json(item, dirs)

    assert final_path.exists()
    assert synced_dirs == [dirs["tmp"], dirs["pending"]]


def test_atomic_move_moves_between_dirs(state_dir: Path, sample_sms: IncomingSms) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    source_path = atomic_write_json(item, dirs)

    dest_path = atomic_move(item.id, dirs["pending"], dirs["processing"])

    assert dest_path == dirs["processing"] / f"{item.id}.json"
    assert dest_path.exists()
    assert not source_path.exists()


def test_atomic_move_fsyncs_source_and_destination_directories(
    state_dir: Path,
    sample_sms: IncomingSms,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    source_path = atomic_write_json(item, dirs)
    synced_dirs: list[Path] = []
    monkeypatch.setattr(queue_paths, "fsync_dir", synced_dirs.append)

    dest_path = queue_paths.atomic_move(item.id, dirs["pending"], dirs["processing"])

    assert dest_path.exists()
    assert not source_path.exists()
    assert synced_dirs == [dirs["pending"], dirs["processing"]]


def test_atomic_move_raises_file_not_found_for_missing_source(state_dir: Path) -> None:
    dirs = ensure_state_dirs(state_dir)

    with pytest.raises(FileNotFoundError):
        atomic_move("missing", dirs["pending"], dirs["processing"])


def test_load_item_reads_and_parses(state_dir: Path, sample_sms: IncomingSms) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    path = atomic_write_json(item, dirs)

    assert load_item(path) == item


def test_load_item_on_invalid_json_raises_queue_corrupted(state_dir: Path) -> None:
    dirs = ensure_state_dirs(state_dir)
    path = dirs["pending"] / "bad.json"
    path.write_text("{bad-json", encoding="utf-8")

    with pytest.raises(QueueCorrupted, match="invalid queue item JSON"):
        load_item(path)


def test_load_item_wraps_read_errors(state_dir: Path) -> None:
    dirs = ensure_state_dirs(state_dir)

    with pytest.raises(QueueCorrupted, match=str(dirs["pending"])):
        load_item(dirs["pending"])


def test_save_item_writes_atomically_in_current_dir(
    state_dir: Path,
    sample_sms: IncomingSms,
) -> None:
    dirs = ensure_state_dirs(state_dir)
    item = make_item(sample_sms, "1714149693000-0123456789abcdef0123456789abcdef")
    atomic_write_json(item, dirs)
    atomic_move(item.id, dirs["pending"], dirs["processing"])
    updated = item.model_copy(update={"attempts": 2})

    save_item(updated, dirs["processing"])

    assert load_item(dirs["processing"] / f"{item.id}.json") == updated
    assert not (dirs["processing"] / f".{item.id}.json.tmp").exists()


def test_list_items_sorted_returns_json_paths_sorted_by_name(state_dir: Path) -> None:
    dirs = ensure_state_dirs(state_dir)
    second = dirs["pending"] / "1714149693001-b.json"
    first = dirs["pending"] / "1714149693000-a.json"
    ignored = dirs["pending"] / "1714149692999-c.txt"
    second.write_text("{}", encoding="utf-8")
    first.write_text("{}", encoding="utf-8")
    ignored.write_text("{}", encoding="utf-8")

    assert list_items_sorted(dirs["pending"]) == [first, second]


def test_fsync_dir_returns_when_directory_flag_is_unavailable(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_state_dirs(state_dir)
    monkeypatch.delattr("os.O_DIRECTORY")

    fsync_dir(state_dir)
