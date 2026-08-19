# Mixup 能力测试问题清单

- **日期**: 2026-08-18
- **测试**: L1 能力验证（Mixup 复现，方案见 `CAPABILITY_VALIDATION_TEST_PLAN.md`）
- **状态**: 测试未完整跑完（停于 task_002），核心链路验证通过，暴露 2 个问题
- **涉及模块**: reproagent（主）、ExpAgent / ResAgent（输入侧）、基础设施（数据集源）
- **证据现场**（服务器）:
  - run 日志: `/root/autodl-tmp/capability-test/run.log`（含 reproagent 每步 `step N:` 决策）
  - reproagent 状态: `/root/autodl-tmp/resagent-workspace/runs/mixup-l1-20260818/tasks/reproagent/task_002/attempt_002/repo_workspace/state.json`
  - ResAgent 状态: `/root/autodl-tmp/resagent-workspace/runs/mixup-l1-20260818/state.json`

---

## 结论速览

| # | 问题 | 严重度 | 主要模块 |
|---|---|---|---|
| 1 | ReproAgent 复现不收敛，反复换 seed 重跑 | 高 | reproagent + 上游输入 |
| 2 | CIFAR-10 从 cs.toronto.edu 下载极慢 | 中 | reproagent（dataset cache / 下载策略） |

---

## 问题 1：ReproAgent 复现不收敛，反复重跑

### 现象

复现 ERM 基线（mixup 论文，CIFAR-10）时，reproagent 连续跑了 **5 轮训练**：

1. `smoke_alpha0_0` —— smoke 测试（1 epoch）
2. `erm_alpha0_0` —— 100 epoch，underfit，test error 11.06%（因训练脚本需 200 epoch + 在 100/150 降 LR 才收敛）
3. `erm_alpha0_200` —— 200 epoch seed=0，best test error **5.64%**
4. `erm_alpha0_seed1` —— 200 epoch seed=1，best test error **5.5%**
5. `erm_seed20170922` —— 200 epoch seed=20170922（README 官方示例 seed），**测试停在这一轮**

它始终没有推进到 mixup（task_003）。

### 证据（reproagent 的 step 决策原文摘录）

```
step 15: "200-epoch ERM 完成，best test error 5.64% vs target 4.41%。
         Because seed=0 skips torch.manual_seed ..."
step 16: "seed=1 200-epoch 跑完，解析 CSV 计算 final/best test error 对比..."
step 17: "seeds 0/1 best test error 5.64% 和 5.5%，论文官方 README 示例用 seed=20170922..."
```

核心：它给自己定了一个目标值「误差 4.41%」（论文数字），实际只到 5.5%–5.64%，于是不断换 seed 去追。但差距根本不来自 seed。

### 根因分析

**根因 A（输入侧）：reproagent 的输入没钉死 recipe。**

`ReproTask.experiment_goal` 只是一句自然语言（"复现 ERM 基线，alpha=0"），**没有**：
- 模型架构（论文 headline 是 **PreActResNet18**，而仓库默认/它实际跑的是 **ResNet18**，精度天然低约 1 个点）
- 目标指标 + 容差（"误差 4.41% ± X"）
- 论文超参

于是 reproagent 自己读论文推断出目标 4.41%，却用了 ResNet18，差距永远补不上。涉及链路：用户 goal → ExpAgent 动作图 → `ReproTask.experiment_goal`（全程没 pin 模型）。

**根因 B（reproagent prompt 侧）：缺「止损 / 诊断」指引。**

`reproagent/src/reproagent/controller/prompts.py` 的 SYSTEM_PROMPT 只教了「跑实验 → 提取指标 → 写报告」，**没有**：
- 「复现误差落进合理容差就 finish」的止损规则；
- 「追不上目标时，先诊断偏差来源（模型选型 / 配方 / 数据），而不是换 seed 重跑」。

所以 LLM 的默认行为是「再加把劲」（换 seed 重跑），而不是「停下来诊断」。这正是"不聪明"的来源。

### 建议修复

**给 reproagent 侧（prompt 层，改动最小、收益最大）：**

在 SYSTEM_PROMPT 的 Experiment / Reporting 规则里补三条：

1. **止损规则**：复现结果落进合理容差（例如与论文 target 误差在 ±1 个百分点内）就应 finish，并在 finish_summary 里明确标注「复现成功，与论文偏差在合理范围内（说明偏差来源）」。
2. **诊断规则**：连续 2 次复现结果无明显改善时，先诊断偏差根因（模型架构 / 超参 / 数据 / 训练配方），把结论写进 Deviations 段，而不是再开一轮同配置实验。
3. **明确常识**：换 random seed 不改变「模型选型 / 配方」造成的系统性偏差；若差距稳定在 ~1 个点，应优先怀疑模型架构而非 seed。

**给输入侧（ExpAgent / ResAgent，可选增强，非阻塞）：**

把 recipe 硬信息结构化传下去——模型架构、目标指标、容差范围，而不是只给一句自然语言 goal。（长期看这比 prompt 补丁更根本，但改动面更大。）

---

## 问题 2：CIFAR-10 下载慢（基础设施）

### 现象

torchvision 从 `https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz` 下载，国内节点实测 ~50KB/s（170MB 需约 1 小时）。且 reproagent 的重试用 `curl --retry 3 --max-time 900`（15 分钟），小于下载所需时间，导致反复超时重来（每次归零重启，无断点续传）。

### 根因

1. 数据源 cs.toronto.edu 对国内访问极慢。
2. reproagent 走 torchvision 默认下载 URL，未用快镜像。
3. 大文件下载超时（900s）过短，且无 `-C -` 断点续传。

### 建议修复

1. **dataset cache 预置**：允许预先将数据集放入 `dataset_cache_dir`，reproagent 检测到已存在就跳过下载（本次已手动验证可行——预置 `cifar-10-batches-py/` 后 reproagent 直接跳过下载）。
2. **下载策略**：大文件下载用 `curl -C -`（断点续传）+ 更长超时；对已知慢源提供镜像替换（如国内镜像）。

---

## 附：本次验证通过的能力（说明系统底盘可用）

- **ExpAgent**：动作图规划正确（reproduce ERM + execute mixup + analyze）。
- **ReproAgent**：clone 仓库、装 torch 2.6.0、M2 环境审计、数据集处理。
- **CodingAgent**：精准修复 2018 老代码的 torch 2.6 兼容（`loss.data[0]` → `loss.item()`、`stty size`、`term_width`）。
- **编排闭环**：复现受阻 → 自动派 CodingAgent 修 → 恢复执行，链路完整走通。
- **ERM 结果**：94.31% / 94.36%（best）测试精度，与论文 ~95.3% 量级吻合（差 ~1% 即上述模型选型所致）。

> 结论：系统的编排、复现、改代码、环境管理这条核心链路是通的；当前最需要补的是 **reproagent 的"复现收敛 / 止损 / 根因诊断"能力**（问题 1），其次才是数据集基础设施（问题 2）。
