"""Core policy engine for the Hermes skill-creation-guard plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_RECENT_USER_MESSAGES: dict[str, str] = {}

DEFAULT_CREATE_THRESHOLD = float(os.getenv("SKILL_CREATION_GUARD_THRESHOLD", "6.5"))
DEFAULT_DIRECT_REQUEST_THRESHOLD = float(os.getenv("SKILL_CREATION_GUARD_DIRECT_THRESHOLD", "4.5"))

TEMPORARY_PATTERNS = [
    r"本次任务",
    r"当前任务",
    r"当前进度",
    r"任务进度",
    r"已完成",
    r"今天完成",
    r"下一步继续",
    r"\bsession outcome\b",
    r"\bcompleted work\b",
    r"\btask progress\b",
]

DANGEROUS_PATTERNS = [
    r"curl\s+[^\n|;]+\|\s*(bash|sh)",
    r"wget\s+[^\n|;]+\|\s*(bash|sh)",
    r"rm\s+-rf\s+(/|\$HOME|~)",
    r"~/.hermes/.env",
    r"~/.ssh",
    r"os\.environ",
    r"eval\s*\(",
    r"exec\s*\(",
    r"base64\s+-d",
]

DIRECT_REQUEST_RE = re.compile(
    r"(创建|保存|新增|写一个|做一个).{0,12}(skill|技能)|"
    r"(create|save|add|write).{0,16}(skill)",
    re.IGNORECASE,
)


@dataclass
class Decision:
    allowed: bool
    verdict: str
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    threshold: float = DEFAULT_CREATE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def remember_turn(session_id: str, user_message: str, **_: Any) -> None:
    """Remember the latest user message for policy context.

    Hermes pre_tool_call hooks do not receive the original user message directly,
    so the plugin also registers a pre_llm_call hook and stores the message by
    session id. The value is ephemeral process memory only.
    """
    if session_id:
        _RECENT_USER_MESSAGES[session_id] = user_message or ""


def _content_from_args(args: Dict[str, Any]) -> str:
    return str(args.get("content") or args.get("file_content") or "")


def _contains_any(patterns: Iterable[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE | re.MULTILINE)]


def _has_frontmatter(content: str) -> bool:
    return content.lstrip().startswith("---") and re.search(r"\n---\s*\n", content[3:]) is not None


def _has_procedural_steps(content: str) -> bool:
    numbered = len(re.findall(r"(?m)^\s*\d+\.\s+", content)) >= 2
    bullets_with_verbs = len(re.findall(r"(?im)^\s*[-*]\s+(run|check|verify|create|write|inspect|执行|检查|验证|创建|写入)", content)) >= 2
    return numbered or bullets_with_verbs


def _has_section(content: str, names: Iterable[str]) -> bool:
    joined = "|".join(re.escape(n) for n in names)
    return re.search(rf"(?im)^\s*#{{1,3}}\s*({joined})\b", content) is not None


def evaluate_skill_create(args: Dict[str, Any], recent_user_message: str = "") -> Decision:
    """Score a proposed skill creation for durable procedural value.

    This is intentionally heuristic and conservative. Security-dangerous content
    always blocks. User-explicit creation requests lower the score threshold but
    do not bypass safety checks.
    """
    content = _content_from_args(args)
    name = str(args.get("name") or "")
    reasons: list[str] = []
    signals: list[str] = []
    score = 0.0

    if not content.strip():
        return Decision(False, "block", 0.0, ["missing_content"], [], DEFAULT_CREATE_THRESHOLD)

    dangerous = _contains_any(DANGEROUS_PATTERNS, content)
    if dangerous:
        return Decision(False, "block", 0.0, ["dangerous_pattern"], dangerous[:3], DEFAULT_CREATE_THRESHOLD)

    direct_request = bool(DIRECT_REQUEST_RE.search(recent_user_message or ""))
    threshold = DEFAULT_DIRECT_REQUEST_THRESHOLD if direct_request else DEFAULT_CREATE_THRESHOLD
    if direct_request:
        signals.append("direct_user_request")
        score += 1.0

    temporary = _contains_any(TEMPORARY_PATTERNS, content)
    if temporary:
        reasons.append("temporary_or_progress")
        score -= 3.0

    if _has_frontmatter(content):
        signals.append("valid_frontmatter")
        score += 1.0
    else:
        reasons.append("missing_frontmatter")
        score -= 1.0

    if _has_section(content, ["When to use", "Use when", "触发条件", "适用场景"]):
        signals.append("trigger_conditions")
        score += 1.5
    else:
        reasons.append("missing_trigger_conditions")

    if _has_procedural_steps(content):
        signals.append("procedural_steps")
        score += 2.0
    else:
        reasons.append("missing_procedural_steps")

    if _has_section(content, ["Pitfalls", "Known issues", "注意事项", "坑", "风险"]):
        signals.append("pitfalls")
        score += 1.0

    if _has_section(content, ["Verification", "Validate", "验证", "验收"]):
        signals.append("verification")
        score += 1.0

    if re.search(r"(?i)future session|reuse|reusable|recurring|下次|复用|长期|反复|重复", content):
        signals.append("reuse_language")
        score += 1.0
    else:
        reasons.append("missing_reuse_signal")

    if name and re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", name):
        signals.append("valid_name")
        score += 0.5
    else:
        reasons.append("weak_or_missing_name")

    score = max(0.0, min(10.0, round(score, 2)))
    allowed = score >= threshold and "temporary_or_progress" not in reasons
    verdict = "allow" if allowed else "block"
    if not allowed and "score_below_threshold" not in reasons:
        reasons.append("score_below_threshold")
    return Decision(allowed, verdict, score, reasons, signals, threshold)


def evaluate_skill_mutation(args: Dict[str, Any], action: str) -> Decision:
    """Safety-focused gate for edits/patches/supporting-file writes."""
    content = _content_from_args(args) + "\n" + str(args.get("new_string") or "")
    dangerous = _contains_any(DANGEROUS_PATTERNS, content)
    if dangerous:
        return Decision(False, "block", 0.0, ["dangerous_pattern"], dangerous[:3], DEFAULT_CREATE_THRESHOLD)
    return Decision(True, "allow", 10.0, [], [f"{action}_safety_pass"], DEFAULT_CREATE_THRESHOLD)


def _default_audit_dir() -> Path:
    home = os.getenv("HERMES_HOME")
    if home:
        return Path(home) / "skills-audit"
    return Path.home() / ".hermes" / "skills-audit"


def _write_audit(event: dict[str, Any], audit_dir: Optional[Path] = None) -> None:
    audit_dir = Path(audit_dir) if audit_dir is not None else _default_audit_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "skill-creation-guard.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _block_message(decision: Decision, action: str) -> str:
    return (
        "skill-creation-guard blocked skill_manage "
        f"action={action}: score={decision.score}/10 threshold={decision.threshold}; "
        f"reasons={', '.join(decision.reasons) or 'none'}; "
        f"signals={', '.join(decision.signals) or 'none'}. "
        "If this skill is genuinely durable and reusable, ask the user explicitly "
        "or improve it with trigger conditions, procedural steps, pitfalls, and verification."
    )


def pre_tool_call_guard(
    tool_name: str,
    args: Dict[str, Any],
    session_id: str = "",
    task_id: str = "",
    audit_dir: Optional[Path] = None,
    **_: Any,
) -> Optional[dict[str, str]]:
    """Hermes pre_tool_call hook entry point."""
    if tool_name != "skill_manage":
        return None

    action = str((args or {}).get("action") or "").lower()
    if action not in {"create", "edit", "patch", "write_file", "remove_file", "delete"}:
        return None

    recent = _RECENT_USER_MESSAGES.get(session_id, "")
    if action == "create":
        decision = evaluate_skill_create(args or {}, recent_user_message=recent)
    else:
        decision = evaluate_skill_mutation(args or {}, action)

    content = _content_from_args(args or {})
    event = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tool": tool_name,
        "action": action,
        "skill_name": (args or {}).get("name"),
        "task_id": task_id,
        "session_id": session_id,
        "content_sha256": hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest() if content else None,
        "decision": decision.to_dict(),
    }
    _write_audit(event, audit_dir=audit_dir)

    if decision.allowed:
        return None
    return {"action": "block", "message": _block_message(decision, action)}
