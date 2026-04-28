"""Hermes plugin entry point for skill-creation-guard."""

from __future__ import annotations

import os
import time

try:
    from .guard import pre_tool_call_guard, remember_turn
    from .skill_file_audit import scan_skill_files
except ImportError:  # Allows pytest to import this plugin directory as a test package root.
    from guard import pre_tool_call_guard, remember_turn
    from skill_file_audit import scan_skill_files

_LAST_FILE_SCAN = 0.0


def _remember_turn_hook(session_id: str = "", user_message: str = "", **kwargs):
    remember_turn(session_id, user_message, **kwargs)
    return None


def _pre_tool_call_hook(tool_name: str = "", args: dict | None = None, **kwargs):
    return pre_tool_call_guard(tool_name=tool_name, args=args or {}, **kwargs)


def _scan_skill_files_hook(**_kwargs):
    """Best-effort file audit hook.

    It is intentionally fail-open: audit failures must not break Hermes turns.
    A cron job should also run the same scanner so writes made outside active
    Hermes turns are still observed.
    """
    global _LAST_FILE_SCAN
    interval = float(os.getenv("SKILL_FILE_AUDIT_INTERVAL_SECONDS", "60"))
    now = time.monotonic()
    if now - _LAST_FILE_SCAN < interval:
        return None
    _LAST_FILE_SCAN = now
    try:
        scan_skill_files()
    except Exception:
        return None
    return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _remember_turn_hook)
    ctx.register_hook("pre_tool_call", _pre_tool_call_hook)
    ctx.register_hook("post_tool_call", _scan_skill_files_hook)
    ctx.register_hook("on_session_start", _scan_skill_files_hook)
