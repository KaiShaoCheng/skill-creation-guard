"""Filesystem-level audit for Hermes skill writes.

This module complements the pre_tool_call guard. Tool hooks only see writes made
through Hermes tools; this scanner records any SKILL.md file that appears,
changes, or disappears under configured skill roots.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATE_FILE = "skill-file-audit-state.json"
LOG_FILE = "skill-file-audit.jsonl"


def default_audit_dir() -> Path:
    home = os.getenv("HERMES_HOME")
    if home:
        return Path(home) / "skills-audit"
    profile_home = Path.home() / ".hermes" / "profiles" / "kaishao-admin"
    if profile_home.exists():
        return profile_home / "skills-audit"
    return Path.home() / ".hermes" / "skills-audit"


def default_skill_roots() -> list[Path]:
    roots: list[Path] = []
    home = os.getenv("HERMES_HOME")
    if home:
        roots.append(Path(home) / "skills")
    roots.extend(
        [
            Path.home() / ".hermes" / "profiles" / "kaishao-admin" / "skills",
            Path.home() / ".hermes" / "skills",
            Path("/root/data/disk/skills"),
        ]
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if root.exists():
            unique.append(root)
    return unique


def _now_fields() -> dict[str, str]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone().replace(microsecond=0)
    return {
        "observed_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "observed_at_local": now_local.isoformat(),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _skill_identity(root: Path, skill_md: Path) -> tuple[str, str]:
    rel = skill_md.relative_to(root)
    parts = rel.parts
    # Expected: category/skill-name/SKILL.md. Top-level skills are also allowed.
    if len(parts) >= 3:
        return parts[-2], parts[0]
    return skill_md.parent.name, ""


def _snapshot(roots: Iterable[Path]) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for skill_md in root.rglob("SKILL.md"):
            if not skill_md.is_file():
                continue
            st = skill_md.stat()
            skill_name, category = _skill_identity(root, skill_md)
            key = str(skill_md)
            snap[key] = {
                "path": key,
                "root": str(root),
                "target_type": "skill",
                "target_name": skill_name,
                "skill_name": skill_name,
                "category": category,
                "sha256": _sha256(skill_md),
                "size": st.st_size,
                "mtime_epoch": st.st_mtime,
                "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
    return snap


def _load_state(audit_dir: Path) -> dict[str, dict[str, Any]]:
    path = audit_dir / STATE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data["files"]
    except Exception:
        return {}
    return {}


def _write_state(audit_dir: Path, files: dict[str, dict[str, Any]]) -> None:
    path = audit_dir / STATE_FILE
    payload = {"schema_version": 1, "updated_at": _now_fields()["observed_at_utc"], "files": files}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _append_events(audit_dir: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path = audit_dir / LOG_FILE
    with path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _event(event_type: str, current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(current or previous or {})
    event = {
        "event_type": event_type,
        **_now_fields(),
        "target_type": "skill",
        "target_name": base.get("target_name"),
        "skill_name": base.get("skill_name"),
        "category": base.get("category"),
        "path": base.get("path"),
        "root": base.get("root"),
        "sha256": base.get("sha256"),
        "previous_sha256": previous.get("sha256") if previous else None,
        "size": base.get("size"),
        "previous_size": previous.get("size") if previous else None,
        "mtime_utc": base.get("mtime_utc"),
    }
    return event


def scan_skill_files(
    skill_roots: Iterable[Path] | None = None,
    audit_dir: Path | None = None,
    *,
    baseline_only: bool = False,
) -> dict[str, int]:
    """Scan skill roots and append JSONL events for created/modified/deleted skills.

    First run records existing files as `skill_file_created` unless baseline_only=True.
    Subsequent runs compare sha256 hashes and paths against the saved state.
    """
    audit_dir = Path(audit_dir) if audit_dir is not None else default_audit_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    roots = list(skill_roots) if skill_roots is not None else default_skill_roots()
    previous = _load_state(audit_dir)
    current = _snapshot(roots)

    events: list[dict[str, Any]] = []
    if not previous and baseline_only:
        _write_state(audit_dir, current)
        return {"created": 0, "modified": 0, "deleted": 0, "scanned": len(current)}

    for path, info in sorted(current.items()):
        old = previous.get(path)
        if old is None:
            events.append(_event("skill_file_created", info, None))
        elif old.get("sha256") != info.get("sha256"):
            events.append(_event("skill_file_modified", info, old))

    for path, old in sorted(previous.items()):
        if path not in current:
            events.append(_event("skill_file_deleted", None, old))

    _append_events(audit_dir, events)
    _write_state(audit_dir, current)
    counts = {"created": 0, "modified": 0, "deleted": 0, "scanned": len(current)}
    for event in events:
        if event["event_type"] == "skill_file_created":
            counts["created"] += 1
        elif event["event_type"] == "skill_file_modified":
            counts["modified"] += 1
        elif event["event_type"] == "skill_file_deleted":
            counts["deleted"] += 1
    return counts


if __name__ == "__main__":
    print(json.dumps(scan_skill_files(), ensure_ascii=False, indent=2, sort_keys=True))
