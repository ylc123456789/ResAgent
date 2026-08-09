# ResAgent 对话层交接文档

**日期**: 2026-08-08
**状态**: M1–M3 已完成并验证（对话层 + 双专家咨询通路 + 立项晋升）
**权威设计文档**: [CONVERSATION_LAYER_DESIGN.md](./CONVERSATION_LAYER_DESIGN.md)（方案与任务书以它为准，本文档只记实施现状与遗留问题）
**测试**: 70 passed（31 个存量 + 39 个新增），真实 API 端到端验证通过

---

## 1. 一句话现状

`resagent chat` 已是可用的统一对话入口：科学/代码问答走只读专家咨询（不建 run），模糊 idea 只讨论不落项目，明确立项经"brief 展示 → 用户确认"闸门后复用现有 orchestrator 执行。存量 `init/run/step/status` 批处理通路零行为变化。

## 2. 环境与常用命令

```bash
# 环境：WSL Ubuntu-D，conda env "ResAgent"（Python 3.11）
source ~/miniconda3/etc/profile.d/conda.sh && conda activate ResAgent
cd /home/cyl/ResAgent && pip install -e ".[dev]"

# 产物根目录（可选）：不设则默认 ./runs（相对当前目录）
# 解析链：--workspace CLI > $RESAGENT_WORKSPACE > config.yaml workspace.default_runs_dir > ./runs
export RESAGENT_WORKSPACE=/root/autodl-tmp/resagent-workspace/runs

python -m pytest tests/ -q          # 70 passed，全程 mock，无需 API key

# 真实运行（DEEPSEEK_API_KEY 在 ~/.bashrc 中，bash -lc 会自动加载）
resagent chat --workspace runs/ --config config.yaml
resagent chat --workspace runs/ --resume conv-20260808-xxxx   # 恢复会话
resagent chat --mock --workspace /tmp/demo                    # 离线演示全剧本
```

`config.yaml` 关键段（agents 路径 + chat 调参）：

```yaml
agents:
  expagent_path: /home/cyl/ExpAgent
  codingagent_path: /home/cyl/CodingAgent
  reproagent_path: /home/cyl/reproagent
chat:
  consult_max_steps: 12      # 专家咨询步数上限（开文献检索时 6-8 必耗尽，勿调低）
  default_advance_steps: 3   # start/advance_run 每次执行的 run loop 步数
  max_steps_per_turn: 5      # 硬上限，防止对话层长跑阻塞
  max_tool_calls_per_turn: 4
```

## 3. 本次交付文件地图

| 文件 | 职责 | 备注 |
|------|------|------|
| `src/resagent/chat_models.py` | ConversationState / ConversationEvent / ResearchBrief / ExpertCard | 含宽容 validator（§5 问题 3） |
| `src/resagent/conversation.py` | 会话持久化：events.jsonl（权威）+ conversation.json（快照）+ rebuild | 与 state.py 模式对偶 |
| `src/resagent/capabilities.py` | 名片注册表：内置 4 张 < config `agents.cards` < repo `agent.yaml` | 非标准 side_effects 强制转 workspace 并告警 |
| `src/resagent/chat_tools.py` | 5 个对话工具 | consult 是纯 advisory，绝不建 task |
| `src/resagent/chat.py` | ChatLoop（路由=工具选择）+ REPL + slash 命令 | scripted_responses 是测试钩子 |
| `src/resagent/llm.py` | 对话层 LLM 传输 | 与 Planner 传输并存，未合并 |
| `src/resagent/prompts.py` | 新增 CHAT_SYSTEM；CONTROLLER_SYSTEM 加 User Directives 段 | |
| `src/resagent/models.py` | ResearchState 增加 `user_directives`（唯一存量侵入点） | |
| `src/resagent/context.py` | planner context 渲染最近 3 条 user_directives | |
| `src/resagent/config.py` | ChatConfig + agents.cards | |
| `src/resagent/main.py` | `resagent chat` 子命令 | |
| `src/resagent/adapters/expagent.py` | `advise_adhoc()`（用 E2 旋钮 max_steps/enable_paper_search） | |
| `src/resagent/adapters/codingagent.py` | `ask_adhoc()`（对接 `run_code_question`） | |
| `tests/test_chat_{models,loop,tools}.py`, `test_conversation.py`, `test_capabilities.py` | 39 个新用例 | |

## 4. 真实 API 验证结论（2026-08-08，DeepSeek deepseek-v4-pro）

四个验收剧本全部真实跑通：科学问答→expagent 咨询；代码问答→codingagent_qa（自动提取 workspace_path）；模糊 idea→仅咨询不立项；立项→propose→确认→建 run（真实 ExpAgent 规划 4 任务）→"上次那个实验"回指解析正确。

**测试抓出并已修复的 4 个问题**（都有回归测试）：

1. **slash 命令吞绝对路径**：`/home/cyl/ResAgent ...` 被误判为未知命令。修复：未知 `/` 开头输入若为多 token 或路径形态则放行给 LLM。勿回退——WSL 用户高频踩中。
2. **对话层转述编造技术事实**：专家答案正确（`src/resagent/main.py`），chat LLM 复述成虚构的 `res_agent/cli.py`。修复：CHAT_SYSTEM 强制"文件路径/命令/配置键逐字引用"。**这是 NL 契约架构的固有损耗，若再发现转述失真，优先加 prompt 规则或让工具结果直接透出，不要改专家接口。**
3. **ResearchBrief schema 摩擦**：LLM 把 `constraints` 传成散文字符串。修复：`chat_models.py` 宽容 validator（str→list、裸路径→artifact）。
4. **咨询步数预算**：`consult_max_steps ≤ 8` 开文献检索时 ExpAgent 必耗尽。默认已调 12。

## 5. 已知限制（有意为之或待办）

- **对话层 LLM 传输无重试**（`llm.py` 单次调用）；Planner 传输也没有。网络抖动会直接报错进 error 事件。
- **`scratch_summary` 字段保留但未启用**：旧事件用确定性单行折叠，无 LLM 压缩。事件超过 ~40 条后信息有损。
- **start/advance_run 会阻塞 REPL**（受 max_steps_per_turn 限制）。repro 类长任务的异步化是后续工作。
- **真实 API 下 advance_run 未做深度验证**（真实测试为控副作用只跑了 1 步咨询）。
- 管道输入时 REPL 的 `you>` 回显为空（纯展示问题）。

## 6. 待办（按优先级）

### 6.1 跨仓库问题（转达对应 owner，非 ResAgent 改动）

1. **CodingAgent：`CodeExplanation` 证据字段恒为空**。`run_code_question` 当前只做 `PatchReport.summary → answer` 映射，`evidence_files / relevant_snippets / uncertainty` 从未填充，不满足 C-验收第 5 条（行号级证据）。需从 step 记录回填实际读过的文件与片段。
2. **CodingAgent：`agent.yaml` 的 side_effects 值不规范**（`none_for_question__workspace_for_modification`）。ResAgent 已宽容强制转 `workspace` 并告警，行为安全；建议改标准词表或按设计文档 §7-C2 拆成 `codingagent` + `codingagent_qa` 两张名片。

### 6.2 ResAgent 侧（M4 硬化）

- 事件回放/导出工具（`resagent chat --export <conv_id>` 之类）。
- 名片加载失败的人读报错已部分具备（registry.warnings），可加 `--check-config` 诊断命令。
- scratch_summary 的 LLM 压缩（超窗口时）。
- 长跑任务异步化（后台跑 run，对话层轮询）。

## 7. 不得回退的设计红线（改代码前先读设计文档 §10）

1. 不做前置意图分类器——路由必须是对话 loop 内带完整上下文的工具选择。
2. 不给专家加 mode 枚举——语义判断留在专家内部（ExpAgent 仓库的 SCIENTIFIC_ADVISOR_REFACTOR.md 已否决过一次）。
3. 未确认不建 ResearchRun——propose → 展示 brief → 显式确认，不可跳过。
4. consult_expert 必须是 advisory——禁止从 recommended_actions 直接建 task。
5. slash 命令不过 LLM。

## 8. 新功能开发速查

- **加一个新专家**：对方仓库放 `agent.yaml` → ResAgent 内置名片可加可不加 → 若是只读咨询，在 `chat_tools._consult_expert` 加一个 dispatch 分支 + adapter 加 `*_adhoc` 方法。路由层零改动。
- **改对话行为**：优先改 `prompts.py::CHAT_SYSTEM`，其次才动代码。
- **写对话层测试**：用 `ChatLoop(scripted_responses=[...])` 注入 LLM 响应队列（见 `test_chat_loop.py` 的 `tc()/rp()` helper），不要用关键词 mock 写精细断言。
