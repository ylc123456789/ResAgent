# 会话与项目模型 — 开发文档

**日期**: 2026-08-10
**状态**: M1（三子模块原语）+ M2（ResAgent 集成）已实施并验收（2026-08-11）；M3（RESEARCH.md）未开始

**实施备注**（验收时发现，已修复或转达）:
- ResAgent 侧新增 `session_cards.py`（卡片读取，ResAgent 自读 yaml 不 import 子模块）；adapter mock 均会写 mock 卡片，保证 mock 全链路可测
- reproagent 手写 YAML 在多行摘要时回读截断（仅影响展示，建议后续摘要单行化）
- reproagent `test_run_one_writes_full_logs...` 存在时序 flaky（单独跑通过）
- ExpAgent 仓库存在未提交的 EOL 归一化 diff（2217/2217），建议提交或丢弃
**关联文档**: [CONVERSATION_LAYER_DESIGN.md](./CONVERSATION_LAYER_DESIGN.md)（对话层）、[HANDOVER_CONVERSATION_LAYER.md](./HANDOVER_CONVERSATION_LAYER.md)
**涉及模块与分工**:

| 模块 | 改动量 | 负责方 |
|------|--------|--------|
| reproagent | 中（参考实现） | 按 §8.1 任务书 |
| CodingAgent | 中小 | 按 §8.2 任务书 |
| ExpAgent | 小 | 按 §8.3 任务书 |
| ResAgent | 中（集成） | 按 §8.4，M1 完成后开工 |

---

## 1. 背景与目标

系统已跑通，但四个模块普遍缺乏"会话"和"项目"的一等概念：每次执行都有物理现场（workspace/state.json/logs），却没有**身份**（稳定可引用的 ID）、**可发现**（list/status）、**可恢复**（resume）。用户无法在三天后说"继续上次那个复现"。

目标：

1. 每个模块**单独使用**时有自己的项目和会话概念，可 list / status / resume。
2. 大系统里存在一个**大会话**（ResAgent conversation），它通过**索引**引用各子模块的小会话，子会话的实体完全由子模块自管。
3. 模块间保持解耦：唯一跨模块契约是一个 yaml 文件格式，不互相 import 代码。
4. 环境分层：数据集/pip 缓存跨项目共享；conda env 按项目隔离。

## 2. 概念定义

```
Project（项目）= 显式的研究项目实体（opencode 式，非 Claude Code 的隐式目录式）
  大系统里 = ResearchRun（res-*），有 goal/tasks/artifacts/budget 等富状态。
  子模块里 = 一次任务的工作区（workspace_dir / output_dir）。

Session（会话）= 一次可恢复的连续工作过程
  身份 = 稳定 session_id；实体 = 事件日志/状态文件 + 工作区。
  操作 = list / status / resume。

SessionIndexCard（会话索引卡，即 manifest）= 唯一的跨模块契约
  每个会话工作区根部的 session.yaml。大会话只持有索引卡引用，
  子会话实体的存储、恢复、清理全部由子模块自己负责。
```

### 2.1 拓扑结构

```
Conversation (conv-*)  ←─ 多对多引用 ─→  Run/Project (res-*)  ←─ 1:N 拥有 ─→  子会话
  用户对话线（前台）                      项目（档案柜）                子模块自管
   - 可跨项目漫游                           - 显式实体，可枚举              - reproagent task
   - active_run_id 切换                     - user_directives 记录          - codingagent task
   - 持有子会话索引                          - 持有子会话索引                 - expagent 咨询
```

关键约束：**大会话绝不物理包含子会话的内容**（事件流不合并）。子会话可能产生几十步 LLM trace，合并会导致上下文爆炸和格式耦合。索引卡引用是唯一通道。

### 2.2 与主流实现的对应

| 系统 | 模型 | 我们借鉴什么 |
|------|------|-------------|
| opencode / codex | 显式项目实体，session 1:N 挂项目 | 项目必须是显式的——科研项目的锚点是研究目标，不是目录 |
| Claude Code | 子 agent 一次性，父会话只引用产物 | 引用而非嵌套；但我们子任务长达数十分钟，需额外加 resume |
| Claude Code / Aider | 项目级记忆文件（CLAUDE.md） | M3 的 RESEARCH.md 跨会话继承 |

## 3. 索引卡规范（唯一跨模块契约，先锁定）

每个会话工作区根部的 `session.yaml`：

```yaml
schema_version: 1
session_id: repro-20260810-a4b232-3f2a1c   # 模块前缀 + 时间戳 + 短uuid
module: reproagent                          # reproagent | codingagent | expagent
kind: task_session                          # task_session | qa_session | advisory_session
status: completed                           # running | completed | failed | paused
created_at: 2026-08-10T15:20:11Z
updated_at: 2026-08-10T15:47:02Z
parent:                                     # 独立使用为 null；被编排时指向父项目
  module: resagent
  run_id: res-20260810-a4b232
  task_id: task_001
project_path: /root/autodl-tmp/resagent-workspace/runs/res-20260810-a4b232/tasks/reproagent/task_001/attempt_001/repo_workspace
summary: 复现 pytorch/examples MNIST，3 epoch 达 99.04% test accuracy
bindings:                                   # 模块相关键名允许扩展
  conda_env: repro_repro-20260810-a4b232-3f2a1c
  dataset_cache: /root/autodl-tmp/datasets
  pip_cache: /root/autodl-tmp/pip-cache
key_artifacts:
  - type: repro_result
    path: result.md                         # 相对 project_path
    summary: 3 epochs, 99.04% accuracy
resume:
  cli: reproagent resume /path/to/workspace --instruction "..."
  note: 同一工作区开新一轮 loop，注入上次结果摘要与新指令
```

规则：

- **只增不改**：后续版本通过 `schema_version` 演进。
- 字段缺失容忍：读者必须能处理缺少 `bindings`/`key_artifacts`/`resume` 的卡片。
- 卡片由**模块自己写**，ResAgent 只读。
- 卡片是**派生信息**：即使丢失，也能从 state.json 重建（各模块可提供 `--rebuild-card` 之类的修复入口，可选）。

## 4. Resume 统一语义（所有执行模块一致）

**做**：同一工作区 + 新指令 + 上次结果摘要 → 开新一轮 loop。

```
<module> resume <session_path> --instruction "再跑 5 个 epoch 并报告"
```

- 工作区（repo/代码/env/logs/上次结果）原样保留；
- 新一轮 loop 的初始上下文注入：上次 final_summary + 关键产物指针 + 本次新指令；
- 同一个 session_id，steps 追加而非清空；`updated_at` 刷新，status 回到 running。

**不做**：从第 N 步的断点精确续跑。状态机复杂度爆炸，而真实需求（"继续"、"再跑点"、"换个参数"）用"同现场新指令"全覆盖。

## 5. 环境与缓存分层

```
共享层（跨项目，已有）：数据集缓存、pip 缓存
项目层（隔离，本期新增）：每个项目一个 conda env
```

**env 命名规则**（reproagent）：

| 场景 | env 名 | 效果 |
|------|--------|------|
| 被 ResAgent 编排 | `resenv_<run_id 短码>` | 同项目多个 repro 任务共享一个 env，第二个任务零安装 |
| 独立使用 | `repro_<task_id>`（现状） | 隔离 |

实现：`ReproTask` 增加 `env_namespace: str = ""`；非空时 env 名从 namespace 派生，否则从 task_id 派生（现状）。ResAgent 的 adapter 传 `env_namespace=run_id`。

**冲突回退**：同项目两任务依赖冲突（罕见，如 torch 版本打架）时，LLM 在装包阶段会遇到冲突 → 允许任务要求隔离 env（`ReproTask.isolate_env: bool = False`，置真回退 task 级命名）。

**CodingAgent 没有自己的 env**：它的绑定是"目标 repo + 跑 verify 的环境"，记入索引卡 bindings 即可。

## 6. 实施顺序

**先子模块、后总的**——子会话原语自包含、可独立测试；ResAgent 只是消费索引卡。

```
P0  本规范（唯一全局依赖，尤其是 §3 索引卡格式）   ← 本文档
M1  子模块原语（三个模块可并行，无相互依赖）:
      reproagent    session.yaml + list/status/resume + env_namespace   [参考实现]
      CodingAgent   session.yaml + resume(Python API) + list/status
      ExpAgent      session.yaml + advisory thread（轻量）
M2  ResAgent 集成（等 M1 任意一个模块落地即可开工）:
      会话/项目状态里的子会话索引、对话层 resume 路由
M3  项目记忆 RESEARCH.md（跨会话继承，最后做）
```

参考实现选 reproagent：它最长跑、resume 需求最真切、代码熟悉度最高。其余两个模块照它的模式对齐。

## 7. 反模式清单

1. 不把子会话事件流合并进大会话；
2. 不做断点步进恢复（§4 已界定 resume 语义）；
3. 不建统一消息总线/框架；索引卡 yaml 是全部契约；
4. 模块间不 import 对方会话相关代码；
5. env 清理策略不与会话生命周期耦合（删会话不连带删 env）。

---

## 8. 模块任务书

### 8.1 reproagent（参考实现）

**改动文件**：新 `session.py`；`controller.py`、`models.py`、`main.py`、`env.py` 修改；`tests/test_session.py` 新增。

**R1 — 索引卡写出**。`run_controller` 结束时（以及每次状态保存时）在 `workspace_dir/session.yaml` 写卡片。字段按 §3；`session_id = task.task_id`；`parent` 从新增 `ReproTask.parent_run: dict | None`（ResAgent 传入 `{"module":"resagent","run_id":...,"task_id":...}`）取值；`key_artifacts` 含 result.md；`bindings` 记 conda_env / dataset_cache / pip_cache（pip_cache 取 `_command_env` 实际解析值）。

**R2 — resume**。`main.py` 新子命令：

```bash
reproagent resume <workspace_dir> --instruction "..." [--max-steps N]
```

实现：从 `state.json` 读回原 ReproTask 字段 → 构造新 ReproTask（**同 task_id**，保证 env 复用）→ 新 loop。在初始上下文注入：上次 `final_summary` 摘要 + 本次 instruction。同一 state.json 续写（steps 追加）。

**R3 — list / status**。

```bash
reproagent list --root <dir>     # 扫描 **/session.yaml，表格输出
reproagent status <workspace_dir>  # 读 session.yaml + state.json 摘要
```

**R4 — 项目级 env**。`ReproTask` 增加 `env_namespace: str = ""`、`isolate_env: bool = False`。`env.py::_env_name` 改为：`isolate_env` 或 namespace 为空 → 现状 task_id 派生；否则 `resenv_<sanitize(namespace)[:40]>`。

**验收**：

| # | 检查 |
|---|------|
| 1 | 新测试：mock run 结束后 session.yaml 存在且字段齐全（schema_version/module/status/summary/bindings） |
| 2 | 新测试：resume 后 steps 追加、env 名不变（同 task_id 复用 env）、初始 prompt 含上次摘要与新指令 |
| 3 | 新测试：`env_namespace="res-x"` 时 env 名为 `resenv_res_x`；`isolate_env=True` 时回退 task 级 |
| 4 | `reproagent list --root <dir>` 能列出 §8.1 验收 run 的卡片 |
| 5 | 现有 84+ 测试全绿 |

### 8.2 CodingAgent

**改动文件**：新 `session.py`；`agent.py`、`models.py` 修改；`tests/test_session.py` 新增。无 CLI 现状下，resume 用 Python API 即可。

**C1 — 索引卡**：`run_code_task` / `run_code_question` 结束时在 `output_dir/session.yaml` 写卡片。`CodeTaskSpec`/`CodeQuestionSpec` 增加 `session_id: str = ""`（缺省自动生成 `code-<ts>-<uuid>`）与 `parent_run: dict | None`。kind 分别为 `task_session` / `qa_session`。

**C2 — resume（Python API）**：

```python
def resume_code_task(output_dir: Path, instruction: str, **overrides) -> PatchReport:
    """同一 output_dir 开新一轮；注入上次 summary + diff 摘要 + 新指令。"""
```

QA 会话不需要 resume（问答无延续性）。

**C3 — list/status**：模块级函数 `list_sessions(root) -> list[dict]`、`session_status(output_dir) -> dict`，读 session.yaml。

**验收**：mock/真实 run 后卡片存在；resume 后新 report 生成且 task_goal 含新指令与上次摘要；现有测试全绿。

### 8.3 ExpAgent（轻量）

**E1 — 索引卡**：每次 `advise()` 的 run_dir 写 `session.yaml`：`session_id` 自动生成 `exp-<ts>-<uuid>`；kind `advisory_session`；`key_artifacts` 指向 scientific_decision.json 与 papers/。

**E2 — 咨询线程（可选增强）**：`AdvisorContext` 增加 `thread_dir: str = ""`；非空时把本次 decision 摘要追加到 `<thread_dir>/thread.yaml`，并将此前摘要（最近 5 条）注入 situation 前，实现连续咨询。独立 REPL 使用时 thread 即项目。

**验收**：卡片写出；thread 模式下第二轮 advise 的 prompt 含首轮摘要；现有测试全绿。

### 8.4 ResAgent（M2，等 M1 任一模块落地）

**改动文件**：`chat_models.py`、`chat_tools.py`、`adapters/*.py`、`prompts.py`、`main.py`。

**R1 — 子会话索引**。`ConversationState` 增加 `session_index: list[SessionRef]`：

```python
class SessionRef(BaseModel):
    module: str
    session_id: str
    manifest_path: str
    status: str = ""
    summary: str = ""
```

adapters 在执行后把子会话卡片路径写入 artifact metadata + 会话事件 state_patch（`add_sessions`）。ResearchRun 侧的 task 记录同样补 `session_manifest` 指针。

**R2 — resume 路由**。chat_tools 新增工具：

```python
resume_subsession(session_id 或 manifest_path, instruction)
```

按卡片的 `module` 分发到对应 adapter 的 resume 通路（reproagent: 子进程 CLI；codingagent: Python API）。CHAT_SYSTEM 增加路由规则："继续上次那个复现/任务" → 查 session_index → resume_subsession。

**R3 — list/status 展示**：`/sessions` slash 命令 + `list_sessions(run_id?)` 工具，聚合 run 目录下所有 session.yaml。

**验收**：mock 全链路"继续上次那个复现"→ 正确命中索引并调用 resume；测试全绿。

## 9. M3：项目记忆（最后做）

每个 run 根部 `RESEARCH.md`：研究目标、关键决策、当前结论、待办。由 ResAgent 在 run 关键节点（ExpAgent 决策后、run 完成时）维护；对话层和 ExpAgent 咨询时注入其摘要。参照 CLAUDE.md 模式：纯文本约定，无框架。

## 10. 附录：resume 调用序列示例

```
用户: 继续上次那个 MNIST 复现，再跑 5 个 epoch
ResAgent chat loop:
  → 查 session_index，找到 manifest: .../repo_workspace/session.yaml (reproagent, completed)
  → tool: resume_subsession(manifest_path=..., instruction="再跑 5 个 epoch 并报告最终精度")
  → adapter: subprocess `reproagent resume <path> --instruction ...`
reproagent:
  → 读 state.json，原 task_id（env 复用，零安装）
  → 初始上下文：上次 99.04% @3ep + 新指令
  → 新 loop；session.yaml status→running→completed，updated_at 刷新
ResAgent:
  → 工具结果带回新摘要；会话事件记录（索引卡状态刷新）
```
