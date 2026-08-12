# 四模块可读性重构开发方案

## 1. 文档目的

本文指导 ResAgent、ExpAgent、CodingAgent 和 ReproAgent 进行一次以可读性为唯一主要目标的结构重构。

本次重构不是功能开发，不追求更多抽象层，也不重新设计 Agent 架构。目标是让新开发者可以快速定位入口、Agent 循环、上下文、Prompt、工具执行、外部模块适配、状态持久化和报告生成代码，同时保持全部外部行为不变。

本文是四个仓库共同遵循的实施与验收规范。每个模块仍然独立维护、独立提交、独立测试。

---

## 2. 重构基线

开始重构前，各仓库必须至少包含下列提交，且工作区必须干净：

| 模块 | 仓库路径 | 分支 | 基线提交 |
|---|---|---|---|
| ResAgent | `/home/cyl/ResAgent` | `master` | `87e76f4` |
| ExpAgent | `/home/cyl/ExpAgent` | `main` | `090e132` |
| CodingAgent | `/home/cyl/CodingAgent` | `main` | `d11570c` |
| ReproAgent | `/home/cyl/reproagent` | `main` | `384312b` |

建议为每个仓库创建相同语义的基线标签：

```bash
git tag pre-readability-refactor-2026-08
git push origin pre-readability-refactor-2026-08
```

若开始开发时仓库已有更新，应先重新执行完整基线测试，并在本文实施记录中填写新的实际基线，不得默认沿用上表。

---

## 3. 成功标准

重构完成后应同时满足：

1. 开发者可以通过目录结构判断代码职责。
2. CLI 文件只负责参数解析、依赖装配和调用公共 API。
3. Agent 循环、动作执行、Prompt、上下文和运行时操作彼此分离。
4. 跨模块调用只经过公开 API 或 adapter，不引用其他模块内部实现。
5. 四个模块对相同职责使用一致的目录和命名习惯。
6. 不改变任何公共 API、CLI、状态模型、产物布局或运行行为。
7. 全部现有测试、兼容性测试、确定性闭环和指定真实测试通过。
8. 文件移动后不存在重复实现、废弃兼容层或无人使用的旧文件。

本次重构不以减少总代码行数为硬指标。清晰的模块边界比机械压缩代码更重要。

---

## 4. 明确的非目标

本次不得顺手进行以下工作：

- 改变 Prompt 文案、Schema 或 LLM 决策策略；
- 增加新的 Agent、工具、工作流或配置项；
- 修改重试次数、超时、预算、GPU、镜像或 Conda 策略；
- 修改 `state.json`、`session.yaml`、artifact 或 workspace 结构；
- 统一四个项目的 Pydantic 模型到共享包；
- 创建跨仓库基础库或公共框架；
- 将同步代码改成异步代码；
- 引入新的 Agent 框架或依赖注入框架；
- 重写已经通过真实测试的执行器；
- 修改其他模块仓库的代码。

重构过程中若发现真实 bug，应记录到独立 issue 或修复文档。除非 bug 阻止重构测试，否则不得和文件搬迁提交混合。

---

## 5. 四模块共同结构规则

四个模块不要求目录完全相同，但同类职责遵循统一位置：

```text
<package>/
├── __init__.py          # 稳定公共 Python API
├── main.py              # CLI 参数解析和依赖装配
├── agent.py             # 顶层公共运行/恢复 API（模块需要时）
├── models.py            # 公共输入输出和持久化模型
├── config.py            # 配置解析
├── controller/          # Agentic loop、动作分派和 Prompt
├── context/             # 上下文构建与预算策略
├── runtime/             # shell、环境、文件编辑等副作用执行
├── integrations/        # 外部模块和服务适配
├── session.py           # 会话卡和恢复
└── report.py            # 最终报告
```

目录只在确实存在两个或以上相关文件时创建。禁止为了“看起来统一”创建空目录、单文件目录或只做一次转发的无意义层。

### 5.1 依赖方向

推荐依赖方向：

```text
main
  -> agent/orchestrator
      -> controller
          -> context
          -> runtime/integrations
      -> session/report

models/config
  <- 可被各层引用
```

禁止：

- `models.py` 导入 controller、runtime 或 integration；
- runtime 导入 CLI；
- 子模块内部反向导入 ResAgent；
- Prompt 文件执行 I/O；
- report/session 触发 Agent 决策。

### 5.2 文件职责

- 文件名表达职责，不使用 `utils.py`、`common.py`、`helpers.py` 作为杂物箱。
- 一个文件通常不超过 350 行；超过 450 行必须说明为何不能按职责拆分。
- 单个函数通常不超过 80 行；复杂 Agent loop 可例外，但应通过命名良好的私有函数分段。
- 不增加只有一个调用点、只转发参数的类。
- 注释解释约束、原因和不变量，不复述代码。
- 领域词汇保持一致：`task`、`attempt`、`artifact`、`session`、`workspace`、`observation` 不混用。

### 5.3 兼容导入

内部文件可以迁移，但公共导入必须保持：

```python
from coding_agent import CodeTaskSpec, run_code_task
from experiment_designer import AdvisorContext, ScientificDecision
from reproagent import ReproTask
```

已有外部代码直接使用的模块路径，在确认没有调用方前不得删除。必要时保留薄兼容模块：

```python
# old_module.py
from .new_package.module import PublicName

__all__ = ["PublicName"]
```

兼容模块只能转发公开符号，不得保留第二套实现。

---

## 6. 行为冻结清单

重构前必须把以下项目作为行为快照保存并测试：

### 6.1 公共接口

- 包根目录 `__all__`；
- 公共函数和 Pydantic 模型签名；
- CLI 命令、参数、默认值和退出码；
- `--help` 输出中的参数集合；
- 环境变量名称；
- 配置 YAML 字段。

### 6.2 持久化和产物

- `state.json` 字段及旧状态加载；
- `session.yaml` 字段、parent、bindings、key_artifacts；
- task/attempt/workspace 目录布局；
- artifact ID、类型和相对路径；
- 日志文件名称；
- Conda 环境 namespace 和复用逻辑。

### 6.3 控制流

- Allowed Actions；
- task owner 和 dependency gate；
- retry、pause、answer、resume 和 finish gate；
- `completed_with_warnings` 映射；
- CodingAgent 修改后工作树传给 ReproAgent；
- shell `pipefail` 和危险命令限制；
- 上下文预算和裁剪顺序。

### 6.4 LLM 边界

- system prompt 和 schema 文本内容；
- JSON action 类型；
- mock LLM 输出；
- 模型名称、API base 和 key env 传递。

重构提交不应产生 Prompt 内容 diff。若格式化工具改变长字符串，应回退该变化。

---

## 7. CodingAgent 重构方案

CodingAgent 当前结构最好，应作为目录风格参考，并最先重构以验证方法。

### 7.1 目标结构

```text
coding_agent/
├── __init__.py
├── agent.py
├── models.py
├── controller/
│   ├── __init__.py
│   ├── loop.py
│   ├── actions.py
│   ├── prompts.py
│   └── repair.py
├── runtime/
│   ├── __init__.py
│   ├── runner.py
│   ├── edits.py
│   ├── apply.py
│   └── safety.py
├── context/
│   ├── __init__.py
│   ├── builder.py
│   └── policy.py
├── llm.py
├── session.py
└── report.py
```

### 7.2 文件迁移

| 现有文件 | 目标文件 | 说明 |
|---|---|---|
| `runner.py` | `runtime/runner.py` | 命令执行 |
| `edits.py` | `runtime/edits.py` | 文本编辑原语 |
| `apply.py` | `runtime/apply.py` | patch 应用 |
| `safety.py` | `runtime/safety.py` | 路径与命令安全 |
| `context.py` | `context/builder.py` | 仓库上下文 |
| `context_policy.py` | `context/policy.py` | 上下文预算 |

现有 `controller/` 保持，不在本轮重写 action 实现。旧模块路径可先保留兼容转发，待四模块 E2E 后再决定是否删除。

### 7.3 CodingAgent 验收

- 根包公开 API 完全不变；
- repo-scoped edit、verification、patch report 行为不变；
- read-only QA 和 resume API 不变；
- session card 的 `project_path` 和 `parent` 不变；
- 全量测试通过。

---

## 8. ReproAgent 重构方案

### 8.1 目标结构

```text
reproagent/
├── __init__.py
├── main.py
├── agent.py
├── models.py
├── controller/
│   ├── __init__.py
│   ├── loop.py
│   ├── actions.py
│   └── prompts.py
├── runtime/
│   ├── __init__.py
│   ├── runner.py
│   ├── environment.py
│   ├── audit.py
│   ├── hardware.py
│   └── dataset_cache.py
├── repository/
│   ├── __init__.py
│   └── context.py
├── context/
│   └── policy.py
├── integrations/
│   └── codingagent.py
├── llm.py
├── session.py
├── report.py
└── text.py
```

### 8.2 拆分重点

`controller.py` 当前约 431 行，应按职责拆分：

- `controller/loop.py`：状态循环、step budget、finish；
- `controller/actions.py`：`run_commands`、`audit_env`、`call_coding_agent` 动作处理；
- `controller/prompts.py`：Prompt 构建和动作历史格式化。

运行时文件迁入 `runtime/`，但函数实现不重写：

| 现有文件 | 目标文件 |
|---|---|
| `runner.py` | `runtime/runner.py` |
| `env.py` | `runtime/environment.py` |
| `audit.py` | `runtime/audit.py` |
| `hardware.py` | `runtime/hardware.py` |
| `dataset_cache.py` | `runtime/dataset_cache.py` |
| `context.py` | `repository/context.py` |
| `context_policy.py` | `context/policy.py` |

必须保留 `_INTERNAL_ACTIONS`、`pipefail`、日志实时透传、timeout 和安全判断的原始执行顺序。

### 8.3 ReproAgent 验收

- `reproagent` CLI 参数不变；
- `ReproTask` 和 `run_controller` 调用方式不变；
- Conda 创建、查找和复用不变；
- repo/dataset/pip cache 行为不变；
- GPU 硬件上下文不变；
- CodingAgent patch 流程不变；
- session resume 不变；
- `105+` 项测试通过，包含 `pipefail` 和 `_INTERNAL_ACTIONS` 回归测试。

---

## 9. ExpAgent 重构方案

### 9.1 目标结构

```text
experiment_designer/
├── __init__.py
├── main.py
├── agent.py
├── models.py
├── config.py
├── controller/
│   ├── __init__.py
│   ├── loop.py
│   ├── planner.py
│   └── validator.py
├── prompts/
│   ├── __init__.py
│   ├── system.py
│   ├── schemas.py
│   └── rendering.py
├── context/
│   ├── __init__.py
│   ├── builder.py
│   └── policy.py
├── tools/
│   ├── __init__.py
│   ├── files.py
│   ├── papers.py
│   └── registry.py
├── llm.py
├── session.py
└── report.py
```

### 9.2 拆分重点

`main.py` 当前约 597 行。最终只保留：

- argparse 定义；
- CLI 输入转换；
- 调用 `agent.py` 公共 API；
- 输出路径和退出码。

业务流程迁入 `agent.py` 或 `controller/loop.py`。

`prompts.py` 按内容拆分，但 Prompt 的最终渲染字符串必须字节级一致：

- JSON schema 常量 → `prompts/schemas.py`；
- system instructions → `prompts/system.py`；
- context/action 渲染 → `prompts/rendering.py`。

`tools.py` 仅在自然边界明确时拆分：

- 本地文件/论文读取；
- 论文搜索；
- tool registry 和 dispatch。

禁止为了压缩单文件行数，将每个工具拆成一个文件。

### 9.3 ExpAgent 验收

- `expagent` CLI 不变；
- 根包模型导出不变；
- `advise()` 和设计流程不变；
- action dependency metadata 不变；
- Prompt 快照一致；
- mock decision 一致；
- session card 和报告一致；
- `57+` 项默认测试通过，e2e marker 选择方式不变。

---

## 10. ResAgent 重构方案

ResAgent 最后重构，因为它依赖三个子模块接口，且当前平面文件最多。

### 10.1 目标结构

```text
resagent/
├── __init__.py
├── main.py
├── agent.py
├── models.py
├── config.py
├── controller/
│   ├── __init__.py
│   ├── loop.py
│   ├── actions.py
│   ├── planner.py
│   ├── task_contracts.py
│   └── prompts.py
├── conversation/
│   ├── __init__.py
│   ├── loop.py
│   ├── models.py
│   ├── tools.py
│   └── history.py
├── context/
│   ├── __init__.py
│   ├── builder.py
│   └── policy.py
├── adapters/
│   ├── codingagent.py
│   ├── reproagent.py
│   └── expagent/
│       ├── __init__.py
│       ├── adapter.py
│       ├── task_conversion.py
│       └── dependency_graph.py
├── persistence/
│   ├── __init__.py
│   ├── state.py
│   ├── sessions.py
│   ├── workspace.py
│   └── report.py
├── integrations/
└── policies/
```

### 10.2 Controller 拆分

`controller.py` 按以下边界拆分：

- `controller/loop.py`：`Controller.step/run`、终态保护和状态推进；
- `controller/actions.py`：各 ActionName handler；
- `controller/task_contracts.py`：任务 owner、依赖图、Allowed Actions、finish gate；
- `controller/planner.py`：LLM action planning；
- `controller/prompts.py`：controller prompt。

不得改变每次 step 写入 DecisionRecord、Observation 和 budget 的顺序。

### 10.3 ExpAgent adapter 拆分

`adapters/expagent.py` 当前约 629 行，是最高优先级：

- `adapter.py`：调用 ExpAgent、保存 scientific decision、返回 Artifact；
- `task_conversion.py`：RecommendedAction → AgentTask；
- `dependency_graph.py`：action ID、依赖 DAG、cycle 和 workspace inheritance；
- session card 修补保留在 adapter，或迁入 persistence/sessions，二选一，禁止复制实现。

该拆分必须覆盖已经真实发现过的边界：

- dependent action 可以没有自己的 action ID；
- run task 可继承规范化前置任务推断出的 workspace；
- 未知依赖、自依赖和环依赖拒绝；
- 重复任务 fingerprint 和 supersedes 行为不变。

### 10.4 Conversation 拆分

现有 `chat.py`、`chat_models.py`、`chat_tools.py`、`conversation.py` 统一迁入 `conversation/`：

- `loop.py`：聊天循环与路由；
- `models.py`：消息和会话模型；
- `tools.py`：工具 schema 和执行；
- `history.py`：conversation 状态和历史。

公共 CLI 和 resume/answer 行为不变。

### 10.5 Persistence 拆分

| 现有文件 | 目标文件 |
|---|---|
| `state.py` | `persistence/state.py` |
| `session_cards.py` | `persistence/sessions.py` |
| `workspace_layout.py` | `persistence/workspace.py` |
| `report.py` | `persistence/report.py` |

所有路径必须继续由 WorkspaceLayout 单一来源生成。

### 10.6 ResAgent 验收

- `resagent` CLI、chat、answer、resume 和 status 不变；
- 三个 adapter 的公共构造参数不变；
- task contract、依赖图和 finish gate 不变；
- state/session/artifact/workspace 完全兼容；
- 当前 `122+` 项测试通过；
- 确定性四模块闭环通过；
- cloud acceptance 脚本参数和报告格式不变。

---

## 11. 必须先补的重构保护测试

在移动文件前，先增加以下测试。测试只锁定已有行为，不创造新行为。

### 11.1 Public API smoke test

每个包从根目录导入公开符号，并检查函数签名关键参数。

### 11.2 CLI contract test

记录每个 CLI parser 的 subcommand、option、default 和 required 属性。避免仅比较带换行/宽度差异的完整 help 文本。

### 11.3 Serialization compatibility

- 用基线 fixture 加载 `state.json`；
- 保存后重新加载；
- 比较语义字段；
- 对 `session.yaml` 做 schema key 检查。

fixture 中不得包含密钥、绝对用户路径或大型日志。

### 11.4 Prompt snapshot

对最终发送给 LLM 的 system prompt 和固定 mock context 做快照。重构阶段快照变化即失败。

### 11.5 Workspace layout snapshot

用固定 run/task/attempt ID 检查所有路径生成结果。

### 11.6 Cross-module contract

- ExpAgent action → ResAgent task；
- ResAgent parent_run → CodingAgent/ReproAgent；
- CodingAgent 未提交修改 → ReproAgent 隔离快照；
- shared ResAgent run → shared ReproAgent env namespace。

---

## 12. 分阶段实施计划

### Phase 0：冻结与测量

1. 确认四仓库干净并记录 commit。
2. 创建基线 tag。
3. 保存测试结果、CLI contract、Prompt snapshot 和序列化 fixture。
4. 建立独立分支：`refactor/readability-layout`。

验收：没有生产代码行为变化。

### Phase 1：CodingAgent

1. 创建 `runtime/` 和 `context/`。
2. 逐文件移动并更新内部 import。
3. 保留必要旧模块兼容导出。
4. 运行 CodingAgent 全量测试。
5. 用 ResAgent adapter 运行 mock 集成。

一个提交只完成一个自然目录迁移。

### Phase 2：ReproAgent

1. 先移动 runtime 文件，不拆 controller。
2. 验证完整测试。
3. 再拆 controller loop/actions/prompts。
4. 验证 `pipefail`、内部动作、stream、timeout。
5. 运行一个无 GPU mock/轻量流程。

### Phase 3：ExpAgent

1. 移动 context 和 validator/planner。
2. 拆 Prompt，确保快照不变。
3. 拆 tools。
4. 将 main 中业务逻辑迁入 agent/controller。
5. 验证 action dependency schema。

### Phase 4：ResAgent

1. 移动 persistence 和 context。
2. 移动 conversation。
3. 拆 ExpAgent adapter。
4. 最后拆 controller。
5. 每一步运行 ResAgent 全量测试。

### Phase 5：系统验收

1. 四个模块全量测试。
2. `scripts/deterministic_system_test.py`。
3. cloud `dependency-chain`。
4. 一个轻量 GPU repro case。
5. 检查 git diff 中无 Prompt、Schema 和 fixture 意外变化。

---

## 13. 提交规范

推荐提交粒度：

```text
test: freeze public api and cli contracts
refactor: move coding runtime modules
refactor: split repro controller actions
refactor: split expagent prompt modules
refactor: move resagent persistence modules
refactor: split resagent expagent adapter
docs: update architecture map after refactor
```

每个提交必须：

- 可以独立运行测试；
- 不同时修改两个仓库；
- 不混入格式化全仓库、依赖升级或功能变化；
- 在提交说明中列出移动前后路径；
- 保持 `git diff --check` 通过。

禁止一个提交同时移动文件并大幅重写内部逻辑。Git 难以识别 rename 时，应先做纯移动提交，再做拆分提交。

---

## 14. 测试命令

### CodingAgent

```bash
cd /home/cyl/CodingAgent
conda activate CodingAgent
pytest -q
```

### ReproAgent

```bash
cd /home/cyl/reproagent
conda activate reproagent
pytest -q
```

### ExpAgent

```bash
cd /home/cyl/ExpAgent
conda activate ResAgent
pytest -q
```

### ResAgent

```bash
cd /home/cyl/ResAgent
conda activate ResAgent
pytest -q tests
python scripts/deterministic_system_test.py
```

### 云端最小真实验收

```bash
python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace \
  --case dependency-chain

python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace \
  --case repro
```

重构期间不要求每个移动提交都跑云端测试。云端测试只在四个模块合并后的候选版本运行。

---

## 15. 停止条件和回滚规则

出现以下任一情况应停止当前阶段：

- 公共 import 或 CLI 发生变化；
- Prompt 快照变化；
- 旧 state/session 无法加载；
- workspace 或 artifact 路径变化；
- 测试需要通过降低断言强度才能通过；
- 为完成移动必须改动业务逻辑；
- 同一功能在新旧位置出现两份实现；
- 真实测试出现重构前没有的 LLM、环境或路径问题。

处理方式：

1. 定位到最小重构提交。
2. 回退该提交或恢复兼容层。
3. 不在后续模块中绕过问题。
4. 如果确属基线 bug，另开功能修复提交和测试，完成后重新确定重构基线。

禁止使用测试 skip、宽泛 exception、fallback import 或动态 `sys.path` 掩盖迁移错误。

---

## 16. 文档交付

每个模块完成后更新其 `ARCHITECTURE.md` 或 README 架构章节，至少包含：

- 目录树；
- 每个目录职责；
- 顶层调用流程；
- 公共 API；
- session/workspace 位置；
- 添加新 Action、Tool 或 Integration 应修改的位置；
- 本地和真实测试命令。

ResAgent 最终文档还应包含四模块调用图：

```text
User / Chat
    -> ResAgent Controller
        -> ExpAgent (scientific advice)
        -> CodingAgent (code work)
        -> ReproAgent (environment + experiment)
            -> CodingAgent (patch when required)
```

---

## 17. 最终完成定义

只有同时满足以下条件，重构才算完成：

- 四个仓库目标目录落地；
- 旧公共导入和 CLI 仍可使用；
- 没有无引用旧实现；
- 四模块全量测试通过；
- ResAgent 确定性闭环通过；
- 云端 dependency-chain 和轻量 GPU repro 通过；
- state/session/artifact/workspace 兼容性通过；
- Prompt 快照无变化；
- 四个仓库架构文档已更新；
- 相对基线的差异中不包含功能、依赖或配置变化。

本次工作的最终产物应是更容易阅读和继续开发的同一个系统，而不是一套功能相似但行为不同的新系统。
