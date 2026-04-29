# skill-creation-guard

> A Hermes Agent plugin for governing skill creation, auditing every `SKILL.md` write, and explaining where each skill change came from.

**中文文档:** [README.zh-CN.md](README.zh-CN.md)

`skill-creation-guard` adds a two-layer governance system for Hermes skills:

1. **Tool-layer guard** — intercepts `skill_manage` before it runs and blocks low-value or risky skill creation.
2. **Filesystem-layer audit** — watches and scans skill directories so skill writes are still recorded even when they bypass `skill_manage`.
3. **Origin enrichment** — correlates tool calls, filesystem events, and Hermes hub metadata to label each change as runtime creation, runtime mutation, install sync, or external write.

This repository is public and intended as a reusable reference implementation for Hermes skill-sprawl control.

---

### Why this exists

Hermes Agent can improve itself by saving reusable procedures as skills. That is powerful, but without guardrails an agent may also create:

- one-off task progress notes;
- narrow project-specific artifacts;
- duplicated skills;
- fragile workflows without verification steps;
- unsafe skills that run suspicious commands or expose secrets.

This plugin keeps the useful self-learning behavior while adding visibility and policy enforcement.

It answers four operational questions:

1. **Did an agent attempt to create or change a skill through `skill_manage`?**
2. **Was that tool call allowed or blocked, and why?**
3. **Which `SKILL.md` files were actually created, modified, or deleted?**
4. **Did the write come from runtime tool use, hub installation/sync, or an external file write?**

### What it does

#### 1. Intercepts `skill_manage`

The plugin registers Hermes hooks:

- `pre_llm_call`
- `pre_tool_call`
- `post_tool_call`
- `on_session_start`

The main enforcement hook is `pre_tool_call`. It watches only this Hermes tool:

```text
skill_manage
```

and these actions:

```text
create
edit
patch
write_file
remove_file
delete
```

For `action=create`, the plugin scores whether the proposed skill is durable and reusable. Low-scoring creation attempts are blocked before Hermes writes the skill.

For mutation actions such as `edit`, `patch`, `write_file`, `remove_file`, and `delete`, the plugin applies a lighter safety-focused gate and records allowed calls.

#### 2. Scores skill creation quality

The create policy is heuristic and explainable. It rewards signals such as:

- valid YAML frontmatter;
- clear trigger conditions / “when to use” section;
- procedural steps;
- pitfalls or known issues;
- verification / validation steps;
- explicit reuse language;
- valid skill name;
- explicit user request to create a skill.

It penalizes or blocks:

- temporary task progress;
- session outcome summaries;
- missing procedural steps;
- missing trigger conditions;
- weak or missing reuse signal;
- dangerous command patterns.

Examples of dangerous patterns include:

```text
curl ... | bash
wget ... | sh
rm -rf /
~/.ssh
~/.hermes/.env
eval(...)
exec(...)
base64 -d
```

#### 3. Records tool-layer audit events

Tool-layer events are written to:

```text
$HERMES_HOME/skills-audit/skill-creation-guard.jsonl
```

If `HERMES_HOME` is not set, this deployment falls back to:

```text
~/.hermes/profiles/kaishao-admin/skills-audit/skill-creation-guard.jsonl
```

Example blocked event:

```json
{
  "event_type": "skill_manage_blocked",
  "blocked": true,
  "blocked_at_utc": "2026-04-27T03:00:00Z",
  "blocked_at_local": "2026-04-27T11:00:00+08:00",
  "tool": "skill_manage",
  "action": "create",
  "target_type": "skill",
  "target_name": "task-progress",
  "skill_name": "task-progress",
  "session_id": "20260427_example",
  "content_sha256": "...",
  "decision": {
    "allowed": false,
    "verdict": "block",
    "score": 0.0,
    "threshold": 6.5,
    "reasons": ["temporary_or_progress", "score_below_threshold"],
    "signals": []
  }
}
```

Allowed calls are also logged:

```json
{
  "event_type": "skill_manage_allowed",
  "blocked": false,
  "tool": "skill_manage",
  "action": "patch",
  "target_type": "skill",
  "target_name": "hermes-gateway-troubleshooting",
  "decision": {
    "allowed": true,
    "verdict": "allow",
    "score": 10.0,
    "signals": ["patch_safety_pass"]
  }
}
```

#### 4. Audits filesystem-level skill writes

Tool hooks only see writes that go through Hermes tools. Skills can also appear through installation, synchronization, direct file copy, or other external processes.

To close that gap, this plugin includes a filesystem scanner that records every observed `SKILL.md` create/modify/delete event under configured Hermes skill roots.

Semantic file events are written to:

```text
$HERMES_HOME/skills-audit/skill-file-audit.jsonl
```

Scanner state is stored at:

```text
$HERMES_HOME/skills-audit/skill-file-audit-state.json
```

Each file-level event includes:

- `event_type`: `skill_file_created`, `skill_file_modified`, or `skill_file_deleted`;
- `observed_at_utc`;
- `observed_at_local`;
- `target_type`;
- `target_name` / `skill_name`;
- `category`;
- `path`;
- `root`;
- `sha256`;
- `previous_sha256`;
- `size`;
- `previous_size`;
- `mtime_utc`.

#### 5. Watches raw file events in near real time

The repository includes optional systemd user units:

```text
systemd/hermes-skill-file-watch.service
systemd/hermes-skill-file-audit-scan.service
systemd/hermes-skill-file-audit-scan.timer
```

The watcher records raw filesystem events to:

```text
$HERMES_HOME/skills-audit/skill-file-watch.jsonl
```

Typical raw events include:

```text
CREATE
CLOSE_WRITE,CLOSE
MOVED_TO
DELETE
```

The once-per-minute scan timer acts as a fallback for events that the watcher may miss.

#### 6. Enriches every skill write with origin metadata

Origin-enriched events are written to:

```text
$HERMES_HOME/skills-audit/skill-origin-audit.jsonl
```

Origin labels:

| Origin | Meaning |
| --- | --- |
| `runtime-create` | Created through `skill_manage(action=create)` during a Hermes session. |
| `runtime-mutation` | Modified through `skill_manage(edit/patch)`. |
| `runtime-delete` | Deleted through `skill_manage(delete)`. |
| `runtime-support-mutation` | Supporting file changed through `skill_manage(write_file)`. |
| `runtime-support-delete` | Supporting file removed through `skill_manage(remove_file)`. |
| `install-sync` | Matched Hermes hub lock metadata; likely installed or synchronized from a hub/source. |
| `external-write` | A filesystem write was observed but no tool or hub metadata explains it. |

Origin source labels:

| Origin source | Meaning |
| --- | --- |
| `tool-hook` | Directly observed by Hermes plugin tool hooks. |
| `hub-lockfile` | Derived from Hermes hub lock metadata. |
| `filesystem-infer` | Inferred from a live file event without tool metadata. |
| `backfill-infer` | Inferred while backfilling old file audit events. |

### Installation

Copy the plugin directory into the Hermes profile plugin directory and enable it:

```bash
mkdir -p ~/.hermes/plugins
cp -a skill-creation-guard ~/.hermes/plugins/skill-creation-guard
hermes plugins enable skill-creation-guard
```

For a named profile, copy it into:

```text
~/.hermes/profiles/<profile-name>/plugins/skill-creation-guard
```

Then enable it for that profile.

Also enable Hermes' built-in agent-created skill scanner:

```yaml
skills:
  guard_agent_created: true
```

You can find the active config path with:

```bash
hermes config path
```

### Optional systemd deployment

Install the systemd user unit files:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-skill-file-watch.service ~/.config/systemd/user/
cp systemd/hermes-skill-file-audit-scan.service ~/.config/systemd/user/
cp systemd/hermes-skill-file-audit-scan.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-skill-file-watch.service
systemctl --user enable --now hermes-skill-file-audit-scan.timer
```

Check status:

```bash
systemctl --user status hermes-skill-file-watch.service
systemctl --user list-timers hermes-skill-file-audit-scan.timer
```

If you run this as `root` in a non-interactive environment and `systemctl --user` cannot connect to the user bus, set:

```bash
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
```

### Configuration

Environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `SKILL_CREATION_GUARD_THRESHOLD` | `6.5` | Minimum score for autonomous skill creation. |
| `SKILL_CREATION_GUARD_DIRECT_THRESHOLD` | `4.5` | Lower threshold when the user explicitly asks to create/save a skill. |
| `SKILL_FILE_AUDIT_INTERVAL_SECONDS` | `60` | Throttle interval for hook-triggered filesystem scans. |
| `HERMES_HOME` | unset | If set, audit logs are written under `$HERMES_HOME/skills-audit/`. |

### Manual commands

Run a semantic file scan:

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_file_audit.py
```

Backfill origin metadata for existing file audit events:

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_origin_audit.py
```

Run tests:

```bash
pytest -q
ruff check .
python -m py_compile __init__.py guard.py skill_file_audit.py skill_file_watch.py skill_origin_audit.py skill_origin_tools.py
```

### Current limitations

- The scoring policy is heuristic, not an LLM reviewer.
- The plugin intentionally focuses on explainable deterministic signals.
- `external-write` does not always mean suspicious; it means no tool or hub metadata was available.
- Origin correlation is best-effort when a file event and a tool event happen close together.
- It does not yet perform full semantic deduplication against all existing skills.

---

---

## Repository status

Current plugin version:

```text
0.3.1
```

Primary logs:

```text
skill-creation-guard.jsonl
skill-file-watch.jsonl
skill-file-audit.jsonl
skill-origin-audit.jsonl
```

Recommended operating mode:

```text
Hermes pre_tool_call guard + filesystem watcher + periodic scanner + origin enrichment
```

License: MIT
