"""Hermes plugin entry point for skill-creation-guard."""

from __future__ import annotations

try:
    from .guard import pre_tool_call_guard, remember_turn
except ImportError:  # Allows pytest to import this plugin directory as a test package root.
    from guard import pre_tool_call_guard, remember_turn


def _remember_turn_hook(session_id: str = "", user_message: str = "", **kwargs):
    remember_turn(session_id, user_message, **kwargs)
    return None


def _pre_tool_call_hook(tool_name: str = "", args: dict | None = None, **kwargs):
    return pre_tool_call_guard(tool_name=tool_name, args=args or {}, **kwargs)


def register(ctx):
    ctx.register_hook("pre_llm_call", _remember_turn_hook)
    ctx.register_hook("pre_tool_call", _pre_tool_call_hook)
