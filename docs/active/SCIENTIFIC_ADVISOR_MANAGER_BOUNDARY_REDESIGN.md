# 科学顾问与总管模块边界重构方案

## 1. 目标与约束

这是一份 **ExpAgent + ResAgent 联合改造方案**。

目标：

- ResAgent 始终是最高层总管和唯一用户入口；
- ExpAgent 是独立、无副作用、可复用的科学顾问；
- ExpAgent 只提出科学判断和科学行动图；
- ResAgent 决定是否采纳、由谁执行、在哪里执行以及何时结束；
- CodingAgent 和 ReproAgent 只负责专业执行；
- 系统只保留一套科学规划表示。

本次重构必须遵守以下简洁性规则：

1. **删除优先**：先删除旧主线，再考虑新增结构。
2. **复用现有模型**：现有 `dict` 或 Pydantic 模型能表达时不加包装层。
3. **不为命名而重构**：名称没有造成错误时，不做跨仓重命名。
4. **只修有证据的问题**：新字段必须对应真实信息缺失或明确验收需求。
5. **不顺手扩展范围**：不同时重构环境、资源、插件或其他稳定模块。
6. **无长期兼容层**：调用者迁移后直接删除旧代码。
7. **净复杂度下降**：模型数、转换步骤和生产代码原则上应减少。

## 2. 架构选择

采用 **Manager / Subagents-as-Tools** 模式，不采用专业 Agent 平级 handoff。

参考：

- OpenAI manager pattern：一个 manager 保持用户控制并调用专业 Agent；
- LangGraph subagents pattern：所有专业调用返回主 Agent；
- Anthropic orchestrator-workers：中央协调器分解、委派并综合结果；
- AI Scientist v2：围绕提议、实验和证据迭代，但本项目不照搬其树搜索复杂度。

链接：

- https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/
- https://www.anthropic.com/engineering/building-effective-agents
- https://github.com/SakanaAI/AI-Scientist-v2

目标流程：

```text
用户
  -> ResAgent 对话层（唯一入口）
       -> 直接回答；或
       -> 咨询 ExpAgent
            -> ScientificDecision + recommended_actions
       <- 控制权返回 ResAgent
       -> 采纳 / 推迟 / 拒绝 / 请求用户确认
       -> capability registry 匹配执行模块
       -> 创建和调度 AgentTask
            -> CodingAgent / ReproAgent
       <- 产物、状态、失败
       -> 需要科学解释时再次咨询 ExpAgent
       -> ResAgent 决定继续、暂停或结束
       -> ResAgent 回复用户
```

即使首个实际动作是科学咨询，入口也仍是 ResAgent。ExpAgent 不接管会话，也不直接调用其他模块。

## 3. 模块边界

### 3.1 ResAgent：最高层总管

ResAgent 负责：

- 用户对话和会话持久化；
- 判断消息是问答、讨论、规划、执行、恢复还是结束；
- 决定是否咨询专业模块；
- 决定是否采纳 ExpAgent 建议；
- capability 到具体模块的匹配；
- action 到运行任务的转换；
- workspace、repo、environment、cache 和资源管理；
- dependency、attempt、retry、pause、resume 和 cancel；
- artifact 与 provenance；
- 是否再次咨询 ExpAgent；
- finish gate 和最终用户回复。

ResAgent 可以知道具体模块名和 Adapter，因为这正是总管职责。

### 3.2 ExpAgent：独立科学顾问

ExpAgent 负责：

- 理解研究问题和已有证据；
- 提出或评估假设；
- 设计 baseline、control、变量、指标和判断标准；
- 文献检索和现状分析；
- 结果解释和科学失败诊断；
- 输出一张逻辑科学行动图；
- 根据新证据修订或替换旧行动。

ExpAgent 不得输出：

- 具体 Agent 名；
- 其他模块 API/CLI；
- workspace、Conda 环境、缓存或绝对路径；
- attempt、retry、lease 或调度策略。

ExpAgent 应能独立安装和调用：

```text
advise(AdvisorContext) -> ScientificDecision
```

不要求安装 ResAgent、CodingAgent 或 ReproAgent。

### 3.3 CodingAgent：代码专家

在调用者提供的 workspace 中解释、创建、修改和验证代码。它不决定科学方向，不调用其他专业模块。

### 3.4 ReproAgent：实验执行者

负责仓库、环境、命令、实验、日志、指标和证据。由 ResAgent 调用时，代码修改需求应作为结构化状态返回，不直接调用 CodingAgent。

## 4. 唯一科学规划主线

### 4.1 删除双主线

当前 ExpAgent 同时存在：

```text
ExperimentPlan -> TaskBundle -> CodingTask/ReproTask/RunTask

recommended_actions -> 反向投影到上述旧模型
```

这会造成两套表示、字段丢失、CLI 与集成接口分叉，并让 ExpAgent 了解具体执行接口。

调用者迁移后删除：

- `ExperimentPlan` 及只服务于它的模型；
- `TaskBundle`；
- `CodingTask`、`ReproTask`、`RunTask`；
- `plan()`、`revise()`；
- `_populate_tasks_from_actions()` 和提取函数；
- 旧链专属 validator、renderer、export、test 和文档；
- 调用旧 API 的 CLI/REPL 路径。

不新增 `ExperimentDesign`、`ResearchPlan` 或其他替代计划。`recommended_actions` 本身就是唯一行动图。

### 4.2 保留 recommended_actions 名称

不把 `recommended_actions` 重命名成 `actions`。

原因：

- 它准确表达“科学顾问的建议”，而不是强制调度命令；
- 改名不能增加能力，却会扩大 ExpAgent、ResAgent、fixture 和测试改动；
- 用户强调 ResAgent 有最终决定权，`recommended` 反而更符合边界。

目标输出：

```text
ScientificDecision
  summary
  confidence
  conclusion
  evidence
  result_analysis
  failure_diagnosis
  recommended_actions
  supersedes_action_ids
  analysis_required
  risks
  needs_user_input
  questions
```

其中：

- `result_analysis` 是本次已经完成的分析；
- `failure_diagnosis` 是本次已经完成的诊断；
- `recommended_actions` 是未来建议；
- 它们不是多套计划。

## 5. ScientificAction 契约

### 5.1 capability 是能力，不是模块名

保留现有能力词表：

- `modify_code`
- `reproduce_experiment`
- `execute_experiment`
- `analyze_results`
- `search_literature`
- `ask_user`

ExpAgent 只输出 capability。ResAgent 从 capability registry 查找当前 provider。

不增加另一套 `action_type`。否则又要维护 action_type -> capability 转换。

### 5.2 复用现有公共字段

现有公共字段已经合理：

- `action_id`
- `capability`
- `objective`
- `rationale`
- `depends_on`
- `project_ref`
- `required`
- `success_criteria`

本阶段不创建通用 `ExperimentSpec`，也不批量增加 protocol、seed、repetition 等字段。

### 5.3 最小补强科学语义

当前 action 已有 `objective`、`rationale`、`success_criteria`、`expected_metrics` 和 `constraints`。先通过 Prompt 和 validator 要求模型把以下内容写入现有字段：

- 假设；
- baseline/control；
- 公平性约束；
- 指标；
- 可测判断标准；
- 复现目标和允许偏差。

只有真实测试反复证明现有字段无法稳定表达某类信息时，才增加最小字段。例如实验组表达持续失败时，只考虑：

- `hypothesis: str`
- `conditions: list[str]`
- `controls: list[str]`

不得一次性扩展成大型实验 schema。

`verify_commands` 是否退役不与本次双主线清理绑定。先保留现有契约；若真实测试证明 ExpAgent 因此越权规定执行细节，再单独处理。

模型注释和 Prompt 应使用“调用者”“总管”“能力提供者”等抽象称呼，不出现具体 Agent 名。

## 6. ResAgent 的 action -> task 转换

现有 `AgentTask` 已有通用 `input: dict`，不新增 `ScientificTask`、`RuntimeBinding`、`ActionSnapshot` 等模型。

最小修改：在现有 `_task_input()` 返回值中保留原始 action。

```text
AgentTask
  action_id
  capability
  source
  input
    scientific_action: dict(action)
    现有扁平模块字段
    workspace_path 等运行绑定
  status / attempts / artifacts
```

规则：

1. 增加一行等价逻辑保存 `scientific_action`，避免字段投影丢失；
2. 保留现有扁平输入，避免同时重写所有 Adapter；
3. workspace 等运行字段不修改嵌套的原始 action；
4. 继续复用现有 task/source/artifact 追溯链，不新增 provenance 模型；
5. optional recommendation 继续留在决策产物中，不自动创建任务；
6. capability 无唯一 provider 时继续 fail closed。

这是运行记录，不是第二套计划。

## 7. 完整工作流

### 7.1 普通对话

```text
用户 -> ResAgent
     -> 直接回答；或
     -> 按需咨询 ExpAgent/CodingAgent
     -> ResAgent 汇总回复
```

咨询本身不自动启动完整 run。

### 7.2 科研任务

```text
1. 用户向 ResAgent 提出目标或 idea
2. ResAgent 决定咨询 ExpAgent
3. ExpAgent 返回 ScientificDecision(recommended_actions)
4. ResAgent 验证行动图和 capability
5. ResAgent 展示、采纳或请求确认
6. ResAgent 创建运行任务
7. ResAgent 调度专业模块
8. 专业模块返回产物和状态
9. ResAgent 按行动图发起结果分析
10. ExpAgent 返回新科学判断
11. ResAgent 决定继续、询问用户或结束
12. ResAgent 输出最终回复和报告
```

系统仍是 agentic loop。图合法性、依赖、能力所有权、资源安全、用户暂停、budget 和 finish gate 由确定性代码保证。

## 8. 当前代码判断

### 已经正确

- ResAgent conversation loop 是统一入口；
- ResAgent 创建初始科学咨询任务；
- ExpAgent `agent.yaml` 是 `side_effects: none`；
- ExpAgent 输出 capability、依赖、目标、依据和成功标准；
- Prompt 已禁止输出路径和环境；
- ResAgent registry 解析 capability owner；
- ResAgent Adapter 已把行动图转成受管任务。

### 需要修复

- `ScientificDecision` 仍有 `experiment_plan`；
- ExpAgent planner 仍反向生成旧任务模型；
- ExpAgent CLI/REPL 仍走 `plan()/revise()`；
- ExpAgent 注释和 Prompt 示例仍出现具体模块名；
- 旧任务模型按具体执行接口设计；
- `_task_input()` 对未来新增科学字段会产生投影丢失风险。

### 本次明确不做

- 不重命名 `recommended_actions`；
- 不新增 action/task 包装模型；
- 不批量扩充科学 schema；
- 不重构 capability registry；
- 不改环境和资源管理；
- 不重构 CodingAgent/ReproAgent 主线。

## 9. 按模块实施

### 9.1 ExpAgent（主要工作）

**E0：测试锁定**

- 独立 import/API 测试；
- `advise()`、CLI、REPL 行为测试；
- 代表性 scientific action 测试；
- action 输出不含 executor/path/env 字段测试。

不建议用全仓字符串 grep 禁止所有模块名，因为文档或集成说明可以合法提及。只验证公共输出和系统 Prompt 的行为边界。

**E1：删除 ExperimentPlan 输出**

- 从 `ScientificDecision` 删除 `experiment_plan`；
- 更新 schema、Prompt、validator、report 和测试；
- 保留 `recommended_actions` 及其现有字段；
- 清理公共模型注释和系统 Prompt 中不必要的具体 executor 映射。

**E2：删除旧规划线**

- CLI/REPL 统一调用 `advise()`；
- 直接展示 `ScientificDecision.recommended_actions`；
- 通过旧决策、新证据和 `supersedes_action_ids` 修订行动图；
- 删除 `plan()/revise()`、旧模型和反向投影；
- 删除前做四仓引用检查。

**E3：独立验收**

- 问答不生成行动；
- idea 讨论可继续澄清；
- 实验设计生成完整行动图；
- 结果分析不递归生成同证据分析任务；
- API、CLI、REPL 走同一主线。

### 9.2 ResAgent（必要但较小）

**R0：删除旧字段消费**

- 继续消费 `recommended_actions`；
- 删除 `experiment_plan` fixture/兼容处理；
- 不改 capability registry 主线。

**R1：防止科学字段丢失**

- `_task_input()` 保存原始 `scientific_action`；
- 现有扁平字段和 Adapter 保持不变；
- 增加 action -> task 原文保留测试。

**R2：边界验收**

- conversation 仍是唯一入口；
- 专业调用后控制权回到 ResAgent；
- orchestrated mode 禁止子模块互调；
- optional action 不自动执行、不阻塞 finish。

### 9.3 CodingAgent / ReproAgent

原则上不改生产代码。只运行公共接口和组合回归测试。仅当 ResAgent Adapter 输入变化导致真实兼容问题时做最小修复。

## 10. 实施顺序

1. 从已验证默认分支创建 ExpAgent 和 ResAgent 协调分支；
2. 先补 E0 和 action -> task 保留测试；
3. ExpAgent 删除 `experiment_plan`；
4. ResAgent 删除对应旧消费；
5. ExpAgent CLI/REPL 迁移到 `advise()`；
6. 引用检查后删除旧模型和 planner；
7. 跑 ExpAgent/ResAgent 全量单测；
8. 跑四仓全量单测；
9. 跑确定性 code -> experiment -> analysis -> finish；
10. 跑一次有界真实云端 E2E；
11. 合并并打验证 tag。

推荐分支：

- ExpAgent：`codex/scientific-action-mainline`
- ResAgent：`codex/scientific-action-mainline`

CodingAgent/ReproAgent 无实际修改时不开分支。

## 11. 验收标准

### 简洁性

- 生产模型、转换步骤和代码行数净减少；
- 没有新增编排框架、包装模型或兼容层；
- `recommended_actions` 是唯一未来工作表示；
- 没有 `ExperimentPlan`、`TaskBundle` 和旧 planner 主线；
- CLI、REPL、Python API 共用 `advise()`。

### 边界

- ResAgent 是组合系统唯一用户入口；
- ExpAgent 可独立安装调用；
- ExpAgent 输出不含 executor/path/env；
- capability 匹配只发生在 ResAgent；
- 子模块在 orchestrated mode 不直接互调。

### 信息完整性

- 科学行动包含目标、依据和可测成功标准；
- Prompt 要求实验说明覆盖假设、对照、公平性和指标；
- action -> task 后 `input.scientific_action` 与原 action 一致；
- 产物可通过现有 source/action/task 链追溯；
- optional action 不自动执行。

### 测试

- 四仓单测全绿；
- ExpAgent 独立 API/CLI 测试全绿；
- ResAgent conversation/controller 测试全绿；
- graph/dependency/fan-in/analysis/finish 测试全绿；
- 一次有界真实 E2E 完成 code -> experiment -> analysis -> finish；
- provenance 记录准确 commit，工作区无未解释源码改动。

## 12. 完成定义

用户从 ResAgent 进入系统，ResAgent 按需咨询独立 ExpAgent，ExpAgent 返回唯一的 `recommended_actions` 科学行动图，ResAgent 选择并匹配执行模块，执行结果返回 ResAgent 后再交给 ExpAgent 分析，最终由 ResAgent 决定继续或结束。整个过程中不存在旧 `ExperimentPlan`、第二套任务规划、executor 知识泄漏或 action 投影丢失，同时代码量和概念数量较修改前下降。

## 13. 执行状态（2026-08-23）

### ExpAgent（`codex/scientific-action-mainline`）
- [x] E0：边界锁测试（`d0dcb4d`）
- [x] E1+E2：折叠单主线（`44c8d55`）——删除 `ExperimentPlan`/`TaskBundle`/`CodingTask`/`ReproTask`/`RunTask`/`plan()`/`revise()` 及反向投影；`advise()` 成为唯一入口
- [x] 术语抽象（`b2cf202`）

### ResAgent（`codex/scientific-action-mainline`）
- [x] R0：删除 `experiment_plan` 消费（`ArtifactType` 枚举值 + mock 键 + `_map_artifact_type` 条目）
- [x] R1：`_task_input()` 保存 `scientific_action` 原文 + 回归测试
- [x] R2：边界验收（optional 不自动执行/不阻塞 finish，已有测试覆盖）
- [x] P0：删除 `existing_plan=None`（`advise_adhoc` + `_build_advisor_context`）+ Strict AdvisorContext 契约测试

### 待办
- [ ] 两仓全量单测 + 确定性系统测试
- [ ] `codex/scientific-action-mainline` 两分支显式 push（无远端跟踪关系）
- [ ] 有界真实 E2E（code → experiment → analysis → finish）
- [ ] 合并 + 打验证 tag
