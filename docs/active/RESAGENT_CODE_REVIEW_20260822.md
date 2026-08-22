# ResAgent 只读审查报告

> 日期：2026-08-22
> 范围：`src/resagent/`（ResAgent 模块，四模块系统中的编排层）
> 依据：`FOUR_MODULE_CODE_REVIEW_AND_SIMPLIFICATION_PLAN.md`
> 状态：Phase B 只读审查完成，待执行整理（分支 `codex/readability-cleanup-20260822`）

## 1. 基线

| 项 | 值 |
|---|---|
| 仓库 | `/home/cyl/ResAgent`，分支 `master` @ `f809a41`（"fix: preserve summary on explicit finish"） |
| 工作区 | 干净（仅计划文档本身未跟踪） |
| Python | 系统 Python 3.12.3，pydantic 2.13.4 / pyyaml 6.0.1 / httpx 0.28.1 |
| 测试 | 220 passed in 1.45s（21 个测试文件） |
| 依赖 | pydantic≥2.0、pyyaml≥6.0、httpx≥0.24（dev: pytest、pytest-asyncio） |
| 生产代码 | 42 个 `.py`，7735 行 |

## 2. 真实入口与主流程

**CLI 入口**（`resagent.main:main`）：`init / run / step / status / answer / chat / resources`。
**Python 入口**：`Controller.run()` → `Controller.step()` → `run_loop()`。

**唯一主流程**（可一路追踪到落盘）：

```text
resagent run/answer
  → run_loop → Controller.step
    → [terminal/paused 短路] → apply_finish_control → validate_finish
    → ensure_directive_replan
    → (_next_retry_action 或 planner.choose_action) → _execute
      → handler(exp/coding/repro/classify/ask/finish) → adapter → 子模块
      → register_artifact / register_task_resources
  → save_state 每步落盘 + generate_all 生成报告
```

## 3. 文件职责表

| 目录 | 文件 | 职责 |
|---|---|---|
| 根 | `main.py` | CLI 解析+分发 |
| | `orchestrator.py` | init/resume/run 生命周期、build_controller |
| | `models.py` | 全部 Pydantic 模型 + State 查询助手 |
| | `config.py` | yaml+env 配置加载 |
| | `llm.py` | chat 层 LLM 传输（与 planner 重复） |
| | `capabilities.py` | 能力注册表（单一来源） |
| | `resources.py` | M2 资源/租约/manifest |
| | `cleanup.py` | M2 环境清理 plan/apply |
| controller | `loop.py` | 主循环 step/run |
| | `actions.py` | 各 action 执行 handler + retry |
| | `contracts.py` | 确定性不变量（finish/analysis/directive） |
| | `planner.py` | LLM 决策 next-action |
| | `tasks.py` | Task 唯一创建入口 |
| | `prompts.py` | Prompt 模板 |
| adapters | `expagent/adapter.py`+`task_conversion.py`+`dependency_graph.py` | ExpAgent 适配/action→task 转换 |
| | `codingagent.py` / `reproagent.py` | Coding/Repro 适配 |
| persistence | `state.py`/`workspace.py`/`sessions.py`/`report.py` | 状态读写/路径/会话/报告 |
| conversation | `loop.py`/`tools.py`/`models.py`/`history.py`/`session_tools.py` | chat REPL 层 |
| context | `builder.py`/`policy.py` | 控制器/专家上下文 |
| policies | `retry.py`/`safety.py` | 重试策略 /（死）安全门 |
| integrations | `module_paths.py` | 5 层模块路径解析 |

## 4. 问题清单

共 **53 个**：无严重/高，**16 中、37 低**。
类别分布：Dead/Legacy 15、Correctness 14、Redundancy 10、Test Gap 4、Ownership 3、Readability 3、Overdesign 3、Split Mainline 1。

### 4.1 中严重度（16 个）

| ID | 类别 | 文件:行 | 问题 | 最小处理 |
|---|---|---|---|---|
| C-F1 | Split Mainline | actions.py:358-377; prompts.py:63-64; contracts.py:307-337; planner.py:78-102 | `classify_failure` 在 prompt 宣传但 candidate 列表从不发射，handler 是 no-op，LLM 分类路径不可达 | 删 action/handler/Planner.classify_failure，保留 deterministic classify_transient |
| C-F5 | Correctness | planner.py:156-160 | `_parse_response` 对非法/缺失 action 默认 `finish`（终态），违反 fail-closed | 非法 action 抛 PlannerError 而非默认 finish |
| A-AD1 | Dead / Legacy | actions.py:98-104; tasks.py:22 | `supersedes_task_id` 分支永远不触发（无写入者），真正 supersede 走 retire_superseded_actions | 删死分支 + fingerprint ignore-set 条目 |
| A-AD2 | Dead / Legacy | context/builder.py:97-109 | `build_expagent_context` 无生产调用者（adapter 自己构建） | 删函数 + context/__init__ 导出 |
| A-AD4 | Redundancy | codingagent.py:124; reproagent.py:210-219; actions.py:143-152 | outcome 词汇表 ×3 归一化，会漂移；returncode fallback 死 | 单一定义 + 删除死 fallback |
| P-F1 | Correctness | main.py:159-164 | `resagent step` 只 resume 后什么都不做，静默 no-op | 执行一步 或 删子命令 |
| P-F2 | Redundancy | workspace.py; state.py:39-55; report.py:13; resources.py:27 | run-dir 路径 ×4 重算，WorkspaceLayout 的 report 属性全死 | 统一走 WorkspaceLayout，删死属性 |
| CV-F1 | Correctness | conversation/loop.py:328-333 | `/use`、`/status <id>` 丢弃 state_patch，active_run_id 永不生效 | 保留 ToolOutcome 并 apply state_patch |
| CV-F2 | Correctness | main.py:159-163 | step 子命令静默 no-op（重复确认） | 同 P-F1 |
| CC-F1 | Correctness | resources.py:203-216,378-412 | env_id 路径穿越防护只在 1/3 处（_lifecycle_lock_path 有，read_manifest/acquire_lease/iter_manifests/cleanup._write_manifest 无） | 统一加 `Path(env_id).name != env_id` 守卫 |
| CC-F2 | Redundancy | contracts.py:33-40; capabilities.py:39-46 | capability 词汇表 ×2（_CAPABILITY_KIND vs V2_CAPABILITIES） | 从 V2_CAPABILITIES 派生，或 import 时断言一致 |
| E-F1 | Correctness | main.py:159-164 | step 静默 no-op（三审独立确认） | 执行一步 或 删 |
| E-F2 | Correctness | main.py:122-127; config.py:79-82 | CLI 路径写入 cfg.agents（tier-3）而非 cfg.cmd_*（tier-1），env 变量压过 CLI | CLI 写入 cmd_* 字段 |
| E-F3 | Dead / Legacy | policies/safety.py:6-27; config.py:37-38 | SafetyPolicy 从未 import，confirm_before_* 配置惰性 | 删 safety.py + 死配置 |
| E-F5 | Redundancy | llm.py:13-47; planner.py:116-150 | LLM 传输 ×2 已漂移（timeout 120 vs 60，空响应保护不一致） | 合并为单一传输 |
| E-F8 | Test Gap | test_phase0_contract.py:25-32 | CLI 零行为测试，step no-op 与 tier 违反都没被测到 | 加 CLI 行为测试 |

### 4.2 低严重度（37 个）摘要

**死代码可删**：SUMMARY_PROMPT（prompts.py:211）、RetryPolicy.can_retry（retry.py:20-22）、CapabilityRegistry.available()/get()（capabilities.py:167,254）、module_paths 的 callable/_detect_callable/_git_info/git_commit/git_dirty（module_paths.py:24-27,108-136）、list_conversations/rebuild_from_events（history.py:86-94,133-149）、cleanup_enabled/max_task_retries 死配置（config.py:36,54）、ConvArtifactRef 第二套 artifact 模型（conversation/models.py:58-68）、ExpertCard 5 个无人读契约字段（conversation/models.py:196-215）、scratch_summary 从不写入（conversation/models.py:156,173）、check_callable 的 tier>1 分支（capabilities.py:277-281）。

**重复可合并**：_ensure_import ×3（三个 adapter）、_pid_alive ×3（resources/cleanup）、task_manifest 写入 ×2（coding/repro adapter）、card_to_session_ref 投影 ×2、chat 启动 resolve_all 跑两遍（orchestrator.py:106-113）。

**职责错位（Ownership）**：analysis_required 双写（models.py:235 vs 168）、submit_user_response 在 persistence 层改 task 状态（state.py:155-157）、adapter 里写 run 级策略（adapter.py:106-111）。

**小 bug（Correctness）**：api_calls_used 确定性步骤也 +1（loop.py:90,122）、resume 忽略 conversations_dirname（main.py:209-214）、工具预算 off-by-one（loop.py:72-73）、run --goal 遗留孤儿目录（main.py:141-148）、generate_all 在 chat/step 不调用（persistence F3）、coding adapter 返回空 workspace_path（codingagent.py:130）。

**测试缺口**：generate_all（report.py）、analysis_required 写入（adapter.py:106-111）、classify_failure/_parse_response。

## 5. 分类与执行方案

| 轮次 | 类别 | 内容 | 行为变化 |
|---|---|---|---|
| R1 | 安全整理 | 删死代码（见 4.2 死代码 + 4.1 死代码类） | 无 |
| R2 | 内部结构整理 | 合并重复（capability 词汇、LLM 传输、outcome 词汇、workspace 路径、_ensure_import、_pid_alive、manifest 写入） | 无（映射一致时） |
| R3 | 行为风险 | 修 bug（step no-op、/use patch、CLI tier、planner fail-closed、env_id 加固、api 计数、generate_all 覆盖、analysis_required 单源） | 有（逐个审批） |
| R4 | 测试补足 | CLI 行为测试、generate_all 测试、analysis_required 测试、classify/parse 测试 | 无（附加） |

## 6. 风险与未处理项

- `list_conversations` / `rebuild_from_events`（history.py）为公开 helper + 有测试，删除属公开 API 移除；若需保留可接线到 REPL（属新功能，不在本轮范围）。默认删除。
- `analysis_required` 双源折叠（persistence F4）触及状态语义与 legacy 兼容，需谨慎，单独提交。
- Prompt 变化（C-F1 删 classify_failure、E-F5 合并 LLM 传输超时）属行为变化，需单独审批。
- 跨模块契约（capability 名称、AgentTask 字段、result.json 语义）一律不动。

## 7. 验收

- 全量单测通过（≥220，不减少关键覆盖）
- `git diff --check` 通过
- 公共入口 import 测试（`import resagent`、`python -m resagent.main --help`）
- 修改前后依赖对比（不新增依赖）
- 删除代码均附可达性证据

## 8. 执行结果（实际）

### 8.1 提交记录（分支 `codex/readability-cleanup-20260822`）

| commit | 内容 |
|---|---|
| `1a5ec76` | docs: 只读审查报告 |
| `5a982c6` | refactor: 删死代码与惰性配置（R1） |
| `0021620` | refactor: capability 词汇表防漂移守卫（R2） |
| `1611102` | fix: 修 6 个缺陷（R3） |
| `351cd07` | test: 回归测试（R4） |
| `60c750d` | fix: step 单步语义、env_id 校验、api 计数 |
| `d7ea08f` | restore: 恢复会话重建与会话列表公共接口 |
| `e626d90` | test: 第二轮修复的回归测试 |
| `d2383fe` | docs: 记录执行结果、延期项与测试结果 |
| `2c91818` | fix: step 恢复 interrupted run 并受控处理 PlannerError |

### 8.2 已处理

- **死代码删除**：supersede 死分支、`build_expagent_context`、`SafetyPolicy`、`can_retry`、`available()`、`SUMMARY_PROMPT`、`module_paths` 死字段（callable/git）、`cleanup_enabled`/`max_task_retries`/`confirm_before_*` 惰性配置。
- **capability 词汇守卫**：`_CAPABILITY_KIND` 与 `V2_CAPABILITIES` import 时断言一致。
- **6 个缺陷**：`step` 静默 no-op、CLI 路径层级（env 压过 CLI）、planner 对非法 action 默认 finish、coding adapter 返回空 workspace_path、`/use` `/status` 丢弃 state_patch、chat 路径不生成报告。
- **第二轮修正**：
  - `step` 改为直接 `ctrl.step(state)`（非终态保持 running，不再被 `run_loop` 标记 interrupted）。
  - `step` 执行前恢复 interrupted run，并将 PlannerError 收敛为已持久化的受控中断，不向 CLI 泄漏 traceback。
  - 恢复 `rebuild_from_events` / `list_conversations` 公开接口 + 测试 + 文档（首轮误判为死代码，实际是恢复路径）。
  - 统一 `_validate_env_id`：`read_manifest` / `acquire_lease` / `_lifecycle_lock_path` / `cleanup._write_manifest` 复用，拒绝空值、绝对路径、`..`、路径分隔符。
  - `api_calls_used` 只在真正调用 planner 时计数；显式 finish、retry 不计数。

### 8.3 延期项（未处理，附原因）

- **R2 去重（低 severity、drift 未实际发生）**：`_ensure_import`×3、task_manifest×2、outcome 词汇×3、workspace 路径×4、`_pid_alive`×3、chat 启动 `resolve_all`×2。按计划「不因『看起来更工程化』拆更多层」克制处理。
- **R3 状态语义（需单独评审）**：`analysis_required` 双源折叠、`submit_user_response` 职责错位——触及状态机与 legacy 兼容，风险高于收益。

### 8.4 测试结果

- 全量：**226 passed**（修改前 220）。
- 新增回归：`step` 推进且保持 running、interrupted run 单步恢复、PlannerError 受控退出、planner fail-closed、generate_all、env_id 路径穿越、显式 finish 不耗 API 预算。
- 恢复：`rebuild_from_events` / `list_conversations` 相关测试。
- `git diff --check` 通过；`import resagent` 通过；无新增依赖。

### 8.5 经验教训

删除死代码必须以「公开 API + 测试覆盖」为准，不能仅凭内部零引用判定——首轮 `CapabilityRegistry.get()` 与 `rebuild_from_events`/`list_conversations` 均被误判，测试与任务单已纠正。
