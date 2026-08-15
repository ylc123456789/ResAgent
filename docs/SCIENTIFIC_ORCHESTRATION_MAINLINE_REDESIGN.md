# 科学编排主线重构开发方案

**日期**：2026-08-14  
**状态**：V2 已在四仓功能分支落地；最终回归与合并待完成
**涉及模块**：ResAgent、ExpAgent；reproagent 仅补能力卡；CodingAgent 原则上不改  
**关联文档**：`EXPERIMENT_OPERATOR_REDESIGN.md`、`EXECUTION_CONTRACT_V1.md`、`CONVERSATION_LAYER_DESIGN.md`

## 1. 目标

本次重构解决的不是单次 prompt 偏差，而是科学决策与执行调度的边界问题。

最终系统只保留一条主线：

```text
ExpAgent 产生科学动作图
        ↓
ResAgent 根据统一能力注册表选择执行模块
        ↓
CodingAgent 修改代码 / ReproAgent 执行实验
        ↓
ExpAgent 分析实验结果并形成科学结论
        ↓
ResAgent 决定继续、重试、询问用户或结束
```

完成后必须满足：

1. ExpAgent 只决定“科学上做什么、为什么做、先后关系是什么”。
2. ResAgent 独占“由谁执行、在哪里执行、如何管理状态和资源”的职责。
3. 实验结果在结束前必须得到科学分析，除非任务被明确标记为纯工程验证。
4. Chat router 和 ResearchRun controller 使用同一份模块能力注册信息。
5. 删除旧协议和重复路由逻辑，不长期维护双主线兼容代码。

## 2. 触发问题与根因

### 2.1 P4 真实测试现象

P4 中 ExpAgent 生成了两个 required `run_task`：

1. `run_odenet_3epoch`：真正运行 GPU 实验。
2. `report_deviations`：读取结果并解释与论文的偏差。

ResAgent 将两个 `run_task` 都路由给 ReproAgent。第二个任务本质是科学分析，却让实验操作员执行，最终消耗 30 个 agent step，并以 `completed_with_failures` 结束。

正确任务图应为：

```text
ReproAgent / execute_experiment
        ↓
ExpAgent / analyze_results
```

### 2.2 根因一：动作语义和执行器耦合

现有 `RecommendedAction` 同时包含：

- `type`
- `plan.kind`
- 执行相关字段
- 科学目标字段

`type` 与 `plan.kind` 重复，且 `run_task` 同时承载“运行实验”“解析日志”“生成报告”等不同语义。模型只要选错一个字符串，执行器就会跟着选错。

### 2.3 根因二：两套能力描述

当前：

- Chat router 读取 `agent.yaml` 和 `CapabilityRegistry`。
- ResearchRun controller 使用 `controller/prompts.py` 中写死的团队说明。
- `controller/contracts.py` 再维护一份硬编码 action→executor 映射。

这三处会发生漂移。能力卡目前并没有真正参与 ResearchRun 的路由。

### 2.4 根因三：prompt 与允许动作矛盾

Controller prompt 要求“重大结果后重新咨询 ExpAgent”，但 `allowed_action_candidates()` 只有在 run 尚无 artifact 时才提供自由的初始 `call_exp_agent`。

实验完成后，如果动作图中没有 ExpAgent 分析任务，controller 即使意识到需要分析，也没有合法动作可选。

### 2.5 根因四：finish 缺少科学闭环约束

当前 finish 主要检查 required task 是否完成，却不检查：

- 实验结果是否已经被科学解释；
- 假设是否得到判断；
- 是否存在覆盖该结果的 ExpAgent scientific decision。

因此“执行完成”可能被误当成“科研完成”。

## 3. 非目标与现有能力基线

本次不重写以下能力：

- ReproAgent 的实验执行 loop；
- CodingAgent 的代码修改 loop；
- Conda 环境管理；
- 数据集缓存、pip 缓存、仓库缓存；
- workspace 和 artifact 布局；
- 对话层 Tier 0/1/2 模型。

P4 已确认：

- MNIST 数据集缓存与软链接生效；
- pip 共享缓存目录已注入，但该次 Torch 下载没有命中旧缓存；
- 同一 run 内环境名称传递生效；
- GPU 实验链路生效。

缓存与环境资源的内容寻址、manifest、清理策略属于里程碑二，不与本次科学编排重构混做。

## 4. 设计原则

### 4.1 单一事实来源

模块能力只由模块自己的 `agent.yaml` 声明。ResAgent 加载后同时提供给：

- Chat router；
- ResearchRun controller prompt；
- capability→executor 的确定性解析；
- 输入、输出和副作用校验。

不再在 controller prompt 中维护另一套角色说明。

### 4.2 科学意图与物理执行分离

ExpAgent 输出逻辑动作，不输出：

- executor/module 名称；
- workspace 路径；
- Conda 环境名称；
- copy/external repo 路径；
- 重试次数和运行状态。

ResAgent 根据 capability、能力卡和资源表补全这些执行信息。

### 4.3 确定性闭环优先于 prompt 提醒

“实验后必须分析”不能只写在 prompt 中。应由：

- ExpAgent plan validator；
- ResAgent result-analysis coverage；
- finish gate

共同形成可测试的不变量。

### 4.4 不保留永久兼容层

V2 契约在 ExpAgent 与 ResAgent 的开发分支中同步切换。合并时不保留：

- V1/V2 双解析器；
- 根据自然语言猜旧 action 类型的长期 heuristic；
- `_upgrade_legacy_action_ids()` 一类主线兼容升级逻辑；
- 重复 schema。

若确有外部用户依赖旧接口，应在迁移前确认；否则直接删除。

## 5. 最终职责边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| ExpAgent | 科学问题、实验设计、结果分析、文献检索、失败的科学归因 | 选择具体模块、路径、环境、重试和执行状态 |
| ResAgent | 任务编排、capability 路由、依赖、资源、重试、预算、暂停和完成判定 | 自己做科学分析、改代码或跑实验 |
| CodingAgent | 修改代码并验证补丁 | 解释实验是否支持假设 |
| ReproAgent | 准备实验环境、执行命令、采集日志和原始指标 | 形成最终科学结论 |

## 6. V2 科学动作契约

### 6.1 删除重复的 `type + plan.kind`

以 discriminated union 表达动作，每个动作只有一个 `capability` 判别字段。

公共字段：

```python
class ScientificActionBase(BaseModel):
    action_id: str
    capability: str
    objective: str
    rationale: str
    depends_on: list[str] = []
    project_ref: str = ""
    required: bool = True
    success_criteria: list[str] = []
```

正式 capability 词表：

| capability | 科学语义 | 默认执行模块 |
|---|---|---|
| `modify_code` | 实现或修改实验代码 | CodingAgent |
| `reproduce_experiment` | 从论文/仓库复现方法 | ReproAgent |
| `execute_experiment` | 在已有项目中执行实验 | ReproAgent |
| `analyze_results` | 解释指标、比较方法、判断假设和偏差 | ExpAgent |
| `search_literature` | 检索并分析相关论文 | ExpAgent |
| `ask_user` | 请求必要的人类输入 | ResAgent |

每种 capability 使用独立 Pydantic 子模型保存自身字段。不要退化为一个任意 `dict payload`。

### 6.2 动作示例

```json
[
  {
    "action_id": "run_odenet",
    "capability": "execute_experiment",
    "objective": "运行三轮 ODE-Net MNIST 并记录准确率和运行时间",
    "rationale": "验证官方实现可在当前 GPU 环境中端到端运行",
    "depends_on": [],
    "project_ref": "odenet_mnist",
    "required": true,
    "success_criteria": ["完成 3 epoch", "记录每轮 test accuracy"],
    "requires_gpu": true,
    "expected_metrics": ["train accuracy", "test accuracy", "runtime"]
  },
  {
    "action_id": "analyze_odenet",
    "capability": "analyze_results",
    "objective": "判断结果是否形成有效学习信号并解释与论文配置的偏差",
    "rationale": "有界实验不能直接等同于论文完整复现",
    "depends_on": ["run_odenet"],
    "project_ref": "odenet_mnist",
    "required": true,
    "success_criteria": ["给出结论状态", "列出配置偏差和证据"]
  }
]
```

### 6.3 工程 smoke test

纯工程验证允许跳过科学分析，但必须显式声明：

```python
analysis_required: bool = False
```

默认值为 `True`。不能通过“模型没生成分析任务”隐式跳过。

## 7. 按模块分工与交付

本章是实际开发时的任务入口。可将对应模块小节直接交给该模块的开发 AI；各模块必须共同遵守 §1-§6 的目标、职责边界与 V2 科学动作契约。后文附录保留完整技术细节、跨模块验收和基线信息。

### 7.1 ExpAgent 任务包

**负责方**：ExpAgent 专属开发会话

**开发分支**：`refactor/scientific-action-contract-v2`

**目标**：ExpAgent 只描述科学上要做什么、为什么做以及动作依赖，不输出执行模块、路径、环境和重试状态。

修改范围：

- `src/experiment_designer/models.py`
- `src/experiment_designer/prompts/schemas.py`
- `src/experiment_designer/prompts/system.py`
- `src/experiment_designer/prompts/rendering.py`
- `src/experiment_designer/controller/validator.py`
- `src/experiment_designer/validator.py`
- 相关 presentation/report 序列化代码与测试
- ExpAgent 自己的 `agent.yaml`

必须完成：

1. 用 §6 的 V2 discriminated union 替代 `RecommendedAction + SuggestedPlan`。
2. 保留 `ScientificDecision.recommended_actions`，但将元素切换为 V2 typed action。
3. Prompt 明确区分 `execute_experiment`、`reproduce_experiment` 与 `analyze_results`。
4. Validator 检查 action ID、依赖、DAG、capability、结果分析覆盖和物理字段越界。
5. 对错误计划要求模型修订；达到上限后 fail closed，不用关键词 heuristic 偷偷改写。
6. 更新能力卡，使 `analyze_results`、`search_literature` 等能力与实际接口一致。

必须删除或完成使用审计后删除：

- `RecommendedAction.type` 与 `SuggestedPlan.kind` 的双重判别；
- orchestration 不再使用的 legacy action schema；
- 将科学分析描述成 `run_task` 的 prompt 示例；
- 若 standalone API 无真实调用，则删除旧 `TaskBundle/CodingTask/ReproTask/RunTask`；若仍有调用，则与 orchestration action 明确隔离。

交付物：

- V2 schema、prompt、validator 和序列化实现；
- 一份 ODE-Net 规划 fixture，稳定输出 `execute_experiment → analyze_results`；
- ExpAgent 全量测试结果；
- 提供给 ResAgent 的 V2 `ScientificDecision` 示例 artifact。

模块验收：

- “运行实验并分析偏差”生成 `execute_experiment → analyze_results`；
- `analyze_results` 无结果依赖时被拒绝；
- required 实验无分析覆盖时被拒绝；
- 工程 smoke 显式 `analysis_required=false` 时允许无分析；
- action 不含 executor、workspace、env 或绝对路径。

详细设计见附录 A；跨模块契约以 §6 为准。

### 7.2 ResAgent 任务包

**负责方**：ResAgent 开发会话

**开发分支**：`codex/scientific-orchestration-v2`

**目标**：统一能力来源，把 V2 科学动作转换成内部任务 DAG，并以确定性规则保证实验后的科学分析和正确完成判定。

修改范围：

- `src/resagent/capabilities.py`
- `src/resagent/config.py`
- `src/resagent/controller/planner.py`
- `src/resagent/controller/prompts.py`
- `src/resagent/controller/contracts.py`
- `src/resagent/adapters/expagent/task_conversion.py`
- `src/resagent/models.py`（仅在确有必要时）
- Chat router 使用能力注册表的入口
- 确定性闭环、跨模块 fixture 和云端 acceptance 测试

必须完成：

1. 从配置指定的模块路径加载各模块 `agent.yaml`，形成 Chat 与 ResearchRun 共用的唯一 `CapabilityRegistry`。
2. 校验 capability 唯一归属；缺失或冲突时 fail closed。
3. Controller prompt 动态渲染能力摘要，不再写死团队角色。
4. 将 V2 `ScientificAction.capability` 确定性解析为 executor，再转换成内部 `AgentTask`。
5. 保持 repo/workspace、Conda、artifact materialization、shared/isolated、retry、attempt 和 code repair 的所有权在 ResAgent。
6. 实现 `analysis_coverage()`、缺失分析时的去重兜底任务和 finish gate。
7. `allowed_action_candidates()` 只暴露已登记任务的精确候选，删除无法兑现的自由 re-consult 提示。

必须删除：

- Controller 中硬编码的团队能力说明；
- 与真实模块卡重复的正式 `BUILTIN_CARDS`；
- `_infer_executor()` 中按旧 action 名称维护的主路由；
- `_upgrade_legacy_action_ids()`；
- V1 `type/plan.kind` normalization、fallback 和兼容检查；
- 无法实际执行的“重大结果后自由咨询”规则；
- 被 V2 取代的旧执行契约文档，或明确标记为 superseded。

交付物：

- 统一能力注册表和动态 controller context；
- V2 action→AgentTask 转换；
- analysis coverage、去重 fallback、finish gate；
- 本地确定性闭环和云端 ODE-Net acceptance；
- 四仓库 commit、dirty 状态及测试配置记录。

模块验收：

- 六类 capability 均路由到唯一正确模块；
- Chat router 与 ResearchRun 对同一能力得到同一模块；
- experiment artifact 自动绑定给 `analyze_results`；
- 缺失分析只补一个 ExpAgent task；
- 未分析结果阻止 finish，工程 smoke 不阻止；
- P4 同类流程变为一次 ReproAgent 实验后调用 ExpAgent，不再出现第二个 ReproAgent 报告任务。

详细设计见附录 B-E；清理要求见附录 F。

### 7.3 ReproAgent 任务包

**负责方**：ReproAgent 专属开发会话

**开发分支**：`feat/capability-card-v2`

**目标**：只校准模块能力声明和边界，不重写现有实验执行 loop。

修改范围：

- ReproAgent 的 `agent.yaml`；
- 能力卡加载或校验测试（仅在模块内已有对应机制时）；
- 必要的文档说明。

必须完成：

1. 声明角色为 experiment operator。
2. 声明 `reproduce_experiment` 与 `execute_experiment`。
3. 准确描述输入合同、输出合同和 workspace/environment 副作用。
4. 明确输出是实验 evidence、日志和原始指标，不是最终科学结论。
5. 保持独立 CLI、Experiment Operator、环境与 evidence 合同不变。

不在本次修改：

- 实验执行 agentic loop；
- Conda 环境管理；
- 数据集、pip、Torch 或 repo 缓存；
- workspace/artifact 布局；
- 里程碑二资源管理能力。

交付物与验收：

- 可由 ResAgent registry 成功加载且无 capability 冲突的 `agent.yaml`；
- 独立 CLI 和现有全量测试通过；
- dependency-chain、coding、env-reuse 与 GPU repro 行为不退化。

能力卡格式参考附录 B；本次非目标参考 §3 和附录 L。

### 7.4 CodingAgent 任务包

**负责方**：CodingAgent 专属开发会话

**开发分支**：原则上不开功能分支；若能力卡确需改动，再从 `main` 建短生命周期 `codex/` 分支

**目标**：验证通用编程能力可被统一注册表识别，不修改 CodingAgent 的 agentic loop。

修改范围：

- CodingAgent 的 `agent.yaml`；
- 能力卡加载或校验测试（仅在必要时）；
- 必要的文档说明。

必须完成：

1. 声明 `modify_code` capability。
2. 准确描述输入合同、补丁/报告输出和代码 workspace 副作用。
3. 保持 CodingAgent 可独立使用，不加入 ResAgent 特化逻辑。
4. 保持 clone、环境策略、session bindings 与现有 loop 不变。

交付物与验收：

- 能力卡可由 ResAgent registry 加载且不与其他模块冲突；
- 独立 CLI 和全量测试通过；
- ResAgent 仍只通过适配器和合同调用，不直接修改 CodingAgent 内部实现。

### 7.5 跨模块集成与总体会话

**负责方**：ResAgent 总体会话

**目标**：管理契约切换顺序、跨模块 fixture、最终清理和验收，不跨边界直接修改三个子模块本体。

实施顺序：

```text
Phase 0：四模块基线冻结与旧模型使用审计
    ↓
ExpAgent：V2 schema/prompt/validator
    ↓
ReproAgent / CodingAgent：能力卡校准（可并行）
    ↓
ResAgent：统一 registry、V2 conversion、科学闭环
    ↓
四模块全量测试 + 确定性闭环
    ↓
云端 ODE-Net 验收
    ↓
删除残余 V1 路径并合并
```

集成退出标准：

- 附录 I 的完成定义全部满足；
- 附录 H 的本地与云端验收全部通过；
- V2 是唯一 orchestration 主线，没有永久 V1/V2 双解析；
- 四模块版本和 dirty 状态记录完整；
- 子模块问题由对应会话修改，总体会话负责复核。

## 8. 共享技术附录

以下内容保留原方案的完整技术说明。模块开发 AI 应先阅读自己的任务包，再按引用查阅对应附录。

### 附录 A：ExpAgent 修改方案

#### A.1 Schema

预计修改：

- `src/experiment_designer/models.py`
- `src/experiment_designer/prompts/schemas.py`
- 相关 presentation/report 序列化代码

工作：

1. 用 V2 discriminated union 替代 `RecommendedAction + SuggestedPlan`。
2. 删除 action `type` 与 `plan.kind` 双字段。
3. 删除 orchestration 主线不再使用的 legacy action schema。
4. 保留 `ScientificDecision.recommended_actions` 这个概念，但元素类型切换为 V2。
5. `ExperimentPlan` 的独立 standalone API 若仍被使用，应与 orchestration actions 明确分离；若无调用，删除旧 `TaskBundle/CodingTask/ReproTask/RunTask`。

#### A.2 Prompt

预计修改：

- `src/experiment_designer/prompts/system.py`
- `src/experiment_designer/prompts/rendering.py`

明确规则：

- 执行代码并产生新原始指标 → `execute_experiment`。
- 复现外部论文方法 → `reproduce_experiment`。
- 解释、比较、总结指标或判断 hypothesis → `analyze_results`。
- “生成 deviation report”属于 `analyze_results`，不属于实验执行。
- 不允许输出 module/executor、路径和环境字段。

#### A.3 Validator

预计修改：

- `src/experiment_designer/controller/validator.py`
- `src/experiment_designer/validator.py`

新增确定性规则：

1. action_id 唯一、依赖存在、DAG 无环。
2. capability 必须在允许词表中。
3. `analyze_results` 必须依赖至少一个产生结果的动作。
4. `analysis_required=True` 的终端实验链必须被至少一个 `analyze_results` 覆盖。
5. `analyze_results` 不能包含物理执行字段。
6. `execute_experiment` 必须包含 expected metrics 或 success criteria。

不要通过关键词 heuristic 自动改写错误动作。错误计划返回模型重修；达到修订上限则 fail closed。

#### A.4 ExpAgent 验收

- 给定“运行实验并分析偏差”，输出 `execute_experiment → analyze_results`。
- 给定纯工程 import smoke，允许 `analysis_required=false`。
- 给定 analyze action 无依赖，validator 拒绝。
- 输出中不存在 workspace/env/executor。

### 附录 B：统一能力注册表

#### B.1 能力卡格式

三个执行模块都必须提供 `agent.yaml`。建议结构：

```yaml
name: reproagent
role: experiment_operator
capabilities:
  - reproduce_experiment
  - execute_experiment
side_effects: workspace_and_environment
input_contract: ReproTask
output_contract: AgentState/repro_result
status: available
```

ExpAgent 和 CodingAgent 使用同一 capability 词表。

#### B.2 ResAgent 加载行为

预计修改：

- `src/resagent/capabilities.py`
- `src/resagent/config.py`
- `src/resagent/controller/planner.py`
- `src/resagent/controller/prompts.py`

工作：

1. 启动时从配置指定模块路径加载所有 `agent.yaml`。
2. 校验 capability 是否唯一归属；冲突或缺失直接报告配置错误。
3. Controller prompt 动态渲染 registry 的能力摘要。
4. capability→executor 由 registry 确定性解析，不交给 LLM 猜。
5. Chat router 继续复用同一 registry 实例或同一加载函数。

#### B.3 删除项

迁移完成后删除：

- `CONTROLLER_SYSTEM` 中硬编码的 `Your team` 列表；
- `BUILTIN_CARDS` 中与真实模块卡重复的正式能力定义；
- `_infer_executor()` 中按旧 action 名称维护的硬编码主路由；
- Chat 与 controller 各自维护的角色描述。

允许保留最小错误提示，但不能保留第二套默认能力表。

### 附录 C：ResAgent V2 转换与调度

#### C.1 动作转换

预计修改：

- `src/resagent/adapters/expagent/task_conversion.py`
- `src/resagent/controller/contracts.py`
- `src/resagent/models.py`（仅在确有必要时）

转换流程：

```text
ScientificAction.capability
        ↓ CapabilityRegistry.resolve()
Producer
        ↓ adapter contract
AgentTask
```

`AgentTask.agent`、`AgentTask.kind` 仍可作为 ResAgent 内部执行模型保留。外部科学契约不再暴露这些实现细节。

#### C.2 资源与物理字段

ResAgent 继续负责：

- repo/workspace 解析；
- Conda env 注入；
- artifact materialization；
- shared/isolated 策略；
- retry 和 attempt；
- code repair 调度。

这些逻辑不得移入 ExpAgent。

#### C.3 删除旧转换逻辑

切换完成后删除：

- `_upgrade_legacy_action_ids()`；
- `type` 与 `plan.kind` 一致性兼容检查；
- 按旧 `run_task/repro_task/coding_task` 猜 executor 的代码；
- 对 V1 action 字典的 fallback normalization。

主线只接受 V2 typed action。无效输入清晰失败并保留原始 ExpAgent artifact 供诊断。

### 附录 D：科学结果闭环

#### D.1 Analysis coverage

在 ResAgent 中新增一个小而明确的策略函数，例如：

```python
analysis_coverage(state, experiment_task_id) -> covered | missing | not_required
```

判定依据：

- 实验 task 已完成并产生 `repro_result`；
- 存在 completed ExpAgent task；
- 该任务 capability 为 `analyze_results`；
- depends_on 覆盖对应实验 task；
- ExpAgent 产出 `scientific_decision` artifact。

#### D.2 缺失分析时的兜底

ExpAgent validator 是第一道防线。ResAgent 是第二道防线：

1. 实验完成后检查 coverage。
2. 若 `analysis_required=True` 且没有 planned analysis，创建一个确定性的 ExpAgent analysis task。
3. task fingerprint 由待分析 artifact IDs 组成，确保只创建一次。
4. 记录 DecisionRecord，说明这是 orchestration invariant 修复，而不是 LLM 临时建议。

这不是允许 ResAgent 任意发明科研任务；它只能补齐“分析已完成实验”的闭环节点。

#### D.3 Finish gate

预计修改：

- `src/resagent/controller/contracts.py`

在现有 required task 检查后增加：

```text
存在 analysis_required 的已完成实验
且 analysis coverage 缺失
=> finish 不允许
```

#### D.4 Allowed actions

Controller 不再依赖自由的“重大结果后重新咨询”提示。

- 初始咨询仍可由初始 ExpAgent task 表达。
- 结果分析由 task-bound `call_exp_agent` 表达。
- `allowed_action_candidates()` 只暴露已登记 task 的精确候选。
- 删除无法由候选动作兑现的 prompt 规则。

### 附录 E：主线控制流

```text
1. ResAgent 创建 initial ExpAgent advisory task
2. ExpAgent 返回 V2 scientific action graph
3. ExpAgent validator 验证科学闭环
4. ResAgent 按 capability registry 转换为 AgentTask DAG
5. Controller 选择一个 ready task
6. Adapter 执行并登记 artifact/resource
7. 实验完成后检查 analysis coverage
8. ExpAgent analyze_results 读取依赖 artifact
9. ExpAgent 可返回新的 V2 action graph
10. required tasks + analysis coverage 全满足后允许 finish
```

这是一条循环主线，不新增第二套 workflow engine、黑板或事件总线。

### 附录 F：代码清理清单

实施时必须同步完成以下清理，不留 TODO 式旧路径：

#### F.1 ExpAgent

- 删除 `RecommendedAction.type` 与 `SuggestedPlan.kind` 双重判别。
- 删除 orchestration 不再使用的旧 action schema。
- 审计并删除未被 standalone API 使用的 `TaskBundle` 系列模型。
- 删除将科学分析描述成 run task 的 prompt 示例。

#### F.2 ResAgent

- 删除 controller 硬编码团队能力说明。
- 删除重复内置正式能力卡。
- 删除 V1 action upgrade/normalization 主线。
- 删除旧 action→executor 硬编码推断。
- 删除无法实际执行的“自由 re-consult”规则。
- 更新旧文档，将 `EXECUTION_CONTRACT_V1.md` 标记 superseded 或直接删除。

#### F.3 保留

- `AgentTask` 内部状态模型；
- adapters；
- artifact/resource/session 体系；
- retry、safety、workspace policy；
- ReproAgent/CodingAgent 独立运行能力。

### 附录 G：实施阶段

#### G.1 Phase 0：基线冻结和使用审计

目标：确认哪些旧模型仍被真实入口使用，避免误删独立 API。

工作：

- 搜索 `RecommendedAction`、`SuggestedPlan`、`TaskBundle` 全部调用点。
- 保存当前四模块测试结果。
- 固化 P4 状态机 fixture：当前错误图和期望 V2 图各一份。
- 确认三个模块都有有效 `agent.yaml`。

退出标准：形成明确的删除列表，不以“可能有人用”为理由保留死代码。

#### G.2 Phase 1：ExpAgent V2

目标：ExpAgent 输出单一语义动作契约并保证实验后分析。

工作：schema、prompt、validator、单测、删除 V1 输出路径。

退出标准：ExpAgent 单测通过，ODE-Net 规划稳定输出 `execute_experiment → analyze_results`。

#### G.3 Phase 2：ResAgent 能力注册与 V2 转换

目标：统一 Chat/controller 能力来源并按 capability 路由。

工作：registry、动态 controller context、V2 task conversion、删除硬编码路由。

退出标准：相同 action 在不同入口只经过一套 registry；缺卡/冲突 fail closed。

#### G.4 Phase 3：科学闭环与清理

目标：补 coverage、finish gate、去重兜底，并删除旧主线。

退出标准：没有 V1 parser、没有双能力表、没有未兑现 prompt 规则。

#### G.5 Phase 4：验收

目标：本地确定性测试和云端真实测试。

退出标准见附录 H。

### 附录 H：原模块分工与测试验收

| 工作 | 模块 | 建议负责方 |
|---|---|---|
| V2 scientific action schema/prompt/validator | ExpAgent | ExpAgent 专属开发会话 |
| capability registry、controller、task conversion、finish gate | ResAgent | ResAgent 开发会话 |
| 增加/校准 Experiment Operator `agent.yaml` | reproagent | ReproAgent 专属开发会话 |
| CodingAgent | CodingAgent | 原则上无需修改；只验收能力卡 |
| 跨模块 fixture、确定性闭环、云端 acceptance | ResAgent | 总体会话统一验收 |

ResAgent 不直接修改 ExpAgent/ReproAgent/CodingAgent 本体。跨模块问题以交办文档和验收测试处理。

#### H.1 测试与验收标准

##### H.1.1 ExpAgent 单测

- 实验+解释需求生成 `execute_experiment → analyze_results`。
- `analyze_results` 缺依赖时 validator 拒绝。
- required 实验无分析覆盖时 validator 拒绝。
- smoke test 显式 `analysis_required=false` 时允许无分析。
- action 输出不含 executor/workspace/env/绝对路径。

##### H.1.2 ResAgent 单测

- capability registry 从模块卡解析唯一 executor。
- controller context 包含 registry 内容，不包含硬编码团队表。
- V2 六类 capability 路由正确。
- experiment artifact 自动绑定给 `analyze_results`。
- 缺失分析时只补一个 ExpAgent task。
- 分析完成后 coverage 为 covered。
- 未分析结果阻止 finish。
- smoke test 不阻止 finish。
- Chat router 与 controller 对同一能力得到相同模块。

##### H.1.3 确定性跨模块闭环

预期任务树：

```text
task_001 ReproAgent execute_experiment completed
task_002 ExpAgent   analyze_results    completed
finish
```

断言：

- 只有一个 ReproAgent 执行任务；
- ExpAgent task 依赖 task_001；
- 输入 artifact 路径存在且可读；
- scientific decision 引用实际指标；
- 无 pending/failed/blocked task；
- 无重复分析 task；
- run status 为 completed。

##### H.1.4 云端 ODE-Net 验收

复用 P4 本地仓库和数据集缓存，避免 GitHub 网络成为干扰变量。

必须满足：

- GPU 3 epoch 实验完成；
- ReproAgent 只执行一次实验；
- 后续调用 ExpAgent，不再调用第二个 ReproAgent 报告任务；
- ExpAgent 读取 `result.md` 并输出准确率、配置偏差和结论；
- 不出现 `completed_with_failures`；
- 总任务数和调用次数不膨胀；
- run 正常 finish，无 ask_user 伪阻塞。

##### H.1.5 回归

- 四模块原有单测全部通过；
- dependency-chain、coding、env-reuse 不退化；
- ReproAgent 独立 CLI 和 CodingAgent 独立 CLI 不受影响。

### 附录 I：完成定义

只有同时满足以下条件，重构才算完成：

1. V2 scientific action contract 是唯一 orchestration 输入。
2. 能力卡是 Chat 与 ResearchRun 的唯一能力来源。
3. 实验结果分析是可测试的不变量，而非 prompt 建议。
4. P4 同类任务表现为 ReproAgent→ExpAgent，而非 ReproAgent→ReproAgent。
5. V1 主线、重复卡片、硬编码路由和无效规则已删除。
6. 全量测试和云端 GPU 验收通过。
7. 文档与实际代码一致，不再让新开发者同时理解两套协议。

### 附录 J：推荐实施顺序

```text
Phase 0 使用审计
    ↓
ExpAgent Phase 1
    ↓
ResAgent Phase 2
    ↓
ResAgent Phase 3 + 删除旧主线
    ↓
本地确定性验收
    ↓
云端 ODE-Net 验收
```

不要先在 ResAgent 中增加自然语言 reroute heuristic，也不要先通过扩大 ReproAgent step budget 掩盖问题。正确修复点是科学动作语义、统一能力路由和 finish 闭环。

### 附录 K：P4 稳定基线与分支策略

V2 开发不得继续叠加在 readability 或 experiment-operator 历史功能分支上。先将已经通过 P4 云端验收的版本收口到各仓库默认分支，再建立新的短生命周期分支。

#### K.1 P4 基线来源

| 模块 | 已验收来源分支 | 合并目标 | 基线说明 |
|---|---|---|---|
| ResAgent | `codex/resagent-readability-refactor` | `master` | P3/P4 编排、artifact fan-in、repair loop、路径修复 |
| ExpAgent | `refactor/readability-layout` | `main` | 分层重构、逻辑 action graph、`result_analysis` 基础能力 |
| reproagent | `experiment-operator` | `main` | Experiment Operator、shared workspace、环境与 evidence 合同 |
| CodingAgent | `main` | `main` | clone、env policy、session bindings 已在默认分支 |

ReproAgent 的 `refactor/readability-layout` 已是 `experiment-operator` 的祖先，不单独再合并一次。

#### K.2 收口步骤

1. 四仓库工作区必须干净；开发文档先提交到 ResAgent 功能分支。
2. 默认分支只接受 `--ff-only` 快进合并；若不能快进，停止并审计，不现场制造 merge commit。
3. 合并后运行四模块全量单测。
4. 默认分支推送成功后，统一打 `p4-validated-2026-08-14` 标签。
5. 标签推送并核对后，历史功能分支进入只读状态；V2 不再向这些分支提交。
6. V2 从默认分支新建：

```text
ResAgent:   codex/scientific-orchestration-v2
ExpAgent:   refactor/scientific-action-contract-v2
reproagent: feat/capability-card-v2
CodingAgent: 不开功能分支，仅作为依赖验收
```

#### K.3 跨仓库版本记录

每次云端验收必须记录四个仓库的 commit 与 dirty 状态。P4 标签是本次 V2 的回滚点；V2 acceptance 报告必须同时记录：

- ResAgent commit；
- ExpAgent commit；
- reproagent commit；
- CodingAgent commit；
- 配置文件路径；
- 使用的模型与 mirror profile；
- 本地 repo/cache/env 输入。

不要用“最新版”作为测试版本描述。

### 附录 L：后续里程碑二：环境复用与资源管理（本次不实施）

本节只记录后续方向，避免 V2 科学编排重构夹带环境系统改造。进入里程碑二的前提是附录 I 全部完成。

#### L.1 目标

里程碑二解决的是“相同实验尽量复用、不同依赖安全隔离、所有资源可追踪和可清理”，不是模块职责路由。

目标能力：

1. 基于依赖内容而不是任务名称识别可复用 Conda 环境。
2. 环境登记包含真实路径、创建来源、最后使用者和认证信息。
3. pip/Torch wheel、数据集和仓库缓存具有明确命中证据。
4. 缓存命中、环境复用、环境新建三种状态在报告中分开表达。
5. 支持磁盘配额、引用关系和安全清理。

#### L.2 环境身份

候选环境指纹至少覆盖：

- Python 版本；
- 平台与 CPU 架构；
- CUDA driver/运行时兼容范围；
- torch/tensorflow/jax 及其 CUDA 变体；
- `environment.yml`、`requirements*.txt`、`pyproject.toml`、`setup.py`；
- repo commit 或依赖声明内容 hash。

环境名建议使用：

```text
resenv_<project_slug>_<fingerprint>
```

仅名字相同不能直接复用，必须读取 env manifest 并重新执行轻量审计。

#### L.3 Env manifest

每个环境对应一个机器级 manifest，至少记录：

```yaml
name: resenv_xxx_ab12cd34
path: /root/autodl-tmp/conda-envs-dev/resenv_xxx_ab12cd34
fingerprint: ab12cd34
created_at: ...
last_used_at: ...
python: 3.10
framework: torch==2.6.0+cu124
cuda_driver: ...
repo_origins: []
certification: experiment
audit_artifact: ...
```

ResAgent 的 `ResourceRef` 保存项目内引用；机器级 registry 保存物理环境生命周期。两者不能混成一份全局 `state.json`。

#### L.4 缓存可观测性

ReproAgent session/report 应明确记录：

```text
dataset_cache: hit | miss | unavailable
pip_cache: hit | partial_hit | miss
repo_cache: hit | refreshed | network_clone | local_source
environment: reused | created | repaired
```

判定必须来自日志或 cache API，不能仅因为配置了 `PIP_CACHE_DIR` 就声称命中。

P4 事实基线：MNIST dataset cache 为 hit；pip cache 路径已生效，但 Torch 2.6.0 与 CUDA 依赖在该次运行中重新下载，因此该次不能记为 pip/Torch cache hit。

#### L.5 并发与安全

- 同一 fingerprint 创建环境时使用文件锁；
- 创建失败的半成品环境必须标记并可回收；
- 复用前验证 manifest、关键 import、CUDA availability；
- 环境修复产生新 revision，不静默破坏其他 run 正在使用的环境；
- 删除前检查活动进程与项目引用。

#### L.6 清理策略

- 数据集缓存：默认长期保留，按显式管理命令清理；
- pip/repo cache：按 LRU 和总容量上限清理；
- Conda env：按最后使用时间、引用数和认证状态清理；
- run workspace：由项目归档策略管理，不与机器缓存联动删除；
- 所有清理先 dry-run，输出将删除的绝对路径和预计释放空间。

#### L.7 里程碑二验收

- 两个独立 run 使用相同依赖时，第二个 run 不重新下载 Torch，也不新建环境；
- 依赖或 CUDA 变体变化时创建新环境，不错误复用；
- `ResourceRef.path` 与 manifest 中真实路径一致；
- 原始 `created_task` 不被后续使用覆盖，另记录 `last_used_task`；
- acceptance report 能区分 dataset/pip/repo/env 四类命中；
- 清理 dry-run 和真实清理测试通过。

里程碑二开始前应新建独立设计文档，不直接在本文件继续扩张实施细节。
