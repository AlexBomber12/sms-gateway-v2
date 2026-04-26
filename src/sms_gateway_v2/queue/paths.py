from __future__ import annotations

import os
from pathlib import Path

from sms_gateway_v2.queue.exceptions import QueueCorrupted
from sms_gateway_v2.queue.models import QueueItem

QUEUE_SUBDIRS = ("pending", "processing", "sent", "failed", "tmp")


def ensure_state_dirs(state_dir: Path) -> dict[str, Path]:
    dirs = {subdir: state_dir / subdir for subdir in QUEUE_SUBDIRS}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def atomic_write_json(item: QueueItem, dirs: dict[str, Path]) -> Path:
    tmp_path = dirs["tmp"] / f"{item.id}.json"
    final_path = dirs["pending"] / f"{item.id}.json"
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(item.to_json())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)
    fsync_dir(final_path.parent)
    return final_path


def atomic_move(item_id: str, source_dir: Path, dest_dir: Path) -> Path:
    source_path = source_dir / f"{item_id}.json"
    dest_path = dest_dir / f"{item_id}.json"
    os.replace(source_path, dest_path)
    fsync_dir(dest_dir)
    return dest_path


def load_item(path: Path) -> QueueItem:
    try:
        return QueueItem.from_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise QueueCorrupted(f"failed to read queue item {path}: {exc}") from exc


def save_item(item: QueueItem, current_dir: Path) -> None:
    final_path = current_dir / f"{item.id}.json"
    tmp_path = current_dir / f".{item.id}.json.tmp"
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(item.to_json())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)
    fsync_dir(current_dir)


def list_items_sorted(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"))


def fsync_dir(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return

    fd = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
