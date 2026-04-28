from skill_file_watch import make_watch_event


def test_make_watch_event_records_raw_filesystem_write():
    event = make_watch_event("CLOSE_WRITE,CLOSE", "/tmp/skills/productivity/demo/SKILL.md")

    assert event["event_type"] == "skill_file_write_observed"
    assert event["fs_event"] == "CLOSE_WRITE,CLOSE"
    assert event["target_type"] == "skill"
    assert event["target_name"] == "demo"
    assert event["skill_name"] == "demo"
    assert event["path"] == "/tmp/skills/productivity/demo/SKILL.md"
    assert event["observed_at_utc"].endswith("Z")
