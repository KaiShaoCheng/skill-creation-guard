"""Hermes plugin entry point for skill-creation-guard."""

from __future__ import annotations

import importlib
import os
import time
from pathlib import Path
from typing import Any, Dict


def _load_local_module(name: str):
    package = __package__ or __name__
    if package and package != "__main__":
        try:
            return importlib.import_module(f"{package}.{name}")
        except Exception:
            pass
    module_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        name,
        module_dir / f"{name}.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load local plugin module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_guard = _load_local_module("guard")
_origin_audit = _load_local_module("skill_origin_audit")
_origin_tools = _load_local_module("skill_origin_tools")

pre_tool_call_guard = _guard.pre_tool_call_guard
remember_turn = _guard.remember_turn
enrich_file_event = _origin_audit.enrich_file_event
read_lock_entry_for_skill = _origin_tools.read_lock_entry_for_skill

_LAST_FILE_SCAN = 0.0
_LAST_SKILL_MANAGE: Dict[str, Any] = {}


def _remember_turn_hook(session_id: str = "", user_message: str = "", **kwargs):
    remember_turn(session_id, user_message, **kwargs)
    return None


def _pre_tool_call_hook(tool_name: str = "", args: dict | None = None, **kwargs):
    if tool_name == "skill_manage":
        _record_skill_manage_context(tool_name=tool_name, args=args or {}, hook="pre_tool_call", **kwargs)
    return pre_tool_call_guard(tool_name=tool_name, args=args or {}, **kwargs)


def _on_post_skill_manage_hook(tool_name: str = "", args: dict | None = None, result: Any = None, session_id: str = "", task_id: str = "", **kwargs):
    if tool_name != "skill_manage":
        return None
    _record_skill_manage_context(tool_name=tool_name, args=args or {}, hook="post_tool_call", result=result, session_id=session_id, task_id=task_id, **kwargs)
    try:
        args = args or {}
        action = str(args.get("action") or "").lower()
        target_name = args.get("name")
        audit_dir = _file_audit_dir()
        if target_name:
            now = _now_fields()
            enrich_file_event(
                audit_dir=audit_dir,
                event={
                    "event_type": "skill_manage_tool_call",
                    "observed_at_utc": now["observed_at_utc"],
                    "observed_at_local": now["observed_at_local"],
                    "target_type": "skill",
                    "target_name": target_name,
                    "skill_name": target_name,
                    "category": args.get("category") or "",
                    "path": "",
                },
                tool_name=tool_name,
                action=action,
                session_id=session_id,
                lock_entry=read_lock_entry_for_skill(target_name),
            )
    except Exception:
        pass
    return None


def _record_skill_manage_context(tool_name: str, args: Dict[str, Any], hook: str, **kwargs):
    global _LAST_SKILL_MANAGE
    _LAST_SKILL_MANAGE = {
        "ts": time.monotonic(),
        "tool_name": tool_name,
        "action": str(args.get("action") or "").lower(),
        "target_name": args.get("name"),
        "category": args.get("category"),
        "session_id": kwargs.get("session_id"),
        "task_id": kwargs.get("task_id"),
        "hook": hook,
    }


def _file_audit_dir() -> Path:
    home = os.getenv("HERMES_HOME")
    if home:
        return Path(home) / "skills-audit"
    profile_home = Path.home() / ".hermes" / "profiles" / "kaishao-admin"
    if profile_home.exists():
        return profile_home / "skills-audit"
    return Path.home() / ".hermes" / "skills-audit"


def _now_fields() -> Dict[str, str]:
    from datetime import datetime, timezone

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone().replace(microsecond=0)
    return {
        "observed_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "observed_at_local": now_local.isoformat(),
    }


def _enrich_recent_skill_file_events() -> None:
    """Best-effort origin enrichment for events discovered by the file scanner."""
    global _LAST_FILE_SCAN
    interval = float(os.getenv("SKILL_FILE_AUDIT_INTERVAL_SECONDS", "60"))
    now = time.monotonic()
    if now - _LAST_FILE_SCAN < interval:
        return None
    _LAST_FILE_SCAN = now

    audit_dir = _file_audit_dir()
    origin_path = audit_dir / "skill-origin-audit.jsonl"
    file_path = audit_dir / "skill-file-audit.jsonl"
    if not file_path.exists():
        return None

    seen: set[tuple[str, str, str, str]] = set()
    if origin_path.exists():
        for line in origin_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = __import__("json").loads(line)
            seen.add((row.get("event_type", ""), row.get("observed_at_utc", ""), row.get("target_name", ""), row.get("path", "")))

    # Use recent tool context only when it is fresh.
    ctx = _LAST_SKILL_MANAGE if (time.monotonic() - _LAST_SKILL_MANAGE.get("ts", 0.0)) < interval else {}

    appended = 0
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = __import__("json").loads(line)
        key = (event.get("event_type", ""), event.get("observed_at_utc", ""), event.get("target_name", ""), event.get("path", ""))
        if key in seen:
            continue
        if appended >= 50:
            break

        target_name = event.get("target_name")
        if ctx.get("target_name") == target_name and ctx.get("action"):
            origin_tool = ctx.get("tool_name", "")
            origin_action = ctx.get("action", "")
            origin_session = ctx.get("session_id", "")
        else:
            origin_tool = ""
            origin_action = ""
            origin_session = ""

        enrich_file_event(
            audit_dir=audit_dir,
            event=event,
            tool_name=origin_tool,
            action=origin_action,
            session_id=origin_session,
            lock_entry=read_lock_entry_for_skill(target_name) if target_name else None,
        )
        seen.add(key)
        appended += 1
    return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _remember_turn_hook)
    ctx.register_hook("pre_tool_call", _pre_tool_call_hook)
    ctx.register_hook("post_tool_call", _on_post_skill_manage_hook)
    ctx.register_hook("post_tool_call", _enrich_recent_skill_file_events)
    ctx.register_hook("on_session_start", _enrich_recent_skill_file_events)
