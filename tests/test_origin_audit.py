import json
from pathlib import Path

from skill_origin_audit import enrich_file_event, default_origin_log_path, classify_origin


def write_event(audit_dir: Path, event_type: str, target_name: str, path: str, ts: str):
    p = audit_dir / "skill-file-audit.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event_type": event_type,
            "observed_at_utc": ts,
            "observed_at_local": ts,
            "target_type": "skill",
            "target_name": target_name,
            "skill_name": target_name,
            "path": path,
        }, sort_keys=True, ensure_ascii=False) + "\n")


def test_classifies_runtime_create_from_skill_manage_post_hook():
    assert classify_origin(
        event_type="skill_file_created",
        tool_name="skill_manage",
        action="create",
        session_id="sess-1",
        lock_entry=None,
    ) == "runtime-create"


def test_classifies_install_sync_from_hub_lock_entry():
    assert classify_origin(
        event_type="skill_file_created",
        tool_name="",
        action="",
        session_id="",
        lock_entry={"source": "skills-sh", "trust_level": "community", "installed_at": "2026-04-28T09:00:00Z"},
    ) == "install-sync"


def test_classifies_external_write_when_no_hook_and_no_lock():
    assert classify_origin(
        event_type="skill_file_modified",
        tool_name="",
        action="",
        session_id="",
        lock_entry=None,
    ) == "external-write"


def test_enriches_existing_file_audit_events(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    write_event(audit_dir, "skill_file_created", "new-skill", "/tmp/skills/research/new-skill/SKILL.md", "2026-04-28T10:00:00Z")
    write_event(audit_dir, "skill_file_modified", "existing-skill", "/tmp/skills/productivity/existing-skill/SKILL.md", "2026-04-28T10:05:00Z")

    result = enrich_file_event(
        audit_dir=audit_dir,
        event={
            "event_type": "skill_file_created",
            "target_name": "new-skill",
            "path": "/tmp/skills/research/new-skill/SKILL.md",
            "observed_at_utc": "2026-04-28T10:00:00Z",
            "observed_at_local": "2026-04-28T18:00:00+08:00",
        },
        tool_name="skill_manage",
        action="create",
        session_id="sess-9",
        lock_entry=None,
    )

    assert result["origin"] == "runtime-create"
    assert result["tool_name"] == "skill_manage"
    assert result["session_id"] == "sess-9"

    origin_path = default_origin_log_path(audit_dir)
    assert origin_path.exists()
    rows = [json.loads(line) for line in origin_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["event_type"] == "skill_file_created"
    assert rows[-1]["origin"] == "runtime-create"
    assert rows[-1]["target_name"] == "new-skill"
    assert rows[-1]["observed_at_utc"] == "2026-04-28T10:00:00Z"
