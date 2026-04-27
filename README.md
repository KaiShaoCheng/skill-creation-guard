# skill-creation-guard

Hermes plugin that guards `skill_manage` calls before they execute. It blocks low-value or risky skill creation, allows ordinary reusable skills, and records an audit log for all skill mutations it sees.

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
- Writes JSONL audit events to `~/.hermes/skills-audit/skill-creation-guard.jsonl` by default.

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

## Audit log example

```json
{"ts":"2026-04-27T03:00:00Z","tool":"skill_manage","action":"create","skill_name":"example","content_sha256":"...","decision":{"allowed":false,"verdict":"block","score":2.5,"reasons":["temporary_or_progress","score_below_threshold"],"signals":["valid_frontmatter"]}}
```

## Development

```bash
python -m pytest -q
```

## Limitations

- This is a heuristic policy guard, not a semantic LLM reviewer.
- It does not de-duplicate against all installed skills yet; it only evaluates the proposed content.
- It intentionally fails closed for suspicious/dangerous patterns but is otherwise conservative and explainable.
