# skill-creation-guard

Hermes plugin that guards `skill_manage` calls before they execute, audits filesystem-level `SKILL.md` writes, and enriches every observed skill write with creation origin metadata. It answers "runtime creation, install sync, or external write" for every skill change it records.

## Why

Hermes can proactively create skills after complex work. That is useful, but without a guard it can also create narrow one-off project notes, task progress records, or duplicated procedures. This plugin adds a conservative pre-tool-call policy layer.

## What it does

- Registers a `pre_tool_call` hook.
- Watches only `skill_manage` actions: `create`, `edit`, `patch`, `write_file`, `remove_file`, and `delete`.
- Scores `create` requests for durable procedural value:
  - valid frontmatter
  - trigger conditions /适用场景
  - reusable procedural steps
  - pitfalls /注意事项
  - verification /验证
  - explicit reuse language
  - valid skill name
- Blocks obviously temporary progress/session-outcome content.
- Blocks dangerous patterns such as `curl | bash`, `rm -rf /`, access to `~/.ssh`, `eval(...)`, etc.
- Lowers the score threshold when the user explicitly asked to create a skill, while still enforcing safety checks.
- Writes tool-level JSONL audit events to `~/.hermes/skills-audit/skill-creation-guard.jsonl` by default.
- For every blocked call, records exactly what was intercepted (`target_type`, `target_name`/`skill_name`, `action`), when it was intercepted (`blocked_at_utc`, `blocked_at_local`), and why (`decision.reasons`).
- Also audits filesystem-level `SKILL.md` writes under Hermes skill roots and records created/modified/deleted files in `skill-file-audit.jsonl`.
- Runs file auditing on `on_session_start`, throttled `post_tool_call`, and can be run periodically via `python skill_file_audit.py` for writes made outside active Hermes turns.
- Enriches observed skill writes with origin metadata in `skill-origin-audit.jsonl`, classifying them as `runtime-create`, `runtime-mutation`, `runtime-delete`, `runtime-support-mutation`, `runtime-support-delete`, `install-sync`, or `external-write`.

## Install in Hermes

Copy this directory to the user plugin directory and enable it:

```bash
mkdir -p ~/.hermes/plugins
cp -a skill-creation-guard ~/.hermes/plugins/skill-creation-guard
hermes plugins enable skill-creation-guard
```

Also enable Hermes' built-in agent-created skill scanner:

```yaml
skills:
  guard_agent_created: true
```

Exact config location can be found with:

```bash
hermes config path
```

## Configuration

Environment variables:

- `SKILL_CREATION_GUARD_THRESHOLD` — default `6.5`; threshold for autonomous skill creation.
- `SKILL_CREATION_GUARD_DIRECT_THRESHOLD` — default `4.5`; threshold when the user explicitly asks to create/save a skill.
- `HERMES_HOME` — if set, audit logs go under `$HERMES_HOME/skills-audit/`; otherwise `~/.hermes/skills-audit/`.
- `SKILL_FILE_AUDIT_INTERVAL_SECONDS` — default `60`; throttle interval for hook-triggered filesystem scans.

## Filesystem write audit

The plugin writes file-level events to:

```text
~/.hermes/skills-audit/skill-file-audit.jsonl
```

It also keeps scanner state in:

```text
~/.hermes/skills-audit/skill-file-audit-state.json
```

Each file-level record includes `event_type` (`skill_file_created`, `skill_file_modified`, or `skill_file_deleted`), `target_name`/`skill_name`, category, path, UTC/local observation time, current SHA-256, previous SHA-256 for modifications/deletions, and file size.

The plugin also writes an origin enrichment log to:

```text
~/.hermes/skills-audit/skill-origin-audit.jsonl
```

Each origin record includes the original write metadata plus `origin`, `origin_source`, `tool_name`, `action`, `session_id`, `hub_source`, `hub_trust_level`, `hub_installed_at`, and the enrichment timestamp.

Run a manual scan:

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_file_audit.py
```

Backfill origin tags for existing file-level events. Backfill will use Hermes hub lock metadata when available, so already-installed hub skills are tagged as `install-sync` instead of generic `external-write`:

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_origin_audit.py
```

For near-continuous coverage of writes made outside active Hermes turns, run the scanner periodically with cron/systemd/Hermes cron. This deployment includes two optional user-level systemd units:

- `hermes-skill-file-watch.service`: uses `inotifywait` to record raw `SKILL.md` create/write/delete/move events in near real time.
- `hermes-skill-file-audit-scan.timer`: runs a once-per-minute semantic scan as a fallback and catches writes missed by the watcher.

Install the templates from `systemd/` into `~/.config/systemd/user/`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-skill-file-watch.service
systemctl --user enable --now hermes-skill-file-audit-scan.timer
```


## Audit log example

```json
{"event_type":"skill_manage_blocked","blocked":true,"blocked_at_utc":"2026-04-27T03:00:00Z","blocked_at_local":"2026-04-27T11:00:00+08:00","tool":"skill_manage","action":"create","target_type":"skill","target_name":"example","skill_name":"example","content_sha256":"...","decision":{"allowed":false,"verdict":"block","score":2.5,"reasons":["temporary_or_progress","score_below_threshold"],"signals":["valid_frontmatter"]}}
```

Useful fields:

- `blocked`: whether this `skill_manage` call was actually blocked.
- `blocked_at_utc` / `blocked_at_local`: interception time in UTC and local machine timezone.
- `target_type`: currently always `skill` because this plugin guards `skill_manage`.
- `target_name` / `skill_name`: the intercepted skill name.
- `action`: the intercepted `skill_manage` action, such as `create`, `edit`, or `patch`.
- `decision.reasons`: explainable reasons for blocking.


## Development

```bash
python -m pytest -q
```

## Limitations

- This is a heuristic policy guard, not a semantic LLM reviewer.
- It does not de-duplicate against all installed skills yet; it only evaluates the proposed content.
- It intentionally fails closed for suspicious/dangerous patterns but is otherwise conservative and explainable.
