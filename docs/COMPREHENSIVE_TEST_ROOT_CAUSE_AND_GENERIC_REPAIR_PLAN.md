# ResAgent 综合测试根因分析与通用修复方案

> 日期：2026-08-12  
> 状态：开发指导文档，不代表代码已经修复  
> 范围：ResAgent 及其与 ExpAgent、CodingAgent、ReproAgent 的集成边界

## 1. 文档目标

本文基于以下云端综合测试重新分析系统现状：

- 日志：`/root/autodl-tmp/resagent-workspace/logs/comp_test.txt`
- CodingAgent 测试：`runs/res-20260811-adcfe8/`
- ReproAgent 测试：`runs/res-20260811-9e2ab2/`
- ask_user 实际 run：`runs/res-20260811-46951c/`
- 环境复用测试：`runs/res-20260811-033d29/`

目标不是针对这四个 run 打补丁，而是解决以下通用问题：

1. 如何让 ResAgent 可靠地调度相对独立的子模块。
2. 如何防止 LLM 产生非法任务派发或错误结束整个项目。
3. 如何让任务、状态、产物、会话和环境信息可以完整追溯。
4. 如何建立一套快速、确定、覆盖四个模块的测试体系。
5. 如何避免测试显示 PASS，但实际工作流已经发生错误。

## 2. 总体结论

本次日志中的 `3/4 passed` 不能代表系统通过综合验收。

| 测试 | 子模块执行 | 系统闭环 | 结论 |
|---|---|---|---|
| Test 1：CodingAgent | 代码修改成功 | parent 关联缺失，验证不足 | 部分通过 |
| Test 2：ReproAgent | GPU 实验成功 | 误派发后仍 completed | 部分通过 |
| Test 3：ask_user | 未触发提问 | 测试前提不成立 | 测试无效 |
| Test 4：env reuse | 第一次实验成功 | 第二个 ReproTask 未创建 | 测试无效 |

目前三个子模块的核心能力已经基本可用。主要风险集中在 ResAgent 的：

- 模块边界契约；
- 任务规范化和所有权；
- 状态机不变量；
- LLM 决策约束；
- 综合测试断言。

因此下一阶段应优先修复 orchestration correctness，而不是继续扩大子模块能力或运行更长实验。

## 3. 设计原则

### 3.1 子模块保持独立

ExpAgent、CodingAgent、ReproAgent 应继续满足：

- 可以脱离 ResAgent 独立调用；
- 保留自己的输入模型、内部状态、日志和 session card；
- 不导入 ResAgent 的 Python 模型；
- 不依赖 ResAgent 的固定安装路径；
- 独立调用时允许 `parent_run = null`；
- 被编排调用时接受可选的父级上下文和指定输出目录。

ResAgent 不应修改子模块内部算法，也不应复制其业务逻辑。ResAgent 只负责：

- 项目级目标和状态；
- 任务所有权和生命周期；
- 模块选择与调用顺序；
- 重试、暂停、恢复和结束；
- 跨模块产物引用；
- 项目级预算和最终报告。

### 3.2 使用边界契约，不使用共享实现依赖

不建议为了统一接口，再建立一个所有模块必须安装的共享 Python 包。这样会增加版本耦合。

推荐做法是：

- ResAgent 内部定义版本化的标准任务信封；
- 每个 adapter 将标准任务翻译为子模块原生输入；
- adapter 将子模块原生结果翻译为标准执行结果；
- 契约用 Pydantic 模型和可导出的 JSON Schema 描述；
- 子模块只需要稳定自己的公开 API，不需要了解 ResAgent 内部模型。

### 3.3 LLM 负责判断，代码负责约束

LLM 可以决定下一步做什么，但不能绕过以下确定性约束：

- 任务属于哪个模块；
- 当前任务是否允许执行；
- 哪些 action 是当前状态下合法的；
- 是否可以 finish；
- 是否超过预算和重试次数；
- completed 状态是否还能继续推进。

这些必须由代码校验，不能只写在 prompt 中。

## 4. 问题一：任务类型与执行模块映射不可靠

### 4.1 现象

环境复用测试要求连续运行 2 epoch 和 3 epoch。ExpAgent 已经给出第二个运行计划，但 ResAgent 把它转换成：

```text
agent = ExpAgent
kind = advise
action_type = run_task
```

系统里没有生成第二个 ReproTask，因此无法进行第二次实验，也无法验证环境复用。

### 4.2 根因

当前 `ExpAgentAdapter._actions_to_tasks()` 使用固定映射：

```python
"run_task" -> ExpAgent / advise
```

它只看 `action.type`，没有可靠使用：

- 明确的 executor；
- `plan.kind`；
- payload 中的 `paper_url`、`repo_url`、`experiment_goal`；
- 任务是否本质上需要执行代码或复现实验。

`run_task` 本身是科学计划语义，不是执行模块语义，因此不能直接映射到 ExpAgent。

### 4.3 通用解决方案：标准任务信封

ResAgent 内部任务建议使用以下概念模型：

```json
{
  "schema_version": 1,
  "task_id": "task_002",
  "executor": "ReproAgent",
  "capability": "reproduce_experiment",
  "required": true,
  "source": {
    "module": "ExpAgent",
    "artifact_id": "scientific_decision_003"
  },
  "input": {
    "paper_url": "...",
    "repo_url": "...",
    "experiment_goal": "Run 3 epochs and compare with task_001"
  },
  "acceptance": {
    "required_metrics": ["test_accuracy", "test_loss"],
    "required_artifacts": ["result.md"]
  }
}
```

字段含义：

- `executor`：唯一执行模块，必须显式确定；
- `capability`：稳定能力名，不与子模块内部类名绑定；
- `required`：是否阻塞项目完成；
- `source`：任务从哪个科学决策产生；
- `input`：传给 adapter 的业务输入；
- `acceptance`：ResAgent 判断任务是否完成所需的最低证据。

### 4.4 兼容现有 ExpAgent 输出

短期不要求立即修改 ExpAgent。ResAgent adapter 可增加一个集中式 normalizer：

1. 如果输出有显式 `executor`，直接使用并验证。
2. 否则优先读取明确的 `plan.kind`。
3. 仅对旧格式使用有限的兼容映射。
4. 如果仍有歧义，返回结构化 validation error，不创建猜测任务。

兼容映射示例：

| plan.kind / executor | 标准 executor | capability |
|---|---|---|
| `repro_task` | ReproAgent | `reproduce_experiment` |
| `coding_task` | CodingAgent | `modify_code` |
| `result_analysis` | ExpAgent | `analyze_result` |
| `literature_search` | ExpAgent | `search_literature` |
| `ask_user` | ResAgent | `request_user_input` |
| 只有 `run_task` | 不确定 | 拒绝并要求重新规划 |

长期可以让 ExpAgent 原生输出 `executor + capability`，但这是接口增强，不应让 ExpAgent 依赖 ResAgent 代码。

## 5. 问题二：ExpAgent 任务无法稳定绑定和完成

### 5.1 现象

Test 2 中出现多个 pending ExpAgent 任务和重复咨询。已经完成科学咨询，但原任务仍为 pending，新咨询又产生新任务。

### 5.2 根因

当前三层契约不一致：

- Controller 支持 `call_exp_agent(task_id=...)`；
- prompt 声明 `call_exp_agent` 只有 `reason, focus`；
- LLM 因此经常进行不带 `task_id` 的裸调用；
- 没有 task_id 时，handler 不会完成任何已有 advise task。

### 5.3 通用解决方案

将两种语义明确分开：

```text
consult_exp_agent(reason, focus)
call_exp_agent(task_id)
```

- `consult_exp_agent`：临时科学咨询，不对应已有任务；
- `call_exp_agent`：执行 pending ExpAgent task，必须携带 task_id；
- 如果存在 pending ExpAgent task，Planner 默认只能选择绑定式调用；
- 临时咨询产生的新任务仍必须经过 task normalizer；
- 已完成的咨询任务必须绑定其 scientific decision artifact。

如果暂时不希望增加 action 名称，也至少应规定：

- 有 pending advise task 时，`call_exp_agent.task_id` 必填；
- 无 task_id 的调用只允许在初始分析或明确 replan 场景使用；
- adapter 返回的 spawned tasks 必须去重。

### 5.4 防止重复任务

建议为任务增加稳定的语义指纹：

```text
fingerprint = hash(executor + capability + normalized_input + source_goal)
```

创建任务前检查：

- 相同 fingerprint 的 pending/running task：复用；
- 已 completed 且输入证据未变化：复用结果；
- 明确替代旧任务：使用 `supersedes_task_id` 并将旧任务标为 superseded；
- 不允许仅因再次咨询就生成语义相同的新任务。

## 6. 问题三：非法派发被拦截后仍可结束项目

### 6.1 现象

Test 2 中 Controller 正确拒绝：

```text
call_coding_agent(task_004)
Task task_004 belongs to ExpAgent, not CodingAgent.
```

下一步 LLM 直接调用 finish，项目状态变成 completed，测试仍报告 PASS。

### 6.2 根因

- task ownership guard 已存在且有效；
- 但 adapter error 没有形成“未解决失败”；
- finish handler 没有完成条件；
- `step()` 看到 finish 就无条件设置 completed；
- 测试脚本只打印 error，不断言。

### 6.3 通用解决方案：状态机不变量

ResAgent 应集中定义以下不变量：

#### 终态不变量

- `completed`、`failed`、`cancelled` 为终态；
- 终态调用 `step()` 必须返回 terminal observation，不能再次调用 Planner；
- 恢复终态只能通过显式的 reopen/resume API，并记录事件。

#### finish 不变量

只有同时满足以下条件才允许 completed：

- 没有 required pending/running task；
- 没有未处理的 failed/blocked/needs_user_input task；
- 没有 `_retry_scheduled`；
- 没有 pending question；
- 最近的 adapter error 已被 retry、supersede、skip 或用户确认处理；
- 至少存在满足研究目标的关键 artifact。

finish 校验失败时应返回：

```json
{
  "result": "rejected",
  "reason": "unresolved_tasks",
  "task_ids": ["task_004"],
  "allowed_next_actions": ["call_exp_agent", "ask_user"]
}
```

不能将 finish rejection 记录为 completed。

### 6.4 合法动作候选集

不要让 LLM自由组合 action 和 task_id。每一步由代码先生成候选动作：

```json
[
  {"action": "call_exp_agent", "task_id": "task_004"},
  {"action": "call_repro_agent", "task_id": "task_005"},
  {"action": "ask_user", "reason": "budget_approval"}
]
```

LLM只在候选集中选择，并提供理由。Controller 仍在执行前二次校验。

这样既保留 agentic loop 的灵活性，也不会让模型改变任务所有权。

## 7. 问题四：ask_user 测试不可重复

### 7.1 现象

测试要求把 `NUM_EPOCHS` 从 10 改成 20。输入完整、风险很低，LLM合理地直接完成任务，没有 ask_user。

测试却断言必须出现 pending question，因此失败。报告给出的 run ID 也不是实际生成的 `res-20260811-46951c`。

### 7.2 根因

测试把“模型是否选择提问”和“暂停恢复机制是否正确”混成同一件事。

前者是非确定性策略行为，后者是确定性状态机行为。

### 7.3 通用解决方案

ask_user 状态机测试必须使用固定 Planner：

1. 第一步固定返回 `ask_user`；
2. 断言 state 持久化为 paused；
3. 重新从磁盘加载 state；
4. 调用 answer/submit_user_response；
5. 断言 question 移入 answered_questions；
6. 断言状态恢复为 running；
7. 再运行一步并完成任务；
8. 重复提交同一 question_id 必须幂等或明确拒绝。

真实 LLM 云端测试可以观察模型是否合理提问，但不能把“它必须提问”作为状态机验收条件。

## 8. 问题五：CodingAgent 父级会话关联缺失

### 8.1 现象

CodingAgent session card 中：

```yaml
parent: null
```

同一 run 中 ReproAgent 已正确记录 ResAgent 父级。

### 8.2 根因

ResAgent 的 CodingAgent adapter 构造 `CodeTaskSpec` 时没有传 `parent_run`。

### 8.3 通用解决方案

adapter 在编排调用时传递：

```json
{
  "module": "resagent",
  "run_id": "res-...",
  "task_id": "task_001",
  "attempt": 1
}
```

要求：

- 字段必须是 CodingAgent 公开 API 已支持的可选字段；
- 独立调用 CodingAgent 时 parent 可以为空；
- ResAgent 不修改 CodingAgent 内部 session 实现；
- adapter 集成测试验证 session card 中的 parent；
- artifact metadata 同时记录 session manifest 路径。

## 9. 问题六：测试 PASS 判定过弱

### 9.1 现象

综合测试包装器只要测试函数不抛异常就记录 PASS。Test 2 即使打印了 error，仍然通过；Test 4 只完成一个任务，也仍然通过。

Test 1 只检查源码出现 `loss` 字符串，没有验证：

- 程序实际运行；
- loss 被写入结果；
- accuracy 逻辑未改变；
- session parent 正确；
- 全局状态没有 unresolved task。

### 9.2 通用解决方案：结构化验收器

测试不应依赖终端文本，应直接检查 state 和 artifact：

```python
assert state.run.status == RunStatus.completed
assert not unresolved_required_tasks(state)
assert not unhandled_error_observations(state)
assert all_artifact_paths_exist(state)
assert session_parent_links_are_valid(state)
```

每个测试返回结构化结果：

```json
{
  "status": "passed",
  "run_id": "res-...",
  "assertions": [...],
  "duration_seconds": 120,
  "artifact_index": "..."
}
```

只打印信息不能算验收。

## 10. 问题七：Shell pipeline 可能掩盖依赖安装失败

### 10.1 现象

ReproAgent 执行过：

```bash
pip install ... 2>&1 | tail -20
```

本次安装成功，但如果没有启用 `pipefail`，pip 失败而 tail 成功时，整体返回码仍可能是 0。

### 10.2 归属与解决方案

这是 ReproAgent runner 的通用执行语义问题，不应在 ResAgent 中特判 pip。

推荐在 ReproAgent 会话单独修复：

- runner 使用 `bash -o pipefail -c`；或
- runner 自己流式保存和裁剪日志，不允许 LLM 用 `| tail` 控制输出；
- command result 保存原始 returncode、pipeline status、stdout/stderr 路径；
- 增加“左侧命令失败、右侧 tail 成功”的回归测试。

ResAgent 只消费 ReproAgent 的标准 outcome，不解析 shell 文本猜测成功失败。

## 11. 推荐的测试体系

不能用一次真实 LLM 云端测试同时验证状态机、接口、网络、GPU和科学质量。推荐分四层。

### 11.1 L1：纯状态机单元测试

全部使用固定 Planner 和 fake adapter，几秒内完成：

- 非法跨模块派发被拒绝；
- 拒绝后不能 finish；
- required pending task 阻止 finish；
- completed 后 step 不再调用 Planner；
- ask_user pause/save/load/answer/resume；
- transient failure 创建 attempt_002；
- retry 达上限后 blocked；
- superseded task 不再执行；
- task ID 全局唯一；
- 同 fingerprint 任务去重。

### 11.2 L2：Adapter 契约测试

对子模块使用最小 fake native result，验证：

- 标准任务到原生输入的字段完整；
- parent_run 正确；
- output_dir 不重复嵌套；
- 子模块状态到标准 outcome 的映射；
- artifact 路径存在且位于 run workspace；
- session manifest 可追溯；
- `completed_with_warnings` 不被当成完全失败；
- blocked/needs_user_input 不被当成 completed。

### 11.3 L3：确定性的四模块闭环测试

建立一个稳定的小型 fixture repository，任务应在几分钟内完成，不运行 160 epoch。

建议场景：

1. ExpAgent 生成一个 1 epoch 基线任务；
2. ReproAgent 执行基线并产出 accuracy；
3. ExpAgent 分析结果，指出缺少 loss；
4. CodingAgent 给训练脚本增加 loss 输出；
5. ReproAgent 在同一项目环境中重新运行 1 epoch；
6. ExpAgent 对比修改前后结果；
7. ResAgent 在所有 required tasks 完成后 finish。

为了避免 LLM 随机改变流程：

- orchestration 决策使用 scripted planner；
- 子模块可以先使用真实 API 的 deterministic/mock 模式；
- 每一步的输入和预期产物固定；
- 验证重点是四模块契约和状态闭环，不评价科研创新性。

### 11.4 L4：真实 LLM + GPU 云端验收

发布前运行，不作为每次提交的快速回归：

- 使用真实 DeepSeek；
- 使用真实 ExpAgent、CodingAgent、ReproAgent；
- 使用小型公开仓库或固定 fixture repo；
- GPU 训练限制为 1 至 5 epoch；
- 允许 LLM 的具体步骤不同；
- 验收最终状态、模块覆盖、产物、指标和错误处理，不要求固定 action 序列。

完整 160 epoch 属于科学复现基准，不属于系统集成测试。它可以作为里程碑测试单独运行。

## 12. 四模块闭环测试的最低验收标准

### 12.1 工作流

- ExpAgent、CodingAgent、ReproAgent 至少各成功调用一次；
- 每次调用均对应明确 task_id；
- 无跨模块错误派发；
- 所有 required task 最终为 completed、superseded 或用户明确 skip；
- finish 前不存在 unresolved error；
- 全局 run 最终为 completed。

### 12.2 产物与会话

- 每个 task/attempt 有 task manifest；
- 每个子模块有 session card；
- 被 ResAgent 调用的 session 均有正确 parent；
- state 中登记的 artifact 路径全部存在；
- artifact 不逃逸 run workspace；
- 最终报告引用关键 artifact 和指标证据。

### 12.3 实验

- GPU 可用时实际使用 GPU；
- epoch 数满足测试限定；
- 至少获得一个可解析指标；
- 修改代码后重新运行并产生新 attempt/task 证据；
- 报告明确区分 smoke/bounded test 与论文完整复现。

### 12.4 环境复用

- 同一 project/run 中实际完成两个 ReproTask；
- 两个 session 报告相同 env identity；
- 第二次环境准备明确记录 `reused=true`；
- 第二次没有重新创建 Conda 环境；
- 不同 project/run 默认不误用同一可变环境；
- 环境不兼容时创建新 revision，而不是污染已有环境。

### 12.5 ask_user

- 问题持久化后 run 为 paused；
- 进程退出再加载仍能看到问题；
- 回答后问题进入 answered history；
- run 恢复为 running；
- 同一回答不会导致重复恢复；
- 无外部回答时测试不能自动越过暂停点。

## 13. 推荐实施顺序

### Phase A：先修状态机和测试可信度

1. 增加 terminal guard。
2. 增加 finish validator。
3. 综合测试遇到 observation error 必须失败。
4. 修复 deterministic ask_user 测试。
5. 为以上规则补单元测试。

验收：错误后不能伪 completed；终态不能重复 step；Test 3 稳定通过。

### Phase B：修任务契约和模块路由

1. 引入标准任务信封和 capability。
2. 集中实现 task normalizer/validator。
3. 消除 `run_task -> ExpAgent` 固定映射。
4. 分离临时咨询和绑定式 ExpAgent task。
5. 给 Planner 提供合法 action candidates。
6. 增加 task fingerprint 去重。

验收：第二个实验计划稳定生成 ReproTask；不存在跨模块派发；无重复 pending advise task。

### Phase C：补齐会话与 adapter 契约

1. CodingAgent adapter 传 parent_run。
2. 统一三个 adapter 的 outcome 映射。
3. 验证 artifact/session 路径。
4. 增加 adapter contract tests。

验收：三个子模块仍可独立运行；编排调用均有正确 parent；无硬编码模块路径。

### Phase D：重写综合测试

1. L1/L2 纳入本地 pytest。
2. 建立确定性的 L3 四模块闭环 fixture。
3. 修复 L4 云端测试断言。
4. 真正执行两个 ReproTask 验证环境复用。
5. 输出机器可读 test report。

验收：快速测试可重复；云端测试失败时能准确指出模块、task、attempt 和不变量。

### Phase E：在对应子模块处理执行语义

- ReproAgent：pipefail 和日志管道；
- CodingAgent：仅在其公开 API 尚不支持 parent_run 时再提出接口修改；
- ExpAgent：可选地原生输出 executor/capability；
- 所有子模块修改都在各自仓库和专门会话完成。

## 14. 不建议采用的修复方式

以下方式只能掩盖问题：

- 在 prompt 中增加更多“不要选错 task”的自然语言；
- 看到重复咨询后简单限制 ExpAgent 最多调用一次；
- 把所有 `run_task` 都改成 ReproAgent；
- Test 4 只检查 distinct env 数量为 1；
- Test 3 继续等待真实 LLM 自己产生 ask_user；
- 发现 adapter error 后仍允许 LLM 自行决定是否 finish；
- 在 ResAgent 中解析子模块日志字符串判断成功；
- 为某篇论文、某台 GPU 或 AutoDL 路径写专用分支。

## 15. 变更归属

| 变更 | 所属仓库 |
|---|---|
| 状态机终态和 finish validator | ResAgent |
| 标准任务信封、normalizer、合法动作候选 | ResAgent |
| ExpAgent task 绑定和去重 | ResAgent adapter/controller |
| CodingAgent parent_run 传递 | ResAgent adapter；必要时 CodingAgent 公共 API |
| 综合测试和验收器 | ResAgent |
| Shell pipefail | ReproAgent |
| ExpAgent 原生 executor/capability | ExpAgent，可选的长期增强 |
| 子模块内部科学、编程、复现能力 | 各自仓库 |

ResAgent 只能修改自己的代码。若确认需要调整子模块，应在 ResAgent 中生成问题说明或接口请求文档，再由对应模块的开发会话处理。

## 16. 最终完成定义

只有满足以下条件，才能认为这轮问题真正解决：

1. 非法跨模块派发不能进入子模块，也不能被 finish 掩盖。
2. ExpAgent 产生的执行任务都具有明确 executor。
3. 已执行的 ExpAgent task 能被正确完成，不产生语义重复任务。
4. completed run 不能再次推进。
5. ask_user 测试确定、可恢复且不依赖 LLM主动提问。
6. 环境复用测试真实完成两个 ReproTask。
7. CodingAgent 和 ReproAgent session 均能追溯到父 run/task。
8. 综合测试的任何 error、缺失 artifact 或 unresolved required task 都会导致失败。
9. 四个模块仍可独立使用，调用路径和服务器路径均不写死。
10. bounded 云端测试在合理时间内完成；完整论文训练与系统集成测试分离。

完成这些工作后，ResAgent 才具备可靠扩展更多科研模块的基础。否则新增论文写作、检索或原创实验模块，只会放大当前任务契约和状态机中的不确定性。
