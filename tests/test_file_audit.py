import json
from pathlib import Path

from skill_file_audit import scan_skill_files


def write_skill(root: Path, category: str, name: str, body: str = "# Test\n") -> Path:
    path = root / category / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: test\n---\n\n{body}", encoding="utf-8")
    return path


def read_events(audit_dir: Path):
    path = audit_dir / "skill-file-audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_records_new_skill_file_with_target_and_time(tmp_path):
    skills_root = tmp_path / "skills"
    audit_dir = tmp_path / "audit"
    write_skill(skills_root, "productivity", "new-skill")

    result = scan_skill_files([skills_root], audit_dir=audit_dir)

    assert result["created"] == 1
    events = read_events(audit_dir)
    assert events[-1]["event_type"] == "skill_file_created"
    assert events[-1]["target_type"] == "skill"
    assert events[-1]["target_name"] == "new-skill"
    assert events[-1]["skill_name"] == "new-skill"
    assert events[-1]["category"] == "productivity"
    assert events[-1]["observed_at_utc"].endswith("Z")
    assert events[-1]["path"].endswith("productivity/new-skill/SKILL.md")
    assert events[-1]["sha256"]


def test_records_modified_skill_file_once_state_changes(tmp_path):
    skills_root = tmp_path / "skills"
    audit_dir = tmp_path / "audit"
    skill = write_skill(skills_root, "research", "mod-skill", "# v1\n")
    scan_skill_files([skills_root], audit_dir=audit_dir)

    skill.write_text(skill.read_text(encoding="utf-8") + "\n# v2\n", encoding="utf-8")
    result = scan_skill_files([skills_root], audit_dir=audit_dir)

    assert result["modified"] == 1
    events = read_events(audit_dir)
    assert events[-1]["event_type"] == "skill_file_modified"
    assert events[-1]["target_name"] == "mod-skill"
    assert events[-1]["previous_sha256"]
    assert events[-1]["sha256"] != events[-1]["previous_sha256"]


def test_records_deleted_skill_file(tmp_path):
    skills_root = tmp_path / "skills"
    audit_dir = tmp_path / "audit"
    skill = write_skill(skills_root, "github", "deleted-skill")
    scan_skill_files([skills_root], audit_dir=audit_dir)

    skill.unlink()
    result = scan_skill_files([skills_root], audit_dir=audit_dir)

    assert result["deleted"] == 1
    events = read_events(audit_dir)
    assert events[-1]["event_type"] == "skill_file_deleted"
    assert events[-1]["target_name"] == "deleted-skill"
    assert events[-1]["previous_sha256"]
