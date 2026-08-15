# ResAgent 项目章程

**状态**：常驻的模块边界与设计原则。当前任务、现行契约和历史档案的唯一入口是 [`docs/README.md`](docs/README.md)。本文中的 MVP 示例仅用于解释边界，不代表当前开发进度。

## 1. 模块定位

ResAgent 是完整科研 agent 系统的最高层级模块，定位是：

```text
ResAgent = Research Project Manager / Orchestrator
```

它不是科学顾问，不是代码 agent，也不是复现实验 agent。它负责把已有模块组织成一个可持续运行、可审计、可恢复的科研项目流程。

当前已有模块：

```text
ExpAgent      科学顾问 / 实验科学家
ReproAgent    实验操作员（环境准备、实验执行与证据记录）
CodingAgent   程序员 / repo-local coding agent
```

ResAgent 的核心职责：

```text
维护全局 research_state
调用 ExpAgent 做科学判断
调用 CodingAgent 做代码修改
调用 ReproAgent 做论文/仓库复现
登记所有 artifacts
管理任务状态、依赖、预算、失败、重试和人工确认
决定下一步调用哪个模块
```

一句话边界：

```text
ExpAgent 负责“科学上怎么想”。
ResAgent 负责“系统上怎么做”。
```

## 2. 不做什么

MVP 阶段 ResAgent 不做：

```text
不直接写代码
不直接改 repo
不直接复现论文
不直接训练模型
不直接检索论文
不直接写论文正文
不替代 ExpAgent 做详细科学判断
```

对应职责归属：

```text
科学判断 / 论文检索 / 实验策略     ExpAgent
代码实现 / 代码修改 / repo 验证     CodingAgent
论文复现 / baseline 复现           ReproAgent
论文写作                           未来 PaperAgent
```

ResAgent 可以判断“现在是否该调用某个模块”，但不亲自执行该模块的专业任务。

## 3. 设计原则

### 3.0 Ownership boundary: only modify ResAgent

ResAgent 开发会话只能修改 ResAgent 自己的代码和文档。

严禁在 ResAgent 开发过程中直接修改这些项目：

```text
/home/cyl/CodingAgent
/home/cyl/reproagent
/home/cyl/ExpAgent
```

如果 ResAgent 集成过程中发现下游模块缺能力、有 bug、接口不稳定、输出格式不够用，ResAgent 应该在自己的 `docs/` 下生成问题报告，而不是直接修改下游项目。

报告命名建议：

```text
docs/CODINGAGENT_INTEGRATION_REQUEST.md
docs/REPROAGENT_INTEGRATION_REQUEST.md
docs/EXPAGENT_INTEGRATION_REQUEST.md
```

报告内容至少包括：

```text
问题现象
复现步骤
ResAgent 期望的稳定接口/字段/行为
当前下游模块实际表现
建议在对应模块中修改的位置或方向
临时 workaround（如果有）
```

然后由用户切换到对应模块会话完成修改，再回到 ResAgent 同步或适配。

### 3.1 Artifact first

所有模块输出必须被登记为 artifact。

```text
没有登记 artifact，就等于系统不知道这件事发生过。
```

Artifact 示例：

```yaml
artifacts:
  - id: exp_decision_001
    type: scientific_decision
    producer: ExpAgent
    path: expagent/decision_001/scientific_decision.yaml
    summary: "ExpAgent proposed a bounded MNIST baseline and one coding task."

  - id: code_patch_001
    type: code_patch
    producer: CodingAgent
    path: codingagent/code_001/diff.patch
    summary: "Added loss logging without changing training semantics."

  - id: repro_result_001
    type: repro_result
    producer: ReproAgent
    path: reproagent/repro_001/result.md
    summary: "Reproduced torchdiffeq bounded MNIST run, test acc 99.02%."
```

### 3.2 ResAgent owns state, not expertise

ResAgent 要知道：

```text
谁被调用了
输入是什么
输出在哪里
任务状态是什么
失败原因是什么
下一步候选动作是什么
```

ResAgent 不需要自己知道：

```text
哪个 baseline 科学上最合理
代码应该怎么改
某个论文 repo 应该怎么配环境
```

这些交给专家模块。

### 3.3 Adapter boundary and flexible module paths

ResAgent 通过 adapter 调用下游模块：

```text
adapters/expagent.py
adapters/codingagent.py
adapters/reproagent.py
```

ResAgent 不直接依赖下游模块内部实现。每个 adapter 只暴露稳定接口。

ResAgent 与三个下游模块的关系参考现在 ReproAgent 与 CodingAgent 的关系：

```text
可以在 ResAgent 仓库内保留/同步下游模块的 vendored source copy，方便离线审阅和必要 fallback。
但运行时调用路径必须是灵活配置的，不能写死到某个固定绝对路径。
```

路径解析优先级必须是：

```text
CLI arg > environment variable > config file > importable package > vendored fallback
```

建议环境变量：

```text
EXPAGENT_PATH=/home/cyl/ExpAgent
REPROAGENT_PATH=/home/cyl/reproagent
CODINGAGENT_PATH=/home/cyl/CodingAgent
```

建议 CLI 参数：

```text
--expagent-path /home/cyl/ExpAgent
--reproagent-path /home/cyl/reproagent
--codingagent-path /home/cyl/CodingAgent
```

规则：

```text
不要在 adapter 里写死 /home/cyl/ExpAgent、/home/cyl/reproagent、/home/cyl/CodingAgent。
这些只能作为 config.example 或文档中的示例默认路径。
```

### 3.4 Agentic loop, small action space

ResAgent 最终可以是 agentic loop，但 action space 要小而清楚。

第一版建议 action：

```text
call_exp_agent
call_coding_agent
call_repro_agent
classify_failure
ask_user
mark_task_done
mark_task_blocked
finish
```

不要一开始做太多工具。

## 4. 总体工作流

ResAgent 的高层循环：

```text
observe research_state
  -> decide next orchestration action
  -> call one module or update state
  -> collect observation/artifact
  -> update research_state
  -> repeat
```

典型科研流程：

```text
用户输入 research idea
  -> ResAgent 创建 research run workspace
  -> ResAgent 调 ExpAgent 分析当前 situation
  -> ExpAgent 输出 ScientificDecision + recommended_actions
  -> ResAgent 把 recommended_actions 转成 task queue
  -> ResAgent 根据预算/依赖/风险决定执行顺序
  -> ResAgent 调 CodingAgent 或 ReproAgent
  -> ResAgent 登记 artifacts
  -> 成功后调 ExpAgent analyze_results / revise_plan
  -> 失败后 ResAgent 先判断是否 transient/system failure
      -> 网络/临时下载失败: ResAgent 直接重试或换镜像
      -> 科学问题/实验设计问题: 调 ExpAgent 分析
      -> 代码问题: 调 CodingAgent 或请求用户
  -> 循环直到完成、阻塞或用户停止
```

## 5. 和三个模块的关系

### 5.1 ExpAgent

ExpAgent 是科学顾问。

ResAgent 调用 ExpAgent 的典型场景：

```text
初始 idea 分析
实验方案设计
结果分析
失败诊断中的科学问题分析
实验方案修订
是否需要更多 baseline / ablation / literature search
```

ExpAgent 输入：

```text
AdvisorContext
  situation
  artifacts
  existing_plan
```

ExpAgent 输出：

```text
ScientificDecision
  conclusion
  evidence
  experiment_plan
  recommended_actions
  risks
  needs_user_input
```

重要边界：

```text
ExpAgent 可以内部检索论文。
ExpAgent 不能调用 CodingAgent / ReproAgent。
ExpAgent 只建议 actions，不执行 actions。
```

### 5.2 CodingAgent

CodingAgent 是 repo-scoped coding agent。

ResAgent 调用 CodingAgent 的输入来自：

```text
ExpAgent recommended_action(type=coding_task)
用户明确代码任务
ReproAgent 结果中需要 repo-local patch 的情况
```

CodingAgent 输入核心字段：

```text
repo_path
task_goal
constraints
verify_commands
allowed_paths
output_dir
```

CodingAgent 输出 artifact：

```text
patch_report.md
diff.patch
state.json
logs/
```

重要边界：

```text
CodingAgent 不设计实验。
CodingAgent 不判断科学结论。
CodingAgent 只做明确代码任务。
```

### 5.3 ReproAgent

ReproAgent 是论文/仓库复现 agent。

ResAgent 调用 ReproAgent 的输入来自：

```text
ExpAgent recommended_action(type=repro_task)
用户指定 baseline/SOTA 复现任务
系统需要补充对比实验
```

ReproAgent 输入核心字段：

```text
paper_url
repo_url
experiment_goal
workspace_dir
api_base / api_key_env / model
timeout / mirror_profile / codingagent_path
```

ReproAgent 输出 artifact：

```text
result.md
state.json
logs/
repo/
context/
```

重要边界：

```text
ReproAgent 负责把别人论文代码跑起来。
ReproAgent 不管理整个研究项目。
ReproAgent 可内部调用 CodingAgent 修复目标 repo，但这是复现任务内部行为。
```

## 5.5 ResAgent internal architecture style

ResAgent 的工程风格应与 ExpAgent / ReproAgent / CodingAgent 保持一致，避免做成一个临时脚本式 orchestrator。

推荐采用 agentic loop 风格，但第一版可以先用 deterministic policy 驱动，保留以后切换 LLM controller 的结构。

核心内部结构建议：

```text
LoopState / ResearchState
  持久化全局状态、任务、artifact、decision、observation

ContextBuilder
  从 state 中提取给 ResAgent controller 或下游 adapter 的紧凑上下文

ContextPolicy
  根据模型上下文窗口控制 artifacts、history、logs 的截断和选择

Prompts
  单独管理 ResAgent controller prompt / failure classifier prompt / summary prompt

Controller / Orchestrator
  observe state -> choose action -> execute adapter/tool -> record observation -> repeat

Adapters
  对接 ExpAgent / CodingAgent / ReproAgent，隐藏导入路径、CLI fallback、输出解析

Reporter
  写 state.json、execution_plan.md、summary.md、artifact index
```

对应文件建议：

```text
src/resagent/context.py
src/resagent/context_policy.py
src/resagent/prompts.py
src/resagent/controller.py
src/resagent/orchestrator.py
src/resagent/adapters/*.py
```

设计原则：

```text
prompts 不要散落在 main.py / orchestrator.py 中
上下文截断和 artifact 选择不要散落在各 adapter 中
adapter 只负责调用下游模块，不负责 ResAgent 全局决策
controller 只通过 action/observation 更新 state，不直接乱写 artifact 文件
```

第一版 action space 可以很小：

```text
call_exp_agent
call_coding_agent
call_repro_agent
classify_failure
ask_user
finish
```

后续再扩展并行、预算优化和自动重试。

## 6. 推荐项目结构

```text
ResAgent/
  README.md
  DEVELOPMENT_PLAN.md
  pyproject.toml
  config.yaml.example
  src/resagent/
    __init__.py
    main.py              # CLI
    models.py            # ResearchRun / AgentTask / Artifact / Decision
    controller.py        # agentic loop controller
    orchestrator.py      # high-level orchestration helpers
    context.py           # build compact controller/downstream context
    context_policy.py    # model-aware context/artifact/log selection
    prompts.py           # all ResAgent prompts in one place
    planner.py           # deterministic/LLM next-action policy
    state.py             # state.json read/write
    report.py            # summary.md / execution_plan.md / artifact index
    config.py            # paths, LLM, budget, policy config
    adapters/
      __init__.py
      expagent.py
      codingagent.py
      reproagent.py
    integrations/
      __init__.py
      module_paths.py    # resolve CLI/env/config/importable/vendor paths
    policies/
      __init__.py
      retry.py           # transient failure / retry policy
      budget.py          # runtime / gpu / token budget policy
      safety.py          # user confirmation / destructive action policy
  tests/
    test_models.py
    test_state.py
    test_expagent_adapter.py
    test_task_conversion.py
    test_orchestrator_dry_run.py
```

## 7. 核心数据模型

### 7.1 ResearchRun

```yaml
run_id: res-20260806-xxxxxx
workspace_dir: /home/cyl/ResAgent/runs/res-20260806-xxxxxx
research_goal: "..."
status: running
created_at: "..."
updated_at: "..."
```

### 7.2 ResearchState

```yaml
run: {...}
current_summary: "当前研究状态摘要"
artifacts: []
tasks: []
decisions: []
observations: []
budget: {...}
```

### 7.3 Artifact

```yaml
id: artifact_001
type: scientific_decision | experiment_plan | code_patch | repro_result | log | report | other
producer: ExpAgent | CodingAgent | ReproAgent | ResAgent
path: expagent/decision_001/scientific_decision.yaml
summary: "..."
created_at: "..."
metadata: {}
```

### 7.4 AgentTask

```yaml
id: task_001
source: exp_decision_001
agent: ExpAgent | CodingAgent | ReproAgent | ResAgent
kind: advise | coding_task | repro_task | ask_user | classify_failure
status: pending | running | completed | failed | blocked | skipped | needs_user_input
priority: high | medium | low
input: {}
artifacts: []
attempts: []
error: ""
```

### 7.5 DecisionRecord

```yaml
id: decision_001
made_by: ResAgent | ExpAgent
reason: "为什么做这个决定"
selected_action: "call_repro_agent"
alternatives:
  - "call_exp_agent"
  - "ask_user"
evidence:
  - artifact_001
  - task_003
created_at: "..."
```

## 8. Run Workspace 组织

每次 ResAgent run：

```text
runs/<run_id>/
  state.json
  summary.md
  execution_plan.md
  expagent/
    decision_001/
      scientific_decision.yaml
      experiment_plan.yaml
      validation_report.md
  codingagent/
    code_001/
      patch_report.md
      diff.patch
      state.json
      logs/
  reproagent/
    repro_001/
      result.md
      state.json
      logs/
      repo/
      context/
  artifacts/
    index.yaml
```

`state.json` 是主状态，`artifacts/index.yaml` 是可读 artifact registry。

## 9. Adapter 设计

Adapter 是 ResAgent 与下游模块唯一的运行时边界。Adapter 必须支持灵活路径解析，禁止固定路径调用。

统一路径解析函数建议放在：

```text
src/resagent/integrations/module_paths.py
```

每个 adapter 应记录：

```text
resolved source path
调用方式：python_api | cli | vendored_fallback
下游模块 git commit / dirty 状态（如果可获取）
输入摘要
输出 artifact 路径
```

如果下游模块缺少稳定 Python API，adapter 可以临时使用 CLI subprocess，但必须把命令、stdout/stderr、返回码记录为 artifact/log。

### 9.1 ExpAgentAdapter

职责：

```text
把 ResearchState 摘要转成 AdvisorContext
调用 ExpAgent advise()
保存 scientific_decision.yaml
解析 recommended_actions
返回 artifact + suggested tasks
```

MVP 可先用 Python API：

```python
from experiment_designer.agent import advise
from experiment_designer.models import AdvisorContext
```

如果 import 不稳定，再 fallback 到 CLI。

### 9.2 CodingAgentAdapter

职责：

```text
把 AgentTask.input 转成 CodeTaskSpec
调用 run_code_task()
保存 patch_report/diff/state/logs
返回 artifact
```

MVP 使用 Python API：

```python
from coding_agent import CodeTaskSpec, run_code_task
```

### 9.3 ReproAgentAdapter

职责：

```text
把 AgentTask.input 转成 ReproTask
调用 ReproAgent run/controller
保存 result/state/logs
返回 artifact
```

MVP 可优先用 CLI subprocess，因为 ReproAgent 运行环境和参数较重：

```bash
reproagent run --paper ... --repo ... --workspace ... --experiment-goal ...
```

后续再封装稳定 Python API。

## 10. 失败分类和重试策略

ResAgent 必须区分两类失败：

```text
执行失败：网络、下载、GitHub timeout、临时 API 失败、磁盘不足、环境 transient error
科学失败：结果不支持 hypothesis、baseline 不公平、实验设计不足、metric 缺失
```

执行失败由 ResAgent 处理：

```text
网络/GitHub timeout       重试或提醒用户换镜像
API 5xx / timeout         重试
磁盘不足                  ask_user 或 cleanup suggestion
命令超时                  ask_user 是否增加预算
```

科学失败交给 ExpAgent：

```text
结果不支持 idea           call_exp_agent(analyze_results / revise_plan)
baseline 不充分           call_exp_agent(review_plan)
metric 缺失               call_exp_agent(diagnose_failure)，再可能生成 coding_task
```

第一版 retry policy 可以很简单：

```text
transient task failure 最多重试 2 次
非 transient failure 不自动重试
超过预算需要 ask_user
```

## 11. LLM 控制策略

ResAgent 可以最终做 agentic loop，但第一版建议先从 deterministic controller 开始：

```text
1. 总是先 call_exp_agent
2. 把 recommended_actions 转成 tasks
3. dry-run 展示 tasks
4. 用户确认后按 priority 顺序执行
5. 执行完成后再 call_exp_agent analyze_results
```

等状态和 adapter 稳定后，再加入 LLM planner：

```text
observe state -> LLM selects next ResAgent action
```

不要一开始就让顶层 LLM 有过大的 action space。

## 12. CLI 设计

建议第一版 CLI：

```bash
resagent init \
  --goal idea.md \
  --workspace runs/my-research-run
```

```bash
resagent plan \
  --workspace runs/my-research-run \
  --mock
```

```bash
resagent step \
  --workspace runs/my-research-run
```

```bash
resagent run \
  --goal idea.md \
  --workspace runs/my-research-run \
  --confirm-before-actions
```

```bash
resagent status \
  --workspace runs/my-research-run
```

MVP 可以只实现：

```text
init
plan --dry-run
status
```

## 13. 配置

开发使用 `ResAgent` conda 环境。

```bash
conda activate ResAgent
cd /home/cyl/ResAgent
pip install -e .
pytest -q
```

配置文件示例：

```yaml
agents:
  expagent_path: /home/cyl/ExpAgent
  reproagent_path: /home/cyl/reproagent
  codingagent_path: /home/cyl/CodingAgent

llm:
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-v4-pro

workspace:
  default_runs_dir: runs

policy:
  max_task_retries: 2
  confirm_before_external_runs: true
  confirm_before_long_tasks: true
```

路径解析优先级：

```text
CLI arg > environment variable > config.yaml > importable package > vendored fallback
```

`default sibling path` 只能作为 `config.yaml.example` 的示例值，不应成为代码里的硬编码主路径。

## 14. MVP 成功标准

第一阶段 MVP 成功标准：

```text
可以创建 research workspace
可以保存 research_state.json
可以调用 ExpAgent，得到 ScientificDecision
可以把 recommended_actions 转成 ResAgent AgentTask
可以生成 execution_plan.md
可以登记 ExpAgent artifacts
有模型和状态读写测试
```

第二阶段成功标准：

```text
可以执行一个 CodingAgent task
可以登记 patch_report/diff artifacts
可以执行一个 ReproAgent task 或 mock ReproAgent task
可以根据任务成功/失败更新 state
```

第三阶段成功标准：

```text
可以在任务完成后再次调用 ExpAgent analyze_results
可以根据 ExpAgent 新建议继续追加 task
可以形成一个最小 research loop
```

## 15. 推荐开发顺序

```text
1. 初始化项目骨架和 pyproject
2. 定义 models.py
3. 实现 state.py 读写
4. 实现 config.py + integrations/module_paths.py，确保三个模块路径可配置
5. 实现 context.py / context_policy.py / prompts.py 的最小版本
6. 实现 report.py 生成 execution_plan.md / summary.md / artifact index
7. 实现 ExpAgentAdapter mock + real API
8. 实现 recommended_actions -> AgentTask 转换
9. 实现 resagent init / plan / status CLI
10. 加 tests
11. 再接 CodingAgentAdapter
12. 再接 ReproAgentAdapter
13. 最后做 LLM planner / agentic loop
```

## 16. GitHub

仓库：

```text
https://github.com/ylc123456789/ResAgent.git
```

本地开发目录：

```text
/home/cyl/ResAgent
```

建议首次提交只包含：

```text
README.md
DEVELOPMENT_PLAN.md
pyproject.toml
src/resagent/
tests/
```

不要把其他模块复制进 ResAgent 仓库。ResAgent 通过 path/config 调用它们。

## 16.5 下游模块修改流程

当 ResAgent 开发发现下游模块需要修改时，统一流程是：

```text
1. 不修改下游模块代码
2. 在 ResAgent/docs/ 中生成 integration request 文档
3. 文档说明问题、期望接口、复现步骤、建议修改方向
4. 用户切换到对应模块会话修改该模块
5. 回到 ResAgent 后更新 adapter 或同步 vendored source
```

这条规则优先级很高。ResAgent 项目中的任何自动化、脚本、测试都不应该写入 `/home/cyl/ExpAgent`、`/home/cyl/reproagent`、`/home/cyl/CodingAgent`。

## 17. 最重要的边界

```text
ExpAgent gives scientific recommendations.
ResAgent makes orchestration decisions.
CodingAgent changes code.
ReproAgent reproduces paper repositories.
```

任何涉及“是否调谁、何时调、是否重试、是否问用户、是否消耗预算、是否停止项目”的逻辑，都属于 ResAgent。

任何涉及“实验是否科学、结果说明什么、下一步该验证什么、需要查什么论文”的逻辑，都属于 ExpAgent。
