"""Origin-enrichment layer for Hermes skill write auditing.

This module answers the question "who/what created or changed this skill?" by
combining three complementary evidence sources:

1. Hermes tool hooks (runtime creation / mutation).
2. Hermes hub lock metadata (install-synced skills).
3. Filesystem-level skill write events (catch-all for external writes).

It does not replace the existing audit loggers; it produces an additional
structured log that tags every observed write with a stable origin label.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

ORIGIN_LOG_FILE = "skill-origin-audit.jsonl"
DEFAULT_AUDIT_DIR_FALLBACK = Path.home() / ".hermes" / "profiles" / "kaishao-admin" / "skills-audit"

RUNTIME_TOOL_ORIGINS = {
    "runtime-create",
    "runtime-mutation",
    "runtime-delete",
    "runtime-support-mutation",
    "runtime-support-delete",
}


def default_origin_log_path(audit_dir: Path | None = None) -> Path:
    base = audit_dir or DEFAULT_AUDIT_DIR_FALLBACK
    return base / ORIGIN_LOG_FILE


def classify_origin(
    event_type: str,
    tool_name: str,
    action: str,
    session_id: str,
    lock_entry: Dict[str, Any] | None,
) -> str:
    """Return a short origin label for a skill write event."""
    is_runtime_tool = bool(tool_name) and bool(session_id)
    is_create_event = event_type in {"skill_file_created"} or action == "create"
    is_support_path = action in {"write_file", "remove_file"}

    if is_runtime_tool:
        if is_create_event and action in {"create"}:
            return "runtime-create"
        if action in {"delete"}:
            return "runtime-delete"
        if is_support_path:
            if action == "remove_file":
                return "runtime-support-delete"
            return "runtime-support-mutation"
        return "runtime-mutation"

    if lock_entry:
        return "install-sync"

    return "external-write"


def _now_fields() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone().replace(microsecond=0)
    return {
        "observed_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "observed_at_local": now_local.isoformat(),
    }


def enrich_file_event(
    audit_dir: Path,
    event: Dict[str, Any],
    tool_name: str,
    action: str,
    session_id: str,
    lock_entry: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Attach origin metadata to a file-level skill event and persist it."""
    now = _now_fields()
    origin = classify_origin(
        event_type=event.get("event_type", ""),
        tool_name=tool_name,
        action=action,
        session_id=session_id,
        lock_entry=lock_entry,
    )
    observed_at_utc = event.get("observed_at_utc") or now["observed_at_utc"]
    observed_at_local = event.get("observed_at_local") or event.get("blocked_at_local") or now["observed_at_local"]
    enriched = {
        **event,
        "observed_at_utc": observed_at_utc,
        "observed_at_local": observed_at_local,
        "audit_origin_version": 1,
        "origin": origin,
        "tool_name": tool_name or None,
        "action": action or None,
        "session_id": session_id or None,
        "origin_source": "hub-lockfile" if lock_entry and not tool_name else "tool-hook" if tool_name else "filesystem-infer",
        "hub_source": lock_entry.get("source") if lock_entry else None,
        "hub_trust_level": lock_entry.get("trust_level") if lock_entry else None,
        "hub_installed_at": lock_entry.get("installed_at") if lock_entry else None,
        "recorded_at_utc": now["observed_at_utc"],
        "recorded_at_local": now["observed_at_local"],
    }
    write_origin_events(audit_dir, [enriched])
    return enriched


def write_origin_events(audit_dir: Path, events: list[Dict[str, Any]]) -> None:
    if not events:
        return
    path = default_origin_log_path(audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def load_origin_events(audit_dir: Path) -> list[Dict[str, Any]]:
    path = default_origin_log_path(audit_dir)
    if not path.exists():
        return []
    rows: list[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _default_lock_resolver(skill_name: str) -> Dict[str, Any] | None:
    try:
        from skill_origin_tools import read_lock_entry_for_skill

        return read_lock_entry_for_skill(skill_name)
    except Exception:
        return None


def backfill_origin_events(
    audit_dir: Path,
    default_origin: str = "external-write",
    lock_resolver: Callable[[str], Dict[str, Any] | None] | None = None,
) -> int:
    """Write origin events for any file audit events that are not yet covered."""
    source_path = audit_dir / "skill-file-audit.jsonl"
    if not source_path.exists():
        return 0

    lock_resolver = lock_resolver or _default_lock_resolver
    seen: set[tuple[str, str, str, str]] = set()
    for row in load_origin_events(audit_dir):
        key = (
            row.get("event_type", ""),
            row.get("observed_at_utc", ""),
            row.get("target_name", ""),
            row.get("path", ""),
        )
        seen.add(key)

    appended = 0
    with source_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            key = (
                event.get("event_type", ""),
                event.get("observed_at_utc", ""),
                event.get("target_name", ""),
                event.get("path", ""),
            )
            if key in seen:
                continue
            target_name = str(event.get("target_name") or event.get("skill_name") or "")
            lock_entry = lock_resolver(target_name) if target_name else None
            origin = classify_origin(
                event_type=event.get("event_type", ""),
                tool_name="",
                action="",
                session_id="",
                lock_entry=lock_entry,
            )
            if not lock_entry and default_origin != "external-write":
                origin = default_origin
            now = _now_fields()
            enriched = {
                **event,
                "audit_origin_version": 1,
                "origin": origin,
                "tool_name": None,
                "action": None,
                "session_id": None,
                "origin_source": "hub-lockfile" if lock_entry else "backfill-infer",
                "hub_source": lock_entry.get("source") if lock_entry else None,
                "hub_trust_level": lock_entry.get("trust_level") if lock_entry else None,
                "hub_installed_at": lock_entry.get("installed_at") if lock_entry else None,
                "recorded_at_utc": now["observed_at_utc"],
                "recorded_at_local": now["observed_at_local"],
            }
            write_origin_events(audit_dir, [enriched])
            appended += 1
    return appended


if __name__ == "__main__":
    print(json.dumps({"backfilled": backfill_origin_events(DEFAULT_AUDIT_DIR_FALLBACK)}, ensure_ascii=False))
