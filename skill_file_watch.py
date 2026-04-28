"""Inotify-based near-real-time watcher for Hermes skill file writes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill_file_audit import default_audit_dir, default_skill_roots, scan_skill_files

WATCH_LOG_FILE = "skill-file-watch.jsonl"


def _now_fields() -> dict[str, str]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone().replace(microsecond=0)
    return {
        "observed_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "observed_at_local": now_local.isoformat(),
    }


def _skill_name_from_path(path: str) -> str:
    p = Path(path)
    if p.name == "SKILL.md":
        return p.parent.name
    return p.stem or p.name


def make_watch_event(fs_event: str, path: str) -> dict[str, Any]:
    return {
        "event_type": "skill_file_write_observed",
        **_now_fields(),
        "fs_event": fs_event,
        "target_type": "skill",
        "target_name": _skill_name_from_path(path),
        "skill_name": _skill_name_from_path(path),
        "path": path,
    }


def append_watch_event(event: dict[str, Any], audit_dir: Path | None = None) -> None:
    audit_dir = Path(audit_dir) if audit_dir is not None else default_audit_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / WATCH_LOG_FILE
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def watch_forever() -> int:
    """Watch skill roots with inotifywait and run the semantic scanner on each event."""
    if shutil.which("inotifywait") is None:
        print("inotifywait not found", file=sys.stderr)
        return 127

    roots = [str(p) for p in default_skill_roots() if p.exists()]
    if not roots:
        print("no skill roots found", file=sys.stderr)
        return 0

    # Establish current baseline without flooding the log with all existing skills.
    scan_skill_files(baseline_only=True)

    cmd = [
        "inotifywait",
        "-m",
        "-r",
        "-e",
        "close_write,create,delete,move",
        "--format",
        "%e|%w%f",
        *roots,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line or "|" not in line:
            continue
        fs_event, path = line.split("|", 1)
        if not path.endswith("/SKILL.md") and not path.endswith("SKILL.md"):
            continue
        append_watch_event(make_watch_event(fs_event, path))
        scan_skill_files()
    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(watch_forever())
