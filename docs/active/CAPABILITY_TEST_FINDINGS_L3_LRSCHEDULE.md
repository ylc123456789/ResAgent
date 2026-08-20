# L3 测试问题清单（开放方向：学习率调度）

- **日期**: 2026-08-20
- **测试**: L3 能力验证（开放方向 T1：只给方向、不给论文，见 `CAPABILITY_VALIDATION_TEST_PLAN.md` §14）
- **状态**: ⏸️ 中途停止（见下）。核心能力「search → 设计 → 执行」已正面验证；问题 1 已修复（档 1），问题 2 最小修复（冗余字段清理待办）。

---

## 结论速览

| # | 问题 | 模块 | 状态 |
|---|---|---|---|
| 1 | user directive 注入 context 后，Planner 无动作可据以行动（缺 re-plan lever） | ResAgent | ✅ 已修复（档 1，`c9272e3`） |
| 2 | dataset_cache/mirror 移植的两个缺口（无测试 + 镜像推导缺失） | CodingAgent | ⚠️ 最小修复（`cf8ace2`，`pip_index_profile` 冗余字段清理待办） |

---

## 正面收获（已达成）

1. **search_literature 首次真跑**：goal 只给"学习率调度"方向，不给论文/方法/配方，系统独立检索并设计了实验。
2. **设计质量高**：选了 "linear decay" 当"比 cosine 更简单"的候选（文献里真实、正确的答案）；3 调度 × 3 seed 控制变量；预注册 0.5% 等价边界；主动加 seed 方差（吸收了 L2 的教训）。
3. **全链路**：ExpAgent 规划（task_001）→ CodingAgent 写统一 harness（task_002 completed）→ ReproAgent 准备环境，全部走通。

---

## 问题 1（已修复）：directive 注入 context 但 Planner 无法据此行动

### 现象

中途给 run 注入"单 seed"指令后，指令成功进了 `state.user_directives` 并被注入 controller context（`build_controller_context` 标了 always kept），但 Planner 照旧派发了 3-seed 的 task_003，没有改计划。

### 根因

不是 prompt 没写（`CONTROLLER_SYSTEM` 已写 "User Directives take priority"），也不是指令没到（builder 每次都注入）。**卡在 `contracts.py` 的 `allowed_action_candidates`**：Planner 的动作闸门只放行 pending/failed/blocked 任务的派发动作 + `ask_user` + `finish`。没有"修改任务"、也没有"自由 re-plan"（代码注释明说 "There is no free-floating 're-consult' hint"）。所以"改成单 seed"这种要改 `task_003.input` 的指令，Planner 手里没有能落地的动作，只能照旧派发原任务。

**这不止影响"运行中注入"——连设计内的 `pause → answer → resume` 也受影响**：d6ecd70（L2 问题 5）只修了"answer 进 user_directives"，没修"Planner 能据此行动"这最后一公里。

### 修复（档 1，已实施）

在 pause/resume 边界让 Planner 能重规划：

1. `models.py`: `UserDirective` 加 `handled: bool = False`（区分哪些指令没处理过）。
2. `contracts.py`: 加 `ensure_directive_replan`——套用现成的 `ensure_analysis_coverage` 模板：检测到未处理指令 → 自动生成一个高优先级的 ExpAgent 重规划任务（`replan_from_directive`），走现有 `call_exp_agent` 路径。
3. `loop.py`: 在 `step` 里调 `ensure_directive_replan`。
4. `prompts.py`: 加一条"有 re-plan 任务时优先派发它"的规则。

> commit: ResAgent `c9272e3`。附回归测试 `test_unhandled_directive_creates_replan_task`、`test_step_surfaces_directive_as_replan_task`。

---

## 问题 2（最小修复）：dataset_cache / mirror 移植的两个缺口

把 ReproAgent 的 dataset_cache + mirror 机制移植到 CodingAgent（ResAgent `a0cd0dd` 线程 + CodingAgent `0b9702c` 实现）时暴露两个缺口，均已由 CodingAgent `cf8ace2` 修复：

1. **`dataset_cache.py` 无测试**：231 行新模块（正则扫描 + 符号链接 + 安全边界），移植时没带测试。修复：补 15 个测试（正则误伤、链接粒度、安全边界、cache 命中、prompt 渲染）。
2. **`mirror_profile` 与 `pip_index_profile` 缺推导**：两个字段分属不同层——`mirror_profile` 是任务层输入（config `repro_mirror_profile` → prompt `_mirror_block`），`pip_index_profile` 是环境层记录（写进 M2 manifest）。ReproAgent 的规矩是 `pip_index_profile` 从 `mirror_profile` 推导（`env_identity.py:156`）；移植时漏了这条推导，导致只传 `mirror_profile` 时 manifest 里 `pip_index_profile` 为空、跨模块记录不一致。修复：`_prepare_environment` 里 `pip_index_profile` 缺省时从 `mirror_profile` 推导（`""`/`none` 视为无镜像）。

> 注 1：`pip_index_profile` 是冻结 M2 契约的一部分（f1bf535 已把它排除出身份指纹），纯记录一致性，不影响环境复用判断。

> 注 2（最小修复，完整清理待办）：当前只做了"一行推导"（`pip_index_profile` 缺省从 `mirror_profile` 推导），能跑、manifest 记录一致。但 `pip_index_profile` 作为 `CodeTaskSpec` 的 task 字段是历史遗留（ReproAgent 的 `ReproTask` 里没有该字段）；更干净的做法是删掉它、只留 `mirror_profile`、内部推导。这是动 M2 契约的大改，暂缓，以后单独处理。
