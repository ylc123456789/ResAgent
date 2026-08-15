# 系统边界审计报告 — 文件 I/O 与产物管理

**日期**: 2026-08-08
**审计范围**: ResAgent, ExpAgent, CodingAgent, ReproAgent 全部源码
**方法**: 4 个并行 agent 逐文件审计 + grep 交叉验证
**审计人员默认立场**: 只读分析，不修改代码

---

## 1. 总体评价：边界清晰，无严重污染

四个模块的文件 I/O 边界**基本干净**。没有发现模块 A 直接写入模块 B 仓库目录的情况。所有产物都在各模块自己的 workspace 下，跨模块调用通过"调用方建外层目录、被调用方写内部内容"的契约正确隔离。

### 1.1 跨模块调用链路径所有权

| 调用链 | 谁建目录 | 谁写内容 | 越界？ |
|--------|---------|---------|--------|
| ResAgent → ExpAgent.advise() | ResAgent 建 `expagent/decision_NNN/` | ExpAgent 写内部文件 | ✅ |
| ResAgent → CodingAgent.run_code_task() | ResAgent 建 `codingagent/code_NNN/` | CodingAgent 写全部输出 | ✅ |
| ResAgent → ReproAgent.run_controller() | ResAgent 建 `reproagent/repro_NNN/` | ReproAgent 建完整 workspace 子树 | ✅ |
| ResAgent → ExpAgent.advise_adhoc() | ResAgent 建 `conversations/<id>/experts/` | ExpAgent 写咨询结果 | ✅ |
| ResAgent → CodingAgent.ask_adhoc() | ResAgent 建 `conversations/<id>/experts/` | CodingAgent 写 QA 结果 | ✅ |
| ReproAgent → CodingAgent (内部) | ReproAgent 建 `patches/coding_agent_NN/` | CodingAgent 写补丁 | ✅ (在 ReproAgent workspace 内) |

---

## 2. 各模块详细审计

### 2.1 ResAgent ✅ 干净

**无硬编码绝对路径**。所有写入在 `workspace_root` 下。

```
<workspace_root>/
├── <run_id>/                              # orchestrator.py init_run()
│   ├── state.json                         # state.py (原子写: tmp → rename)
│   ├── execution_plan.md                  # report.py
│   ├── summary.md                         # report.py
│   ├── artifacts/index.json              # report.py
│   ├── expagent/decision_NNN/             # expagent adapter
│   │   └── scientific_decision.json
│   ├── codingagent/code_NNN/              # codingagent adapter
│   │   └── state.json (CodingAgent 写)
│   └── reproagent/repro_NNN/              # reproagent adapter
│       └── repo_workspace/ (ReproAgent 写)
│
└── conversations/<conv_id>/               # conversation.py
    ├── conversation.json                  # 原子写快照
    ├── events.jsonl                       # append-only 权威日志
    ├── experts/
    │   ├── expagent_NNN/                  # chat_tools consult
    │   └── codingagent_qa_NNN/            # chat_tools consult
    └── briefs/brief_<ts>.json             # propose 归档
```

**设计亮点**:
- `conversations/` 和 `runs/` 通过 `ChatConfig.conversations_dirname` 正确隔离
- `_list_runs` 过滤掉 `conversations/` 目录
- 状态文件原子写入（`tempfile` → `os.replace`）

### 2.2 ExpAgent ⚠️ 2 个小问题

**问题 1** (低): `llm.py:219,227` — mock 模式中有硬编码路径
```python
"workspace_path": "/home/cyl/my_project"
```
仅影响 mock 模式，不影响真实调用。建议改为 `"./"` 或 `"<user_project_path>"`。

**问题 2** (低): `tools.py:110` — `save_paper` 的 `output_dir` 默认值 `"papers"`
```python
def save_paper(..., output_dir: str | Path = "papers"):
```
当前 advisor 总是显式传入 `papers_dir`，所以死代码路径不会触发。但如果有人直接调用 `save_paper()` 不传 `output_dir`，会在 CWD 下创建 `papers/`。建议移除默认值或显式要求必传。

**其他**:
- `main.py:580-586` 用 `__file__` 推导项目根目录——依赖安装方式（editable vs site-packages），但 fallback 到 CWD 所以安全
- `runs/` 下有 19 个历史运行产物（未清理），`.gitignore` 已排除

### 2.3 CodingAgent ✅ 干净

**无硬编码绝对路径**。所有产物在 `output_dir` 下：

```
<output_dir>/
├── state.json                         # report.py
├── patch_report.md                    # report.py
├── diff.patch                         # report.py (最终 diff)
├── initial_diff.patch                 # report.py (初始 diff)
└── logs/
    ├── action_NN.json                 # 每步 action 记录
    ├── step_NN/verify_NN.stdout/stderr  # 验证输出
    ├── failed_patch_NN_NN.patch/.stderr # 修复记录
    └── step_NN_finish_verify/          # 完成时自动验证
```

**一个设计注意点** (非 bug): `runner.py:28` 执行 `verify_commands` 时使用 `shell=True`，命令本身可以写入任意位置（不受 `output_dir` 限制）。这是 CodingAgent 的执行模型决定的——验证命令需要在 repo 内运行并可能产生文件。ResAgent 通过 Tier-2 确认闸门保护。

### 2.4 ReproAgent ✅ 干净

**无硬编码绝对路径**（除 conda 可执行文件探测路径）。所有产物在 `task.workspace_dir` 下：

```
<workspace_dir>/
├── repo/                             # git clone (实验目标仓库)
├── logs/                             # 所有 stdout/stderr
│   ├── clone.stdout/stderr
│   ├── conda_setup.stdout/stderr
│   ├── probe_NN_NN.stdout/stderr
│   ├── experiment_NN_NN.stdout/stderr
│   ├── environment_audit.stdout/stderr
│   └── llm_<ts>_stepN.prompt.txt/.response.txt
├── context/context_summary.md        # 仓库/论文/硬件上下文
├── .cache/pip/                       # pip 缓存重定向
├── patches/coding_agent_NN/          # CodingAgent 集成产物
├── state.json                        # report.py
└── result.md                         # report.py
```

**外部写入** (必要且不可避免):
- Conda 环境: `~/anaconda3/envs/repro_*`（由 conda 管理，不由 ReproAgent 直接控制路径）
- 可选 repo 缓存: `{repo_cache_dir}/{slug}/`

**一个设计注意点**: `context.py:28,45` 使用 `shutil.rmtree` 删除失败的 clone——这是 workspace 内部的清理操作，不跨模块。

---

## 3. 交叉问题

### 3.1 全系统文件树（一次完整 run 的产物总览）

```
<workspace_root>/                              # ResAgent 拥有
│
├── conversations/<conv_id>/                   # ResAgent 对话层
│   ├── conversation.json, events.jsonl
│   ├── experts/expagent_NNN/scientific_decision.json   # ExpAgent 写
│   ├── experts/codingagent_qa_NNN/code_explanation.json # CodingAgent 写
│   └── briefs/brief_<ts>.json                # ResAgent 写
│
└── <run_id>/                                  # ResAgent 编排层
    ├── state.json                             # ResAgent 写
    ├── execution_plan.md, summary.md          # ResAgent 写
    ├── artifacts/index.json                   # ResAgent 写
    │
    ├── expagent/decision_NNN/                 # ResAgent 建目录
    │   ├── scientific_decision.json           # ResAgent 写 (serialized decision)
    │   ├── experiment_plan.yaml               # ExpAgent 写
    │   ├── validation_report.md               # ExpAgent 写
    │   ├── state.json                         # ExpAgent 写
    │   ├── logs/llm_*.prompt.txt/.response.txt # ExpAgent 写 (trace)
    │   └── papers/<slug>.md                   # ExpAgent 写 (文献检索)
    │
    ├── codingagent/code_NNN/                  # ResAgent 建目录
    │   ├── state.json                         # CodingAgent 写
    │   ├── patch_report.md, diff.patch        # CodingAgent 写
    │   └── logs/action_NN.json, step_NN/      # CodingAgent 写
    │
    └── reproagent/repro_NNN/                  # ResAgent 建目录
        └── repo_workspace/                    # ReproAgent 建
            ├── repo/                          # ReproAgent clone
            ├── logs/                          # ReproAgent 写
            ├── patches/                       # ReproAgent → CodingAgent
            ├── state.json, result.md          # ReproAgent 写
            └── .cache/pip/                    # ReproAgent pip cache
```

**所有权规则清晰**: 谁建目录谁拥有，子模块只在被分配的子目录内写文件。

### 3.2 命名不一致

| 不一致 | 模块 | 详情 |
|--------|------|------|
| 状态文件扩展名 | ResAgent: `state.json`<br>ExpAgent: `state.json` + `scientific_decision.yaml`<br>CodingAgent: `state.json` + `patch_report.md`<br>ReproAgent: `state.json` + `result.md` | 各模块用 `.json` 存状态是统一的，但科学决策用 `.yaml` / `.json` 不统一 |
| 日志目录命名 | ResAgent: `logs/` 在 CodingAgent 子目录内<br>ExpAgent: `logs/` 在 `run_dir/` 下<br>ReproAgent: `logs/` 在 workspace 根 | 各自独立命名，不冲突 |
| run_id 格式 | ResAgent: `res-YYYYMMDD-xxxxxx`<br>ExpAgent: `YYYYMMDD-HHMMSS`<br>ReproAgent: `repro-YYYYMMDD-HHMMSS-xxxxxx` | 前缀不同但都包含时间戳，不会碰撞 |

### 3.3 潜在风险点

| 严重度 | 模块 | 问题 | 建议 |
|--------|------|------|------|
| 低 | ExpAgent | `llm.py:219,227` mock 数据有 `/home/cyl/my_project` | 改为相对路径或占位符 |
| 低 | ExpAgent | `tools.py:110` `output_dir` 默认 `"papers"` 可能泄露到 CWD | 移除默认值 |
| 信息 | CodingAgent | `verify_commands` 可写任意位置（shell=True）| 设计如此，Tier-2 确认保护。可考虑加 `--sandbox` flag |
| 信息 | ReproAgent | Conda 环境路径不由 ReproAgent 控制 | 必要的系统依赖，非代码问题 |
| 信息 | ExpAgent | `main.py:580` 依赖 `__file__` 推导项目根 | 已有 CWD fallback，安全 |

---

## 4. 结论

1. **无跨模块文件污染** — 四个模块严格在各自 workspace 内写入
2. **无硬编码绝对路径影响生产** — 仅 ExpAgent mock 数据有一处，不影响真实运行
3. **产物目录结构清晰** — 每个模块的 workspace 布局一致、可预测、可审计
4. **命名基本统一** — 都用 `state.json`、`logs/`，差异仅在模块特有产物名
5. **需清理的非代码产物** — ExpAgent `runs/` 下有 19 个历史 run（gitignore 已排除，非代码问题）

**一句话**: 目前系统的边界管理是合格的。如果要进一步规范化，建议做：
- ExpAgent 修掉 mock 硬编码路径
- 统一 `scientific_decision` 的序列化格式（全用 `.json`）
- 给 CodingAgent 加一个 sandbox 模式开关（长线）
