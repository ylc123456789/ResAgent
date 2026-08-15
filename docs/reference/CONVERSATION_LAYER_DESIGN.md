# 对话层与多专家注册架构 — 开发文档

**日期**: 2026-08-08
**状态**: 已实施并纳入 V2 验收基线（tag `v2-validated-2026-08-15`）。本文的契约与边界为常驻参考；任务书和里程碑章节保留为实施记录。
**关联文档**: [SYSTEM_INTERACTION_ARCHITECTURE.md](../completed/SYSTEM_INTERACTION_ARCHITECTURE.md)（本文档取代其中的 IntakeRouter 方案，保留 ConversationState/ResearchState 分离与 Capability Descriptor 思想，差异说明见附录 C）
**涉及模块与分工**:

| 模块 | 改动量 | 负责方 |
|------|--------|--------|
| ResAgent | 新增对话层（主要工作） | 本文档 §4，由 ResAgent 侧直接实施 |
| ExpAgent | 小改（schema 放宽 + 运行旋钮 + 名片） | 按 §6 任务书实施 |
| CodingAgent | 中改（新增 QA 能力 + 名片） | 按 §7 任务书实施 |
| ReproAgent | 零代码改动，仅名片 | 按 §8 |

---

## 1. 背景与目标

### 1.1 现状

ResAgent 目前是纯批处理 orchestrator：`init / run / step / status` 四个 CLI 命令驱动一个 6-action 的 agentic loop（`controller.py` + `planner.py` + 三个 adapter）。所有输入被假定为"一个明确的科研项目目标"。

真实科研场景中用户输入是混合且漂移的：问原理、问代码、讨论模糊 idea、继续已有实验、分析失败日志……系统缺少统一的交互入口。

### 1.2 目标

1. **统一对话入口** `resagent chat`：任意自然语言输入都能得到合理响应，不再强制进入 research workflow。
2. **多专家架构**：子 agent 作为自描述、可复用的"专家能力"，ResAgent 通过名片（agent.yaml）发现并按需调用；专家仓库保持独立，可被其他 agent 系统直接复用。
3. **严肃 workflow 不妥协**：一旦用户确认立项，进入现有可审计、可恢复的 ResearchRun 机制（现有 orchestration 代码基本不动）。

### 1.3 非目标

- 不做统一 `ExpertRequest/ExpertResult` 信封协议（v2 再说，理由见 §5.4）。
- 不给专家加 mode 枚举（理由见 §2 P5）。
- 不做跨进程/跨语言调用（当前全部进程内 Python import）。
- 不实现 PaperAgent（仅在架构上预留）。

---

## 2. 设计原则

**P1 — 对话 loop 即路由器，不做前置意图分类器。**
路由是对话 loop 内的工具选择，带着完整对话上下文发生，而非输入门口的一次性分类。真实输入是混合意图（"这个机制为什么有效？有前途的话帮我规划实验"），单发分类器无法处理，且分类错误不可恢复。参考：Claude Code（主 loop + Task 子 agent 工具）、Codex CLI，均无意图分类阶段。

**P2 — ConversationState 与 ResearchState 分离，ResearchBrief 是唯一的晋升边界。**
对话可以任意发散，只污染 ConversationState；只有当用户明确确认后，才把对话蒸馏成 `ResearchBrief` 并物化为 `ResearchState`。参考：Claude Code 的 plan mode → execute 转换。

**P3 — 承诺分级（Tier），确认策略由名片的 `side_effects` 推导，而不是按意图类型硬编码。**

| Tier | 行为 | 副作用 | 确认策略 |
|------|------|--------|----------|
| 0 | 对话层直接回答 / 追问澄清 | 无 | 无需确认 |
| 1 | 调用 `side_effects: none` 的专家（咨询、问答） | 无（只读） | 无需确认 |
| 2 | 创建/推进 ResearchRun、调用有副作用的专家 | 改 workspace、建环境、花预算 | 必须显式用户确认 |

路由层不需要懂任何专家的语义，读名片就知道要不要确认。新专家注册即获得正确治理，这是扩展性的核心。参考：Codex 的 approval modes。

**P4 — 专家 = 自然语言契约 + 类型化输入输出 + 自描述名片。**
调用方传自包含的自然语言 instruction 和类型化 artifacts；"这是问答还是设计"的判断由专家内部完成，这本身就是专家能力的一部分。参考：Claude Code subagent（description 路由 + NL task prompt）、MCP（capability discovery）、Google A2A（Agent Card）。

**P5 — 只在专家"做不到"时加能力，不为调用方省事加 mode。**
ExpAgent 通过统一 `advise()` 已经能回答原理问题，缺的只是输出 schema 的宽容度 → 放宽 schema，不加 mode。CodingAgent 完全无法产出"代码解释"这类产物（其 PatchReport 语义以 diff 为中心）→ 这是能力缺口，需要新增。裁决标准一句话：*专家做得到但调用方想省事 → 什么都别加，把话写进 instruction；专家做不到 → 加能力，但对外契约形态不变。*

---

## 3. 总体架构

```
User
  └─ resagent chat                          ← 新入口（对话 agentic loop）
       │  system prompt 注入 ExpertCard 摘要
       │
       ├─ Tier 0: reply（直接回答/追问澄清）── 默认终止动作
       │
       ├─ Tier 1 工具:
       │    consult_expert(expert, instruction, artifacts)
       │        └─ 仅允许 side_effects == "none" 的专家
       │        └─ v1: expagent（advisory）；codingagent_qa 名片状态 planned
       │    list_runs() / inspect_run(run_id)
       │
       ├─ Tier 2 工具（需用户确认）:
       │    propose_research_run(brief) ──→ 展示 ResearchBrief，等待确认
       │    start_research_run()        ──→ init_run() + 跑 N 步 loop
       │    advance_run(run_id, instruction) ──→ 注入指令 + 跑 N 步 loop
       │
       ▼
  现有组件（基本零改动）:
       orchestrator.init_run / Controller.run / Planner / Adapters
       唯一侵入点：ResearchState 增加 user_directives 字段（§4.7）
```

对话层是**薄壳**：只做路由、澄清、呈现、蒸馏。不做任何科学推理——深度推理全部在专家内部。

---

## 4. ResAgent 侧实现规格

### 4.1 新增/修改文件清单

```
src/resagent/
  chat_models.py      # 新增: ConversationState / ConversationEvent / ResearchBrief / ExpertCard
  conversation.py     # 新增: 会话持久化（事件日志 JSONL + 状态快照，原子写）
  capabilities.py     # 新增: 名片发现、加载、校验、tool 描述生成
  chat_tools.py       # 新增: 5 个对话工具的实现
  chat.py             # 新增: 对话 agentic loop + REPL
  prompts.py          # 修改: 新增 CHAT_SYSTEM（附录 A）
  models.py           # 修改: ResearchState 增加 user_directives 字段（§4.7）
  context.py          # 修改: build_controller_context 渲染 user_directives（§4.7）
  adapters/expagent.py # 修改: 增加 advise_adhoc() 方法（§4.5）
  config.py           # 修改: 增加 ChatConfig 段（§4.9）
  main.py             # 修改: 增加 chat 子命令（§4.8）
tests/
  test_chat_models.py     # 新增
  test_conversation.py    # 新增
  test_capabilities.py    # 新增
  test_chat_tools.py      # 新增
  test_chat_loop.py       # 新增（mock LLM 端到端）
```

现有 `controller.py / orchestrator.py / planner.py / state.py / adapters/codingagent.py / adapters/reproagent.py` **不改**。

### 4.2 数据模型（`chat_models.py`）

```python
"""Conversation-layer models. Independent from ResearchState by design."""

class ConversationEventType(str, Enum):
    user_message = "user_message"
    assistant_message = "assistant_message"
    tool_call = "tool_call"
    tool_result = "tool_result"
    brief_proposed = "brief_proposed"
    brief_confirmed = "brief_confirmed"
    brief_rejected = "brief_rejected"
    run_created = "run_created"
    run_advanced = "run_advanced"
    error = "error"


class ConversationEvent(BaseModel):
    """Append-only event. ConversationState can always be rebuilt from events."""
    seq: int
    type: ConversationEventType
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class ConvArtifactRef(BaseModel):
    """Lightweight artifact reference surfaced during conversation."""
    id: str
    type: str = "other"          # repro_result | code_patch | run_log | metric_summary | other
    path: str = ""
    summary: str = ""
    source: str = ""             # "expagent" | "user" | run_id


class ResearchBrief(BaseModel):
    """Distilled from conversation; the ONLY gate into ResearchState."""
    goal: str                              # 一句话研究目标（必填）
    hypothesis: str = ""
    context_summary: str = ""              # 对话蒸馏出的背景
    constraints: list[str] = Field(default_factory=list)
    relevant_artifacts: list[ConvArtifactRef] = Field(default_factory=list)
    suggested_first_step: str = ""

    def render_goal_text(self) -> str:
        """Compose the research_goal string passed to init_run()."""
        parts = [self.goal]
        if self.hypothesis:
            parts.append(f"Hypothesis: {self.hypothesis}")
        if self.context_summary:
            parts.append(f"Background: {self.context_summary}")
        if self.constraints:
            parts.append("Constraints: " + "; ".join(self.constraints))
        return "\n\n".join(parts)


class ConversationState(BaseModel):
    conversation_id: str
    workspace_root: str                    # runs 根目录
    active_run_id: str | None = None
    scratch_summary: str = ""              # 滚动摘要（压缩旧对话用）
    recent_artifacts: list[ConvArtifactRef] = Field(default_factory=list)  # 上限 20
    pending_brief: ResearchBrief | None = None   # 已提议未确认的 brief
    created_at: datetime = ...
    updated_at: datetime = ...


class ExpertCard(BaseModel):
    """专家名片（agent.yaml 的内存表示）。跨模块的唯一契约。"""
    name: str                              # "expagent" | "codingagent" | ...
    version: str = ""
    role: str = ""
    description_for_router: str            # 给路由 LLM 看的能力描述（含正反例）
    capabilities: list[str] = Field(default_factory=list)  # 如 ["scientific_advisory", "code_question"]
    side_effects: Literal["none", "workspace", "workspace_and_environment"] = "none"
    requires_confirmation: bool = False    # 默认按 side_effects != "none" 推导
    cost_profile: dict[str, Any] = Field(default_factory=dict)  # latency / llm_calls / gpu 等提示
    input_contract: str = ""               # 人读的输入说明，如 "advise(AdvisorContext)"
    output_contract: str = ""              # 人读的输出说明
    status: Literal["available", "planned", "unavailable"] = "available"
```

`status: "planned"` 是有意设计：允许在 CodingAgent QA 能力落地前先登记名片，路由层据此回答"代码问答能力尚未上线"，而不是假装它存在或完全不认识。

### 4.3 会话持久化（`conversation.py`）

目录布局：

```
<workspace_root>/conversations/<conversation_id>/
    conversation.json     # ConversationState 快照（原子写，复用 state.py 的模式）
    events.jsonl          # append-only 事件日志
    experts/              # consult_expert 调用的落盘产物
        expagent_<n>/
    briefs/               # propose_research_run 渲染的 brief 存档
```

API（与 `state.py` 对偶）：

```python
def new_conversation(workspace_root: str) -> ConversationState
def load_conversation(workspace_root: str, conversation_id: str) -> ConversationState | None
def save_conversation(conv: ConversationState) -> None                 # 原子写
def append_event(conv: ConversationState, type: ConversationEventType, payload: dict) -> ConversationEvent
def list_conversations(workspace_root: str) -> list[str]
def rebuild_from_events(workspace_root: str, conversation_id: str) -> ConversationState
    """事件日志是权威来源；快照损坏时可完整重建。"""
```

`conversation_id` 生成规则：`conv-<yyyymmdd>-<uuid6>`（与 run_id 风格一致）。

### 4.4 名片注册表（`capabilities.py`）

```python
class CapabilityRegistry:
    def __init__(self, config: Config): ...
    def load(self) -> None
    def get(self, name: str) -> ExpertCard | None
    def available(self) -> list[ExpertCard]          # status == "available"
    def router_descriptions(self) -> str             # 注入 chat system prompt 的文本
    def check_callable(self, name: str, tier: int) -> tuple[bool, str]
        """tier 1 仅允许 side_effects == 'none' 且 status == 'available'；
           返回 (是否允许, 拒绝原因)。"""
```

名片发现优先级（高 → 低）：

1. `<module_path>/agent.yaml`（专家仓库自带，单一事实来源，目标形态）
2. `config.yaml` 的 `agents.cards.<name>` 段（覆盖/补充）
3. ResAgent 内置默认名片（`capabilities.py` 内常量，保证开箱即用）

内置默认名片（v1）：

```yaml
# expagent（内置默认，待 ExpAgent 仓库自带 agent.yaml 后可移除）
name: expagent
role: scientific_advisor
description_for_router: |
  科学顾问。擅长：科学原理问答、研究 idea 可行性讨论、实验设计、
  实验结果分析、失败归因。只读咨询，不执行代码、不跑实验。
  适用：用户问原理/方法/文献，或讨论模糊想法。
  不适用：需要实际改代码或跑实验的请求。
capabilities: [scientific_advisory, idea_discussion, experiment_design, result_analysis]
side_effects: none
input_contract: advise(AdvisorContext) -> ScientificDecision
status: available

# codingagent（QA 能力未落地前 status: planned）
name: codingagent
role: coding_agent
description_for_router: |
  程序员。擅长：repo 级代码修改（补日志、修 bug、加配置）。
  规划中：代码理解问答（训练入口在哪、loss 怎么算）。
  适用：明确的代码修改任务。不适用：闲聊式代码问题（QA 能力上线前）。
capabilities: [code_modification]        # code_question 落地后加入
side_effects: workspace
input_contract: run_code_task(CodeTaskSpec) -> PatchReport
status: available

name: codingagent_qa                     # 作为独立名片项登记，便于路由区分
role: coding_advisor
description_for_router: |
  代码理解问答（只读）。回答关于某个 repo 的代码问题，附文件/行号证据。
capabilities: [code_question]
side_effects: none
input_contract: run_code_question(CodeQuestionSpec) -> CodeExplanation
status: planned                          # §7 落地后改为 available

# reproagent
name: reproagent
role: reproduction_agent
description_for_router: |
  复现工程师。克隆论文仓库、建 conda 环境、跑 baseline 实验。
  仅在用户明确要求复现/跑 baseline 时使用。绝不用来回答问题。
capabilities: [reproduction_task, baseline_run]
side_effects: workspace_and_environment
requires_confirmation: true
input_contract: run_controller(ReproTask) -> AgentState
status: available
```

### 4.5 对话工具（`chat_tools.py`）

对话 loop 的动作空间和 run loop 一样走"单 JSON 响应"约定（复用 `planner._extract_json` 的解析风格）。LLM 每轮响应二选一：

```json
{"type": "tool_call", "tool": "<name>", "params": {...}, "reason": "..."}
{"type": "reply", "text": "给用户看的最终回复"}
```

5 个工具：

#### T1 `consult_expert`（Tier 1）

```python
params = {
    "expert": "expagent",                # 必须是 status=available 且 side_effects=none 的名片
    "instruction": "自包含的自然语言请求，引用用户原话",
    "artifact_ids": ["..."]              # 可选，引用 conv.recent_artifacts 中的条目
}
```

行为：
- 经 `registry.check_callable(expert, tier=1)` 校验；拒绝时返回错误说明（如 `codingagent_qa` 为 planned → "代码问答能力尚未上线，可以改用 /status 查看或改问其他问题"）。
- v1 仅实现 `expagent` 通路：调用 `ExpAgentAdapter.advise_adhoc()`（新增方法，见下）。
- **advisory 语义**：结果只进入 ConversationState（`recent_artifacts` 追加、`tool_result` 事件），**不创建任何 AgentTask，不触碰任何 ResearchState**。即使 ExpAgent 返回了 `recommended_actions`，对话层也只做摘要呈现（"如果你要推进，可以立项"），由用户决定是否走 T4。
- 产物落盘：`<workspace>/conversations/<conv_id>/experts/expagent_<n>/`。
- 成本控制：v1 固定传 `max_steps=8`（等 ExpAgent E2 落地后生效，落地前忽略该参数）；instruction 中注明"这是对话咨询，按需检索，不必强行产出实验计划"。

`ExpAgentAdapter` 新增方法（复用现有 `_ensure_import` 与 mock 通路）：

```python
def advise_adhoc(
    self,
    situation: str,
    artifacts: list[dict],          # ConvArtifactRef dicts
    out_dir: str,
    max_steps: int | None = None,
    enable_paper_search: bool = True,
) -> dict:
    """Advisory call outside any ResearchRun. Returns raw decision dict."""
```

mock 通路返回一段固定 explanation 型 decision（`conclusion: None`，验证 §6-E1 的兼容性）。

#### T2 `list_runs`（Tier 1）

扫描 `workspace_root` 下含 `state.json` 的目录，返回 `[{run_id, status, goal_摘要, updated_at}]`，按更新时间倒序，上限 20。

#### T3 `inspect_run`（Tier 1）

复用 `orchestrator.status()` 返回人读摘要；副作用：`conv.active_run_id = run_id`（后续"那个实验"类回指生效）。

#### T4 `propose_research_run`（Tier 2，晋升边界）

```python
params = {"brief": ResearchBrief 模型字段}
```

行为：
- 校验 `brief.goal` 非空；`conv.pending_brief = brief`；写 `brief_proposed` 事件；brief 渲染存档到 `briefs/`。
- 工具结果要求 LLM 用 `reply` 向用户展示 brief 全文并请求确认。**确认前不发生任何状态变更。**
- 用户确认后（自然语言"确认/开始"或 slash `/confirm`），LLM 调用 T5；用户否定则 `brief_rejected` 事件 + 清空 `pending_brief`。

#### T5 `start_research_run` / `advance_run`（Tier 2）

```python
# start_research_run
params = {"max_steps": 3}            # 可选，默认取 config.chat.default_advance_steps
# 要求 conv.pending_brief 非空，否则报错"先 propose"
# 行为: init_run(goal=brief.render_goal_text()) → 写 run_created 事件
#       → active_run_id = run_id → controller.run(state, max_steps)
#       → 返回运行摘要（执行了哪些 action、产出哪些 artifact、当前状态）

# advance_run
params = {"run_id": "...", "instruction": "用户的新指令原文", "max_steps": 3}
# 行为: load_state → state.user_directives.append(...)（§4.7）
#       → controller.run(state, max_steps) → 写 run_advanced 事件 → 返回摘要
```

长跑保护：`max_steps` 有硬上限（`config.chat.max_steps_per_turn`，默认 5）。run 进入 `paused/completed` 或步数耗尽即返回，把"继续推进"的选择权交还用户。repro 类长任务在 run loop 内部由现有 `ask_user`/policy 机制兜底，对话层不新增阻塞等待逻辑。

### 4.6 对话 loop（`chat.py`）

```python
class ChatLoop:
    def __init__(self, config: Config, registry: CapabilityRegistry,
                 adapters: ..., mock: bool = False): ...

    def handle_message(self, conv: ConversationState, text: str) -> str:
        """一轮对话。返回展示给用户的文本。"""
        append_event(conv, user_message, {"text": text})
        for _ in range(self.config.chat.max_tool_calls_per_turn + 1):
            prompt = self._build_prompt(conv)
            raw = self._call_llm(prompt)          # mock 时走规则通路
            msg = self._parse(raw)                # tool_call | reply
            if msg.type == "reply":
                append_event(conv, assistant_message, {"text": msg.text})
                save_conversation(conv)
                return msg.text
            result = self.tools.execute(conv, msg.tool, msg.params)
            append_event(conv, tool_call, {...}); append_event(conv, tool_result, {...})
            save_conversation(conv)
        # 工具调用超限降级：强制 reply
        return self._force_reply(conv)
```

**上下文构建**（每轮重建，不累积原始 messages，与 ExpAgent/reproagent 的 `build_turn_prompt` 风格一致）：

```
CHAT_SYSTEM（附录 A，含 registry.router_descriptions() 注入的名片摘要）
+
## Conversation Snapshot
  - active_run_id / pending_brief 状态 / recent_artifacts 列表
+
## Recent Events（最近 12 条事件，单行压缩格式）
+
## Scratch Summary（旧对话的滚动摘要，超过窗口时由 LLM 异步压缩生成）
```

**LLM 传输**：新增 `llm.py`，提供 `call_chat(system, user, *, model, api_base, api_key_env) -> str`，与 `Planner._call_llm_raw` 同参数风格（httpx、temperature 0.3）。`Planner` 不重构，两个通路并存，后续再统一（避免回归风险）。

**mock 通路**：`mock=True` 时 `ChatLoop` 走关键词规则（含"立项/开始实验"→ propose；含"?"→ consult 或 reply；"确认"→ start），保证 `--mock` 全链路可演示；单测则用**脚本化响应队列**（测试注入 `list[str]` 作为 LLM 依次返回），保证精确控制。

### 4.7 对现有模型的唯一侵入：`user_directives`

`advance_run` 需要把用户新指令注入 run 的 planner 视野。最小方案：

```python
# models.py
class UserDirective(BaseModel):
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_conversation: str = ""

class ResearchState(BaseModel):
    ...
    user_directives: list[UserDirective] = Field(default_factory=list)
```

```python
# context.py build_controller_context()，在 Summary 段之后插入：
if state.user_directives:
    parts.append("\n## User Directives (latest last)")
    for d in state.user_directives[-3:]:
        parts.append(f"- [{d.ts:%Y-%m-%d %H:%M}] {d.text}")
```

指令**持久保留**（它们就是 run 历史的一部分，可审计），planner 每步都能看到最近 3 条。无清除逻辑。`CONTROLLER_SYSTEM` 加一行说明即可："User Directives 是用户的最新指示，优先级高于你自己的判断。"

### 4.8 CLI（`main.py` 新增）

```bash
resagent chat [--workspace runs] [--conversation-id ID | --resume ID] \
              [--mock] [--expagent-path ...] [--codingagent-path ...] [--reproagent-path ...]
```

REPL 内 slash 命令（本地处理，不经过 LLM，作为确定性逃生通道）：

| 命令 | 行为 |
|------|------|
| `/help` | 帮助 |
| `/runs` | 等价 list_runs |
| `/status [run_id]` | 等价 inspect_run（缺省用 active_run_id） |
| `/use <run_id>` | 设置 active_run_id |
| `/brief` | 显示当前 pending_brief |
| `/confirm` | 确认 pending_brief 并启动（等价自然语言"确认"） |
| `/cancel` | 丢弃 pending_brief |
| `/new` | 开新会话 |
| `/quit` | 退出 |

slash 与 LLM 通路优先级：用户输入以 `/` 开头 → 本地执行；否则走 ChatLoop。

### 4.9 config 扩展（`config.py`）

```python
@dataclass
class ChatConfig:
    max_tool_calls_per_turn: int = 4
    default_advance_steps: int = 3
    max_steps_per_turn: int = 5
    consult_max_steps: int = 8           # 传给 ExpAgent 的步数上限（E2 落地后生效）
    conversations_dirname: str = "conversations"

@dataclass
class Config:
    ...
    chat: ChatConfig = field(default_factory=ChatConfig)
```

`config.yaml` 对应段：

```yaml
chat:
  max_tool_calls_per_turn: 4
  default_advance_steps: 3
agents:
  expagent_path: /home/cyl/ExpAgent
  cards:                       # 可选，覆盖内置名片
    codingagent_qa:
      status: available        # §7 落地后在此翻牌，或删段改用仓库名片
```

### 4.10 测试规格

全部 mock LLM + mock adapters，不依赖 API key。新增约 15 个测试：

| 测试 | 断言 |
|------|------|
| test_conv_models_roundtrip | 四个新模型序列化/反序列化 |
| test_event_log_rebuild | 快照删除后从 events.jsonl 完整重建 |
| test_registry_builtin_cards | 内置三张名片可加载，side_effects 正确 |
| test_registry_card_from_repo | 模块路径下 agent.yaml 覆盖内置名片 |
| test_check_callable_tier1 | planned/workspace 专家被 Tier 1 拒绝且原因可读 |
| test_chat_qa_routes_to_expert | "loss 怎么算的" → consult_expert，无 run 创建 |
| test_chat_idea_discussion_no_run | 模糊 idea 讨论 → consult_expert 或 reply，**断言无 state.json 落盘** |
| test_chat_explicit_start_creates_run | "按这个 idea 开始实验"→ propose → "确认" → run 创建且 active_run_id 设置 |
| test_chat_propose_requires_confirm | propose 后直接结束会话，无 run 落盘 |
| test_chat_advance_injects_directive | advance_run 后 state.user_directives 含指令，planner context 渲染该段 |
| test_chat_mixed_intent | "这机制为什么有效？有前途就帮我规划" → 同一轮内 consult_expert 后 reply 提及可立项 |
| test_chat_tool_budget | 连续 tool_call 超限后强制 reply |
| test_slash_commands | /runs /status /use /confirm 行为正确 |
| test_chat_resume | 进程重启后 load_conversation 恢复 active_run_id 与上下文 |
| test_existing_suite_untouched | 现有 31 个测试全部通过（回归门槛） |

### 4.11 ResAgent 侧验收标准

1. `pytest -q` 全绿（含现有 31 个）。
2. `resagent chat --mock` 下完整走通附录 B 的四个剧本。
3. 真实 API 下：科学问答和 idea 讨论不产生任何 run 目录；明确立项经确认后产生标准 run 且与 `resagent run` 手工创建的 run 完全同构。

---

## 5. 跨模块共识：专家名片（agent.yaml）

### 5.1 名片即唯一跨模块契约

v1 唯一的跨模块约定就是名片。各专家的 Python API 保持现状（`advise()` / `run_code_task()` / `run_controller()`），ResAgent 的 adapter/chat_tools 继续按原生 API 调用。

### 5.2 agent.yaml 放置约定

每个专家仓库根目录放 `agent.yaml`。字段即 §4.2 的 `ExpertCard`。仓库名片 > config 覆盖 > ResAgent 内置默认。

### 5.3 名片写作要求（给路由 LLM 看的部分）

`description_for_router` 必须包含：**擅长什么 + 正反适用例**。路由质量直接取决于这段文字，写它等于写 prompt，不要写成营销文案。

### 5.4 v2 候选：统一信封（明确推迟）

`ExpertRequest{instruction, artifacts, budget}` / `ExpertResult{status, summary, payload, artifacts}` 信封的价值在于跨进程/远程调用和框架级统一处理。当前单进程 import 下，信封要求三个仓库同时改动却换不来行为收益，故推迟。触发重新评估的信号：需要把专家部署为独立服务、需要非 Python 调用方、或专家数量超过 5 个。

---

## 6. ExpAgent 改动任务书（交 ExpAgent 侧实施）

> 原则重申（P5）：ExpAgent 统一 `advise()` 架构**保持不变**，不加 mode 枚举。以下三处改动都是"让已有能力更好被对话层消费"。

### E1 — `conclusion` 可选化（schema 放宽）

**动机**：用户问"这个 attention 机制为什么有效"时，强行输出 `conclusion.status: supported/not_supported/...` 语义错误。统一 loop 本身能答，是输出 schema 太紧。

**改动**（`src/experiment_designer/models.py`）：

```python
class ScientificDecision(BaseModel):
    summary: str
    confidence: Literal["high", "medium", "low"]
    conclusion: ScientificConclusion | None = None   # 纯解释/问答类请求为 None
    # 其余字段不变
```

配套改动：
- `validator.py::validate_decision`：`conclusion is None` 时仅要求 `summary` 非空且 `len >= 50`（防止空答），不再要求 conclusion 字段齐全；`conclusion` 存在时走现有校验逻辑。
- `prompts.py` SYSTEM_PROMPT：加一段说明——"当请求是纯解释/问答/讨论类（用户没有要求实验设计或结论判定）时，`conclusion` 传 None，把解释写在 `summary`，论据写在 `evidence`，`experiment_plan` 传 None，`recommended_actions` 可以为空列表。"
- mock advisor 保持返回带 conclusion 的 decision（现有测试不受影响）。

**兼容性**：ResAgent adapter 只整体序列化存储 decision，不读 conclusion 内部字段，无需联动改动。

### E2 — `advise()` 运行旋钮

**动机**：对话层咨询不应默认触发 20 步全量文献调研。调用方控制预算，不控制语义。

**改动**（`src/experiment_designer/advisor.py`）：

```python
def advise(
    ctx: AdvisorContext,
    *,
    model: str = ...,
    api_base: str = ...,
    api_key_env: str = ...,
    mock: bool = False,
    trace_dir=None,
    run_dir=None,
    max_steps: int | None = None,          # 新增：覆盖 MAX_STEPS=20
    enable_paper_search: bool = True,      # 新增：False 时从 TOOLS 移除 search_papers/save_paper
) -> tuple[ScientificDecision, list[dict]]:
```

实现要点：`max_steps` 只影响 loop 终止条件（含宽限步逻辑基于 `max_steps` 重新计算）；`enable_paper_search=False` 时在 prompt 中说明"本轮不可用文献检索，请基于知识与所给 artifacts 回答"。

### E3 — 名片

ExpAgent 仓库根目录新增 `agent.yaml`（内容以 §4.4 内置 expagent 名片为准，`status: available`）。可选：CLI 增加 `expagent capabilities` 输出同样内容（nice-to-have，非必须）。

### E4 — 明确不做

- 不加 `AdvisorContext.mode` / `response_type` 枚举。
- 不改统一 agentic loop 结构。
- `situation` 仍是唯一语义入口；对话上下文由调用方组织进 situation 文本（ResAgent 会附上对话摘要与用户原话引用）。

### E-验收标准

| # | 检查 |
|---|------|
| 1 | 新测试：`conclusion=None` 的 decision 通过 validate_decision |
| 2 | 新测试：`max_steps=3` 时 loop 不超过 3+宽限步 |
| 3 | 新测试：`enable_paper_search=False` 时 LLM 可用工具列表不含 search_papers/save_paper |
| 4 | 现有测试全绿 |
| 5 | 真实调用：用纯问答 situation（如"解释 layer norm 为什么有效"）跑 advise，返回 conclusion=None 且 summary 为实质解释 |

---

## 7. CodingAgent 改动任务书（交 CodingAgent 侧实施）

> 这是唯一的能力新增。定性：**能力缺口**，不是 mode——CodingAgent 现有产物语义（PatchReport/changed_files/reviewer 空 diff 判负）决定了它无法回答代码问题，需要一条只读问答通路。对外契约形态与 `run_code_task` 对偶，不引入 mode 枚举。

### C1 — 代码问答能力

**新模型**（`src/coding_agent/models.py`）：

```python
class CodeQuestionSpec(BaseModel):
    workspace_path: Path
    question: str                          # 用户的代码问题
    output_dir: Path
    context_hint: str = ""                 # 可选：调用方提示去哪看（如 "重点看 train.py"）
    constraints: list[str] = Field(default_factory=list)
    allow_read_only_commands: bool = True
    max_steps: int = 12
    timeout_seconds: int = 600
    # 以下与 CodeTaskSpec 对齐：
    model: str = "gpt-4.1"
    api_base: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    max_context_tokens: int | None = None
    model_context_window_tokens: int | None = None

class Snippet(BaseModel):
    path: str
    start_line: int
    end_line: int
    content: str
    why: str = ""                          # 这段代码与问题的关系

class CodeExplanation(BaseModel):
    status: Literal["completed", "failed", "blocked", "needs_user_input"]
    answer: str                            # 主体回答（markdown）
    evidence_files: list[str] = Field(default_factory=list)
    relevant_snippets: list[Snippet] = Field(default_factory=list)
    uncertainty: str = ""                  # 不确定之处显式声明
    commands_run: list[CommandResult] = Field(default_factory=list)
```

**新入口**（`src/coding_agent/agent.py`，`__init__.py` 导出）：

```python
def run_code_question(spec: CodeQuestionSpec) -> CodeExplanation: ...
```

**实现路径建议**（最大限度复用现有架构）：

1. 复用 `run_step_controller`，但使用裁剪后的 QA 动作集：`list_tree / read_file / search / run_command(只读) / finish / ask_user`。`replace_text / insert_* / apply_patch / write_file` 不出现在 ACTION_SCHEMA 中。
2. **纵深防御**：除 prompt 不提供写动作外，safety 层增加 `read_only: bool` 标志，QA 通路下写动作即使被注入也直接拒绝。`run_command` 只读白名单建议保守起步：`ls, cat, head, tail, grep, rg, find, wc, file, pwd, tree` + 现有黑名单继续生效。
3. **不经过 reviewer**：QA 通路没有 diff 预期，跳过空 diff 判负逻辑。
4. 新 prompt `QA_SYSTEM`：要求回答带证据（文件+行号）、显式声明不确定性、禁止修改任何文件。
5. 产物：`output_dir/explanation.md` + `state.json`（与 PatchReport 通路同构）。

### C2 — 名片

CodingAgent 仓库根目录新增 `agent.yaml`：

```yaml
name: codingagent
role: coding_agent
description_for_router: |
  程序员。两个能力：
  1) code_modification：repo 级代码修改（补日志、修 bug、加配置、API 兼容修复），
     需要明确的 task_goal 和 verify_commands。
  2) code_question：只读代码问答（训练入口在哪、loss 怎么算、报错定位），
     回答附文件/行号证据，不修改任何文件。
capabilities: [code_modification, code_question]
side_effects: none_for_question__workspace_for_modification  # 见下注
input_contract: run_code_task(CodeTaskSpec) -> PatchReport; run_code_question(CodeQuestionSpec) -> CodeExplanation
status: available
```

注：一个仓库两个能力、副作用不同，按 §4.4 的方式拆成两张名片登记（`codingagent` / `codingagent_qa`），各自标 `side_effects`。仓库 agent.yaml 可用 `extra_cards` 列表表达第二张名片，或直接由 ResAgent config 补充——两种均可，以实现简单为准。

### C-验收标准

| # | 检查 |
|---|------|
| 1 | 新测试：QA 通路全程零文件写入（workspace 前后 diff 为空） |
| 2 | 新测试：写动作在 QA 模式下被 safety 层拒绝（注入攻击式用例） |
| 3 | 新测试：`run_command` 非白名单命令被拒绝 |
| 4 | 现有测试全绿 |
| 5 | golden cases（真实 API，对一个示例 ML repo）：①训练入口在哪 ②loss 怎么算 ③数据增强在哪定义 ④`AttributeError: ...` 可能是哪行导致 ⑤模型结构在哪。要求 answer 正确、evidence_files 真实存在、snippets 行号有效 |
| 6 | `python -c "from coding_agent import run_code_question, CodeQuestionSpec, CodeExplanation"` 可导入 |

---

## 8. ReproAgent

零代码改动。仅建议在仓库根目录补 `agent.yaml`（内容见 §4.4 内置名片）。若仓库 owner 暂不方便动，ResAgent 内置名片长期有效，不阻塞任何事情。

---

## 9. 里程碑

| 里程碑 | 内容 | 依赖 | 验收 |
|--------|------|------|------|
| **M1** | ResAgent 对话层 MVP：chat_models/conversation/capabilities/chat_tools/chat + CLI + 测试；consult_expert 仅 expagent 通路（用内置名片 + advise_adhoc） | 无 | §4.11 |
| **M2** | ExpAgent E1-E3 落地并接入：consult 传 max_steps；问答类 decision（conclusion=None）正常呈现 | ExpAgent 侧 | §6 验收 + chat 真实问答剧本通过 |
| **M3** | CodingAgent C1-C2 落地并接入：codingagent_qa 翻牌 available，consult_expert 支持代码问答 | CodingAgent 侧 | §7 验收 + 代码问答剧本通过 |
| **M4** | 硬化：`--resume` 会话恢复、registry 名片校验（schema 错误给出人读报错）、事件回放工具、README 更新 | M1-M3 | 全部测试 + 断电恢复演示 |

M1 不依赖任何其他仓库的改动（内置名片 + 现有 `advise()` 即可跑通全流程），三个模块可完全并行开发。

---

## 10. 反模式清单（明确不做）

1. **不做前置意图分类器**（原 SYSTEM_INTERACTION_ARCHITECTURE.md 的 IntakeRouter 结构化意图输出方案）。路由 = 对话 loop 内的工具选择。
2. **不给专家加 mode 枚举**（`AdvisorContext.mode` 等）。语义判断留在专家内部；调用方只写自然语言 instruction + 运行旋钮。
3. **对话层不做科学推理**。它是分诊台，不替 ExpAgent 思考；遇到拿不准的科学问题应该 consult 或追问，而不是自己编答案。
4. **未经确认不创建 ResearchRun**。propose → 展示 brief → 显式确认，是不可跳过的闸门。
5. **不把专家输出压成字符串**。ScientificDecision/PatchReport/CodeExplanation 的富类型完整保留，信封协议 v2 再议。
6. **不造 agent 框架/平台**。registry 只是名片加载器；当发现自己在写"插件生命周期管理"时，停下来。
7. **slash 命令不经过 LLM**。确定性操作给确定性入口。

---

## 附录 A：CHAT_SYSTEM prompt 草案

```
You are ResAgent's conversation layer — the front desk of a multi-expert
scientific research system.

You are NOT a researcher. You route, clarify, and present. Deep reasoning
lives in the experts.

## Experts available

{registry.router_descriptions() 注入，形如:}
- expagent (scientific_advisor, side_effects: none): 科学顾问。擅长...
- codingagent_qa (coding_advisor, side_effects: none, status: planned): ...
- reproagent (reproduction_agent, side_effects: workspace_and_environment): ...

## Rules

1. Answer directly when you can (greetings, meta questions about the system,
   simple factual replies). This is Tier 0 — no tools needed.
2. For scientific questions or idea discussion, call consult_expert with
   expert="expagent". Quote the user's original words in the instruction;
   add conversation context when relevant. Never fabricate scientific
   claims yourself.
3. For code questions about a repo: if codingagent_qa is available, consult
   it; if planned, tell the user the capability is coming and offer
   alternatives.
4. NEVER create a research run without explicit user confirmation.
   When the user wants to start a project, call propose_research_run with a
   brief distilled from the conversation, then present the full brief and
   ask for confirmation. Only after the user confirms ("确认", "开始",
   /confirm) call start_research_run.
5. When the user references an existing project ("继续上次那个实验"),
   use list_runs/inspect_run to identify it, then advance_run with the
   user's instruction quoted verbatim.
6. If the request is ambiguous between consultation and action, ask a
   clarifying question (reply with the question). Asking is cheap;
   misrouting is expensive.
7. One tool call per response. Respond with exactly one JSON object:
   {"type": "tool_call", "tool": ..., "params": {...}, "reason": "..."}
   or {"type": "reply", "text": "..."}

## Current conversation snapshot

{active_run_id / pending_brief / recent_artifacts / recent events}
```

## 附录 B：典型会话剧本（M1 验收用例）

**剧本 1 — 科学问答（Tier 0/1，无 run）**
```
用户: SE attention 为什么有效？
→ tool_call: consult_expert(expert=expagent, instruction="用户问：'SE attention 为什么有效？'
   这是纯原理问答，请给出解释，无需实验设计。")
→ reply: [呈现 ExpAgent 的解释摘要]
断言: workspace 下无新 run 目录；conv.recent_artifacts 新增一条 expagent 产物
```

**剧本 2 — 模糊 idea 讨论（Tier 1，无 run）**
```
用户: 我想把 diffusion 用到时序异常检测，有戏吗？
→ tool_call: consult_expert(expagent, ...)
→ reply: [可行性分析 + 风险 + "如果你想推进，我可以帮你立项"]
断言: 无 run 创建
```

**剧本 3 — 立项（Tier 2，晋升边界）**
```
用户: 就按刚才讨论的方向开始做实验
→ tool_call: propose_research_run(brief={goal: "验证 diffusion 时序异常检测...",
   hypothesis: ..., context_summary: [前几轮蒸馏]})
→ reply: [展示 brief 全文] "请确认是否立项？"
用户: 确认
→ tool_call: start_research_run()
→ reply: "已创建 run res-20260808-xxxx，首轮 ExpAgent 咨询完成，建议先复现 baseline..."
断言: state.json 落盘；research_goal 含 brief 全文；active_run_id 已设置
```

**剧本 4 — 继续已有 run（Tier 2）**
```
用户: 上次那个实验跑得怎么样了？失败了的话就换个 baseline 继续
→ tool_call: list_runs() → inspect_run(res-...)
→ reply: [状态摘要] "要我按'换 baseline'推进吗？"
用户: 对，继续
→ tool_call: advance_run(run_id, instruction="失败了的话就换个 baseline 继续")
断言: state.user_directives 含该指令原文；planner 下一步行动与指令一致
```

## 附录 C：与 SYSTEM_INTERACTION_ARCHITECTURE.md 的差异

| 原方案 | 本文档 | 理由 |
|--------|--------|------|
| IntakeRouter 输出结构化 intent YAML，再分发 | 对话 loop 内工具选择即路由 | 混合/漂移意图、分类错误不可恢复、双套 NLU 维护（§2 P1） |
| 固定 intent 类型枚举（11 种） | 专家名片 + 5 个工具，能力表随名片扩展 | 加 PaperAgent = 注册名片，不动枚举与分发逻辑 |
| ExpAgent 增加 `AdvisorContext.mode` | 不加 mode；schema 放宽 + 运行旋钮（§6） | P5：语义判断留在专家内部 |
| Capability Descriptor 先写在 ResAgent 配置 | agent.yaml 随仓库 + 内置默认兜底 | 单一事实来源，仓库独立可复用 |
| ConversationState/ResearchState 分离 | **保留**（§4.2/§4.7） | 原方案最有价值的判断 |
| 晋升/确认思想（requires_confirmation） | **保留并强化**为 ResearchBrief + Tier 模型（§2 P2/P3） | — |
