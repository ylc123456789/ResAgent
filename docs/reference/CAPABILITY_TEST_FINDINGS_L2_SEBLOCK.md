# L2 测试问题清单（SE block 原创代码）

- **日期**: 2026-08-19
- **测试**: L2 能力验证（给 ResNet18 加 SE block，方案见 `CAPABILITY_VALIDATION_TEST_PLAN.md` §12）
- **最终状态**: ✅ **核心目标达成**（CodingAgent 成功 clone + 实现 SE block + 验证），共暴露 5 个问题，其中 4 个已修复、1 个待解决
- **证据现场**（服务器）:
  - ResAgent 状态: `/root/autodl-tmp/resagent-workspace/runs/mixup-l2-seblock/state.json`
  - 日志: `/root/autodl-tmp/capability-test/run_l2.log`、`answer_l2.log`、`answer_final.log`

---

## 结论速览

| # | 问题 | 严重度 | 模块 | 状态 |
|---|---|---|---|---|
| 1 | `infer_workspace_path` 把相对路径误判为绝对路径（workspace=`/`） | 高 | ResAgent | ✅ 已修复 |
| 2 | CodingAgent 误拦 `models` 路径 | 中 | CodingAgent | ✅ 已修复 |
| 3 | 大依赖下载超时（不可配置） | 中 | ResAgent / reproagent | ✅ 已修复 |
| 4 | ExpAgent 分析后反复追加实验、不收敛 | 中 | ExpAgent | ⏳ 待解决 |
| 5 | answer 不传导成指令（用户"停止"被忽略） | 高 | ResAgent | ✅ 已修复 |

---

## Bug 1（已修复）：`infer_workspace_path` 把相对路径误判为绝对路径，workspace 被推断成 `/`

**文件**: `ResAgent/src/resagent/adapters/expagent/task_conversion.py`
**位置**: `infer_workspace_path` 函数，第 186–199 行

### 现象

task_002（`modify_code`）的 `input.workspace_path` 被推断成了 `"/"`（根目录），CodingAgent 用 `repo.rglob("*")` 扫描根目录，遍历 `/proc` 撞上：

- `[Errno 1] Operation not permitted: '/proc/1/map_files/...'`
- `[Errno 2] No such file or directory: '/proc/1673'`

### 根因

goal 文本里的「在 **models/resnet.py** 里实现 SE block」，正则 `r"(/[^\s,;]+)"` 匹配到了 `models/resnet.py` 中间那个 `/`，把 `"/resnet.py"` 当成了绝对路径，取其父目录返回 `"/"`。

### 修复（已实施）

两处：

1. 正则加 `(?<!\S)`，要求路径在 token 边界（行首/空白后），`models/resnet.py` 中间的 `/` 不再被误匹配。
2. **更根本的一层**：给 `modify_code` 任务线程 goal 里的 repo_url（`_extract_repo_url`），让 CodingAgent 自己 clone（下游 `CodeTaskSpec.repo_url` + `_prepare_workspace` 的 git clone 早已支持）。

> commit: ResAgent `a54ed96`（正则）、`516d043`（repo_url 线程）。

---

## Bug 2（已修复）：CodingAgent 误拦 `models` 路径

**文件**: `CodingAgent/src/coding_agent/runtime/safety.py`
**位置**: `BLOCKED_PATH_PARTS` 第 22 行的 `"models"`

### 根因

`"models"` 作为目录名太宽泛——mixup-cifar10 的 `models/` 放的是模型代码（`resnet.py` 等），不是权重。目录级拦 `models` 误伤了代码目录（权重文件已由 `BLOCKED_SUFFIXES` 的后缀拦住了）。

### 修复（CodingAgent AI 已实施）

从 `BLOCKED_PATH_PARTS` 移除 `"models"`。

> commit: CodingAgent `5a2204e`。

---

## 问题 3（已修复）：大依赖下载超时

### 现象

ReproAgent 装 torch 2.6.0（约 2.5GB CUDA 依赖）从 aliyun 镜像下载仅约 1.3 MB/s，超过命令超时 1800s，反复超时。

### 根因

1. **超时不可配置**：`ReproTask.timeout_seconds` 一直是默认 1800s，编排流程没从 config 线程下来。
2. **pip `--no-cache-dir`**：禁了缓存，重装无法复用 wheel。

### 修复（已实施）

1. 超时默认 1800s → **3600s**（reproagent `models.py` + ResAgent `adapters/reproagent.py`）。
2. 拆分参数：新增 `Budget.max_code_repairs`（默认 5），`schedule_coding_repair` 改用它，与 `max_task_retries`（瞬时重试，仍 2）解耦——之前"重试次数"和"代码修复次数"共用一个旋钮不合理。

> commit: ResAgent `598d081`、reproagent `cfa8843`。

---

## 问题 4（待解决）：ExpAgent 分析后反复追加实验、不收敛

### 现象

ExpAgent 分析出"SE 不涨点"（baseline 95.84% vs SE 95.85%）后，**没有下结论**，而是不断追加任务：

1. 先提出多 seed 方差分析（task_008，合理，但做一次就该够）；
2. 做完 50-epoch 多 seed 后，又要求 resume 到 200 epoch（task_012 execute）；
3. 还揪着 goal 里的 94.6% 数字反复追问（task_010/task_011 两次 ask_user）。

这导致 run 在"分析 → 追加实验 → 再分析 → 再追加"里打转数小时，迟迟不 finish。和 L1 的 ReproAgent"换 seed 反复重跑"是**同一类病**——LLM 缺"零效应也是有效结论"的收敛判据。

### 根因

ExpAgent 的 prompt 没有"结论准则"：当效应量低于阈值（如 <0.5%）时，它不知道"直接下结论'无显著提升'"，而是默认"再做一组实验确认"。

### 建议修复（供 ExpAgent AI 决策）

1. **prompt 层（最根本）**：给 ExpAgent 加"结论准则"，类比 L1 的 stop-loss：
   - "效应量低于阈值就下结论'无显著提升'并 finish，不要追加实验"；
   - "多 seed 方差分析做一次即可；做完若差值仍 ~0 就收口"；
   - "goal 里的参考数字可能是不同协议的旧值，别死磕"。
2. **预算层（兜底）**：加"追加实验轮次上限"，防止无限升级。

> 注：这是跨模块的系统性问题（L1 ReproAgent、L2 ExpAgent 都是"LLM 缺收敛判据"），值得在 prompt 层统一考虑。

---

## 问题 5（已修复）：answer 不传导成指令（用户"停止"被忽略）

**文件**: `ResAgent/src/resagent/persistence/state.py`
**位置**: `submit_user_response` 函数

### 现象

用户通过 `resagent answer` 回复"直接 finish、不要再追加实验"后，controller **依然去跑 pending 的 task_012/013**，没有收口。

### 根因

链路断在中间：

- ✅ `CONTROLLER_SYSTEM`（`controller/prompts.py:84-88`）已写"User Directives take priority, follow them"；
- ✅ `build_controller_context`（`context/builder.py:56-59`）已注入 `state.user_directives`；
- ❌ 但 `submit_user_response` **只把回答记进 `answered_questions`（死胡同），没写进 `state.user_directives`**。

所以用户的回答根本进不了"User Directives"这条管道，controller 的 LLM 看不到，也就无从遵循。

### 修复（已实施）

`submit_user_response` 现在把回答**同时追加进 `state.user_directives`**（作为 `UserDirective`）。这样用户通过 answer 说的任何话（停止、换方向、改约束）都会成为 controller 能看到的指令并优先遵循。

> commit: ResAgent `d6ecd70`。附带回归测试 `test_submit_user_response_becomes_user_directive`。

---

## 附：L2 测试的正面收获 + 最终结果

**核心目标全部达成**：

1. **repo_url 线程**：CodingAgent 成功 clone（修复 Bug 1 后）。
2. **原创代码能力**：CodingAgent 独立实现 SE block（`se_resnet18` 变体，`use_se` 开关、r=16），不再 /proc 报错。
3. **复现对比**：baseline（ResNet18）与 SE（se_resnet18）各 200 epoch，同协议（seed=20170922、alpha=1 mixup）。
4. **诚实分析**：ExpAgent 正确识别"SE 不涨点"（95.84% vs 95.85%，持平），并主动提出多 seed 方差检验（虽然随后陷入了"反复追加"的问题 4）。

**最终科学结论**：

> SE 通道注意力模块在 alpha=1 mixup 协议下与 baseline 持平（95.85% vs 95.84%），不构成可测量的提升。这与 SE 原论文（在 ERM 下涨 ~1-2%）不矛盾——mixup 本身是强正则，可能吸收了 SE 的收益（SE 与 mixup 冗余）。多 seed 50-epoch 甚至显示 SE 略低（89.8% vs 90.7%），进一步支持"SE 起步慢、在 mixup 下无明显增益"。

**能力验证小结**：

| 能力 | 结果 |
|---|---|
| 编排闭环（repo_url 线程 → clone → 改码 → 复现 → 分析） | ✅ 全链路走通 |
| 原创代码（CodingAgent 实现 SE block） | ✅ 达成 |
| 科学严谨性（识别"不涨点"、质疑误导数字） | ✅ 良好 |
| 收敛/收口（分析后不下结论、反复追加） | ⚠️ 待解决（问题 4） |
| 用户控制权（answer 能被 controller 遵循） | ✅ 已修复（问题 5） |
