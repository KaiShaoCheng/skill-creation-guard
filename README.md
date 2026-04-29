# skill-creation-guard

> A Hermes Agent plugin for governing skill creation, auditing every `SKILL.md` write, and explaining where each skill change came from.

`skill-creation-guard` adds a two-layer governance system for Hermes skills:

1. **Tool-layer guard** — intercepts `skill_manage` before it runs and blocks low-value or risky skill creation.
2. **Filesystem-layer audit** — watches and scans skill directories so skill writes are still recorded even when they bypass `skill_manage`.
3. **Origin enrichment** — correlates tool calls, filesystem events, and Hermes hub metadata to label each change as runtime creation, runtime mutation, install sync, or external write.

This repository is public and intended as a reusable reference implementation for Hermes skill-sprawl control.

---

## English

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

## 中文

### 这个插件解决什么问题

Hermes 可以在完成复杂任务后主动把经验沉淀成 skill。这个机制很有价值，但如果没有治理，也容易产生：

- 一次性任务进度记录；
- 过窄的项目过程文档；
- 与已有 skill 重复的技能；
- 没有验证步骤的脆弱流程；
- 包含危险命令或泄露敏感信息风险的 skill。

`skill-creation-guard` 的目标不是关闭 Hermes 的自学习能力，而是在保留自学习价值的同时，增加 **拦截、审计、追踪来源** 三层能力。

它主要回答四个问题：

1. **agent 是否尝试通过 `skill_manage` 创建或修改 skill？**
2. **这次调用是被允许还是被拦截？原因是什么？**
3. **文件系统里实际有哪些 `SKILL.md` 被创建、修改或删除？**
4. **这次写入来自运行时工具调用、安装/同步，还是外部文件写入？**

### 核心能力

#### 1. 拦截 `skill_manage`

插件注册 Hermes hooks：

- `pre_llm_call`
- `pre_tool_call`
- `post_tool_call`
- `on_session_start`

真正执行拦截的是：

```text
pre_tool_call
```

它只关注 Hermes 的：

```text
skill_manage
```

以及以下动作：

```text
create
edit
patch
write_file
remove_file
delete
```

其中 `action=create` 会走最严格的价值评分。如果评分过低，会在真正写入 skill 之前被 block。

`edit`、`patch`、`write_file`、`remove_file`、`delete` 会走更轻量的安全检查，并记录 allowed 审计事件。

#### 2. 对 skill 创建做价值评分

创建评分是确定性的 heuristic，不依赖 LLM 复审，便于解释和排查。

加分项包括：

- 有合法 YAML frontmatter；
- 有明确触发条件 / When to use；
- 有可复用的操作步骤；
- 有注意事项 / Pitfalls；
- 有验证 / Verification；
- 明确体现未来复用价值；
- skill 名称合法；
- 用户明确要求创建 skill。

扣分或拦截项包括：

- 只是当前任务进度；
- 只是 session outcome；
- 缺少操作步骤；
- 缺少触发条件；
- 缺少复用信号；
- 出现危险命令模式。

危险模式示例：

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

#### 3. 工具层审计日志

工具层日志写入：

```text
$HERMES_HOME/skills-audit/skill-creation-guard.jsonl
```

如果没有设置 `HERMES_HOME`，当前部署会 fallback 到：

```text
~/.hermes/profiles/kaishao-admin/skills-audit/skill-creation-guard.jsonl
```

被拦截事件示例：

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

放行事件也会记录，例如：

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

#### 4. 文件层审计：追踪任何 `SKILL.md` 写入

工具 hook 只能看到通过 Hermes tool 发生的写入。实际环境里，skill 也可能来自：

- 安装；
- 同步；
- 复制覆盖；
- 外部进程写入；
- 手工文件操作。

所以插件额外提供文件层扫描器，记录 skill root 下所有 `SKILL.md` 的创建、修改和删除。

语义文件事件写入：

```text
$HERMES_HOME/skills-audit/skill-file-audit.jsonl
```

扫描状态写入：

```text
$HERMES_HOME/skills-audit/skill-file-audit-state.json
```

每条文件层事件包含：

- `event_type`：`skill_file_created` / `skill_file_modified` / `skill_file_deleted`；
- `observed_at_utc`；
- `observed_at_local`；
- `target_type`；
- `target_name` / `skill_name`；
- `category`；
- `path`；
- `root`；
- `sha256`；
- `previous_sha256`；
- `size`；
- `previous_size`；
- `mtime_utc`。

#### 5. 近实时 watcher + 每分钟兜底扫描

仓库里包含可选 systemd user units：

```text
systemd/hermes-skill-file-watch.service
systemd/hermes-skill-file-audit-scan.service
systemd/hermes-skill-file-audit-scan.timer
```

watcher 会把原始文件事件写入：

```text
$HERMES_HOME/skills-audit/skill-file-watch.jsonl
```

常见原始事件包括：

```text
CREATE
CLOSE_WRITE,CLOSE
MOVED_TO
DELETE
```

timer 每分钟跑一次语义扫描，用于兜底 watcher 可能漏掉的场景。

#### 6. 来源归因：判断这次 skill 变更从哪里来

来源归因日志写入：

```text
$HERMES_HOME/skills-audit/skill-origin-audit.jsonl
```

`origin` 字段含义：

| origin | 含义 |
| --- | --- |
| `runtime-create` | Hermes 会话中通过 `skill_manage(action=create)` 创建。 |
| `runtime-mutation` | 通过 `skill_manage(edit/patch)` 修改。 |
| `runtime-delete` | 通过 `skill_manage(delete)` 删除。 |
| `runtime-support-mutation` | 通过 `skill_manage(write_file)` 修改 supporting file。 |
| `runtime-support-delete` | 通过 `skill_manage(remove_file)` 删除 supporting file。 |
| `install-sync` | 命中 Hermes hub lock metadata，大概率来自安装或同步。 |
| `external-write` | 文件系统观测到写入，但没有工具层或 hub metadata 证据。 |

`origin_source` 字段含义：

| origin_source | 含义 |
| --- | --- |
| `tool-hook` | 直接由 Hermes plugin hook 捕获。 |
| `hub-lockfile` | 根据 Hermes hub lock metadata 推断。 |
| `filesystem-infer` | 根据实时文件事件推断。 |
| `backfill-infer` | 对历史 file audit 事件做 backfill 时推断。 |

### 安装方式

复制插件到 Hermes plugin 目录并启用：

```bash
mkdir -p ~/.hermes/plugins
cp -a skill-creation-guard ~/.hermes/plugins/skill-creation-guard
hermes plugins enable skill-creation-guard
```

如果使用 Hermes profile，则放到：

```text
~/.hermes/profiles/<profile-name>/plugins/skill-creation-guard
```

同时建议打开 Hermes 自带的 agent-created skill 安全扫描：

```yaml
skills:
  guard_agent_created: true
```

当前配置路径可以用：

```bash
hermes config path
```

### 可选 systemd 部署

安装 user-level systemd units：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-skill-file-watch.service ~/.config/systemd/user/
cp systemd/hermes-skill-file-audit-scan.service ~/.config/systemd/user/
cp systemd/hermes-skill-file-audit-scan.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-skill-file-watch.service
systemctl --user enable --now hermes-skill-file-audit-scan.timer
```

检查状态：

```bash
systemctl --user status hermes-skill-file-watch.service
systemctl --user list-timers hermes-skill-file-audit-scan.timer
```

如果在 root 的非交互环境里遇到：

```text
Failed to connect to bus: No medium found
```

可设置：

```bash
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
```

### 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SKILL_CREATION_GUARD_THRESHOLD` | `6.5` | agent 自主创建 skill 的最低分。 |
| `SKILL_CREATION_GUARD_DIRECT_THRESHOLD` | `4.5` | 用户明确要求创建/保存 skill 时的较低阈值。 |
| `SKILL_FILE_AUDIT_INTERVAL_SECONDS` | `60` | hook 触发文件扫描的节流间隔。 |
| `HERMES_HOME` | 未设置 | 如果设置，审计日志写到 `$HERMES_HOME/skills-audit/`。 |

### 常用命令

手动跑文件语义扫描：

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_file_audit.py
```

对已有文件审计事件补充来源归因：

```bash
python ~/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard/skill_origin_audit.py
```

开发验证：

```bash
pytest -q
ruff check .
python -m py_compile __init__.py guard.py skill_file_audit.py skill_file_watch.py skill_origin_audit.py skill_origin_tools.py
```

### 当前限制

- 评分策略是 deterministic heuristic，不是 LLM 语义评审。
- 插件优先保证可解释、可复现、便于审计。
- `external-write` 不一定代表有风险，只代表当前没有 tool 或 hub metadata 可以解释来源。
- 文件事件和工具事件的关联是 best-effort，需要依赖时间窗口与目标 skill 名称。
- 暂未做完整的跨所有已安装 skill 的语义去重。

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
