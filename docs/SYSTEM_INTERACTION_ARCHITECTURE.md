# System Interaction Architecture Proposal

## 1. 背景

当前系统已经有三个相对成熟的模块：

```text
ExpAgent      科学顾问 / Scientific Advisor
ReproAgent    论文复现实验工程师 / Reproduction Agent
CodingAgent   repo/workspace 级代码 agent / Coding Agent
```

当前大的系统设想仍然是：

```text
ResAgent = 项目经理 / 顶层 orchestrator
ExpAgent = 科学顾问
ReproAgent = 复现实验执行者
CodingAgent = 代码执行者
```

但是在真实科研使用场景中，用户输入并不总是一个明确任务。用户可能会：

```text
问代码问题
问论文/方法原理
讨论一个模糊 idea
要求继续已有实验
要求执行某个复现实验
要求解释失败日志
要求分析实验结果
要求规划下一步研究
```

因此，如果 ResAgent 直接把所有输入都当成 research workflow task，会变得不灵活，也不符合真实使用方式。

核心问题不是单个模块能力不足，而是整个系统缺少统一的“交互入口”和“意图分流协议”。

## 2. 核心判断

不要让用户输入直接进入科研任务流。真实系统需要先做 intake/router。

推荐总结构：

```text
User
  -> ResAgent Conversation Layer / IntakeRouter
      -> direct answer
      -> call ExpAgent for scientific discussion
      -> call CodingAgent for code question or code task
      -> create ResearchRun
      -> continue ResearchRun
      -> dispatch ReproAgent/CodingAgent execution tasks
```

也就是说，ResAgent 应该分成两层：

```text
ResAgent Conversation Layer
  负责理解用户这句话到底是什么类型，是否需要进入正式科研项目状态。

ResAgent Project Orchestrator
  负责真正调度 ExpAgent / CodingAgent / ReproAgent，维护任务和 artifact。
```

一句话：

```text
不是所有用户输入都应该进入 research workflow。
先分诊，再决定是否进入 workflow。
```

## 3. ConversationState 与 ResearchState 分离

当前最重要的设计建议是把“对话状态”和“科研项目状态”分开。

### 3.1 ConversationState

ConversationState 处理临时对话和交互上下文。

适合记录：

```text
用户最近在问什么
当前讨论主题
是否绑定某个 active research run
最近几轮意图分类
临时文件/代码/论文引用
尚未确认的澄清问题
```

示例：

```yaml
conversation_id: conv_001
active_run_id: null
current_topic: "讨论一个关于 channel attention 的模糊 idea"
recent_intents:
  - kind: idea_discussion
    target: expagent
    confidence: medium
scratch_summary: "用户想讨论一种新的 attention 结构，但尚未决定启动实验。"
```

很多用户输入只应该更新 ConversationState，不应该污染 ResearchState。

### 3.2 ResearchState

ResearchState 只用于正式科研项目推进。

适合记录：

```text
research_goal
tasks
artifacts
decisions
observations
budget
run status
```

示例：

```yaml
run_id: res_20260808_001
research_goal: "验证新的 channel attention 结构是否提升 CIFAR-10 分类"
status: running
tasks: []
artifacts: []
decisions: []
```

只有当用户明确要“开始做项目 / 规划实验 / 执行任务 / 继续已有项目”时，才创建或修改 ResearchState。

## 4. IntakeRouter

ResAgent 需要一个 IntakeRouter，专门处理自然语言输入。

输出一个结构化意图：

```yaml
intent: idea_discussion
confidence: medium
target: expagent
requires_research_run: false
requires_confirmation: false
suggested_action: call_exp_agent_advisory
rationale: "用户在讨论模糊研究想法，不应立即创建正式实验任务。"
clarification_questions: []
```

推荐意图类型：

```text
scientific_question       科学/原理问题
code_question             代码理解问题
idea_discussion           模糊 idea 讨论
start_research_run        明确开始一个科研项目
continue_research_run     继续已有项目
execute_task              明确执行某个任务
artifact_analysis         分析已有日志/结果/patch
failure_diagnosis         解释失败
write_paper               未来论文写作入口
ask_clarification         需要澄清
smalltalk_or_meta         普通交流/系统说明
```

推荐 target：

```text
resagent
expagent
codingagent
reproagent
paperagent_future
```

## 5. 用户输入示例与路由

### 5.1 科学原理问题

用户：

```text
这个 attention 机制为什么可能有效？
```

不应该创建 ResearchRun。

路由：

```yaml
intent: scientific_question
target: expagent
requires_research_run: false
suggested_action: call_exp_agent_advisory
```

ExpAgent 可以检索论文或直接回答。

### 5.2 代码问题

用户：

```text
这个 repo 里面 loss 是怎么算的？
```

不应该启动 ReproAgent。

路由：

```yaml
intent: code_question
target: codingagent
requires_research_run: false
suggested_action: call_coding_agent_read_only
```

当前 CodingAgent 主要偏代码修改，未来可能需要 read-only/explain mode。

### 5.3 模糊 idea 讨论

用户：

```text
我想把 diffusion 用到时间序列异常检测，你觉得有戏吗？
```

不应该马上变成实验任务。

路由：

```yaml
intent: idea_discussion
target: expagent
requires_research_run: false
suggested_action: call_exp_agent_discuss
```

ExpAgent 输出科学判断、相关论文、可能方向和风险。

### 5.4 明确启动项目

用户：

```text
按这个 idea 开始做实验规划。
```

应该创建 ResearchRun。

路由：

```yaml
intent: start_research_run
target: resagent
requires_research_run: true
suggested_action: create_run_then_call_exp_agent
```

### 5.5 继续已有项目

用户：

```text
继续上次那个 attention 实验。
```

路由：

```yaml
intent: continue_research_run
target: resagent
requires_research_run: true
suggested_action: load_active_run_and_plan_next_step
```

ResAgent 加载 state，判断 pending/failed/completed tasks。

## 6. Capability Descriptor

为了避免 ResAgent 硬编码所有模块能力，建议每个模块提供一个 capability descriptor。

示例：

```yaml
name: ExpAgent
role: scientific_advisor
handles:
  - scientific_question
  - idea_discussion
  - experiment_design
  - result_analysis
  - failure_scientific_diagnosis
outputs:
  - scientific_decision
  - experiment_plan
  - recommended_actions
entrypoints:
  python_api: experiment_designer.advisor:advise
  cli: expagent advise
```

CodingAgent 示例：

```yaml
name: CodingAgent
role: coding_agent
handles:
  - code_question
  - code_inspection
  - code_modification
outputs:
  - code_explanation
  - patch_report
  - diff
entrypoints:
  python_api: coding_agent:run_code_task
```

ReproAgent 示例：

```yaml
name: ReproAgent
role: reproduction_agent
handles:
  - reproduction_task
  - baseline_run
  - reproduction_failure_repair
outputs:
  - repro_result
  - logs
  - cloned_repo
entrypoints:
  cli: reproagent run
```

Capability Descriptor 可以先写在 ResAgent 配置中，后续再让每个模块自己提供。

## 7. 对现有模块的接口影响

### 7.1 ExpAgent

当前 ExpAgent 已经是 Scientific Advisor agentic loop，输入 `AdvisorContext`，输出 `ScientificDecision`。

但为了更适配真实交互，可能需要支持更明确的 advisor mode：

```text
answer_scientific_question
discuss_idea
design_experiment
analyze_result
revise_plan
diagnose_failure
```

原因：不是每次调用 ExpAgent 都需要完整 experiment_plan 或 recommended_actions。

例如用户只是问原理时，ExpAgent 应该可以输出科学解释，而不是强行输出 `supported/not_supported` 风格的研究结论。

可能需要新增或扩展：

```text
AdvisorContext.mode
ScientificDecision.response_type
ScientificDecision.explanation
```

### 7.2 CodingAgent

当前 CodingAgent 更偏 code modification。

真实用户经常问代码理解问题：

```text
这个 repo 训练入口在哪？
loss 怎么算？
模型结构在哪里定义？
这个错误可能是哪行导致的？
```

因此未来 CodingAgent 可能需要 read-only / explain mode。

建议能力：

```text
CodeQuestionSpec
run_code_question()
```

输出：

```text
CodeExplanation
  answer
  evidence_files
  relevant_snippets
  uncertainty
```

这属于 CodingAgent 项目的接口优化，不应该由 ResAgent 直接修改 CodingAgent。

### 7.3 ReproAgent

ReproAgent 主要处理明确复现任务。

对真实交互来说，ReproAgent 不应该被用来回答普通问题；只有当 intent 是 reproduction_task / baseline_run 时才调用。

## 8. ResAgent 新架构建议

推荐结构：

```text
src/resagent/
  main.py
  models.py
  conversation.py        # ConversationState
  intake.py              # IntakeRouter
  capabilities.py        # module capability registry
  controller.py          # project orchestration loop
  state.py               # ResearchState persistence
  context.py             # context building for router/controller
  context_policy.py      # context selection/truncation
  prompts.py             # router/controller/failure prompts
  adapters/
    expagent.py
    codingagent.py
    reproagent.py
  report.py
```

`intake.py` 和 `conversation.py` 应该是一等公民，而不是后期补丁。

## 9. 第一版 ResAgent MVP 调整

原本的 MVP 可能是：

```text
输入 idea -> 调 ExpAgent -> 生成任务 -> 执行
```

现在建议改成：

```text
输入任意用户消息
  -> IntakeRouter 分类
  -> 如果是非执行类问题：回答或调用对应 advisory module，不创建 ResearchRun
  -> 如果是项目推进类请求：创建/加载 ResearchRun
  -> 调 ExpAgent / CodingAgent / ReproAgent
  -> 登记 artifacts
```

第一版可以只实现：

```text
resagent chat
resagent init
resagent status
```

其中 `chat` 支持：

```text
科学问题 -> ExpAgent
模糊 idea -> ExpAgent
明确开始项目 -> 创建 ResearchRun + ExpAgent
代码问题 -> 暂时提示 CodingAgent read-only mode 未实现，生成 integration request
```

## 10. 与优秀实现的对应关系

从 Claude Code / Codex / OpenHands / SWE-agent / LangGraph 这些系统中可以借鉴几个共同点：

```text
1. 所有用户输入不等于执行任务，需要 plan/confirm/route。
2. 需要持久化 event/action/observation，而不是只存最终答案。
3. 执行工具和 reasoning/controller 要分层。
4. 需要 human-in-the-loop：长任务、危险任务、预算任务要确认。
5. 需要 workspace/artifact 管理，让系统能恢复、回溯、继续。
6. 上下文应该从 state 重建，而不是无限堆聊天历史。
```

这些原则应体现在 ResAgent 中，而不是让 ExpAgent/CodingAgent/ReproAgent 各自重复实现。

## 11. 最关键结论

当前问题不是“ResAgent 不够强”，而是系统需要新增统一交互层：

```text
InteractionRouter + ConversationState + Capability Descriptor
```

最终架构应该是：

```text
User
  -> ResAgent Conversation Layer
      -> IntakeRouter
      -> ConversationState
      -> Capability Registry
      -> Direct/Advisory response
      -> ResearchRun Orchestrator when needed
            -> ExpAgent
            -> CodingAgent
            -> ReproAgent
```

这样既能适配真实用户随意提问，又能在用户明确推进科研项目时进入严肃、可审计、可恢复的科研 workflow。
