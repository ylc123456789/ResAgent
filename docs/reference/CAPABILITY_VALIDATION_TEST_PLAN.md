# 系统能力验证测试方案（可复用模板）

- **版本**: v2
- **日期**: 2026-08-19
- **用途**: 用「有 ground truth 的真实论文」端到端验证多模块科研系统（ResAgent + ExpAgent + ReproAgent + CodingAgent）的核心能力，覆盖「复现」和「原创代码」两个层级。
- **复用方法**: 换 case 时只改对应 case 章节的 case 信息、基线与 goal 文本；框架与评分标准不变。
- **状态**: L1（Mixup 复现）已通过；L2（SE block 原创代码）已通过；L3（新论文复现）待跑。

---

## 1. 测试目的

验证三件事，每件都可打分：

| # | 能力 | 问题 |
|---|---|---|
| 1 | 复现能力 | 系统能不能把论文的关键数字复现到合理误差内？ |
| 2 | 正确结论能力 | 实验做完，系统能不能得出「和论文一致」的科学结论？ |
| 3 | 全链路正确性 | 复现 → 运行 → 分析 这条链能不能端到端走通、不漂移？ |

核心原则：**测试必须有 ground truth（已知答案）**，否则只能判断「跑通了」，判断不了「跑对了」。

---

## 2. case 选择标准（可复用）

一个合格的测试 case 必须同时满足：

1. **顶刊或高引用**：有明确、可验证的科学主张（不是 toy demo）。
2. **官方代码公开**：可克隆、可跑，避免系统「重新实现论文」引入额外变量。
3. **单卡可跑**：4090D（24GB 显存）内，单次训练 ≤ 1 小时，整个测试 GPU 时间 ≤ 数小时。
4. **有可测量的 ground truth**：核心结论能落成数字或明确的方向性判断（对/错）。
5. **主张干净**：能明确判为 supported / not_supported，不是开放式、争论性的问题。

---

## 3. 难度分级（可复用）

| 级别 | 特征 | 重点测什么 |
|---|---|---|
| **L1（简单）** | 主要是复现 + 运行 + 分析，几乎不改代码 | Reproduce / Execute / Analyze |
| **L2（中等）** | 给一个明确的改进方向，让系统实现并验证（写原创代码） | CodingAgent 的 Modify（原创代码） |
| **L3（难）** | 完全开放式的改进任务 / 多步科学决策、失败归因、动态改方向 | 完整科学推理闭环 |

**L1、L2 已通过**（见 §13 结果记录），当前推进 L3（见 §14）。

---

## 4. 本次 case：Mixup（L1）

- **论文**: *mixup: Beyond Empirical Risk Minimization*
- **作者**: Hongyi Zhang, Moustapha Cisse, Yann N. Dauphin, David Lopez-Paz
- **会议**: ICLR 2018
- **arXiv**: 1710.09412（引用 4800+）
- **官方代码**: https://github.com/facebookresearch/mixup-cifar10
- **核心主张**: mixup（对样本与标签做凸组合训练）相比 ERM 能提升泛化、降低过拟合。
- **计算量**: CIFAR-10 + PreActResNet-18，单次训练约 10–20 分钟（4090D），两轮共约 30–40 分钟。

**为什么选它作首发**：官方代码同时实现了 ERM（`--alpha=0`）和 mixup（`--alpha=0.2`），只需切换参数，几乎不涉及代码修改。这样能把「复现 → 运行 → 分析」这条主链路的正确性先验证清楚，把 CodingAgent 的 modify 能力留到 L2 专门测。

---

## 5. 前置准备 + ground truth 基线（一次性，不经过 agent 系统）

目的：先用官方代码在**本机硬件上**手动跑出真实数字，作为 ground truth。**不要直接信论文数字**——硬件、CUDA、随机种子都会影响绝对值，本机实测才是真值。

步骤：

1. 检查环境：GPU（确认是 4090D 且显存可用）、conda、磁盘空间。
2. 克隆官方仓库：`git clone https://github.com/facebookresearch/mixup-cifar10`。
3. 建独立 conda env，按仓库 README 装依赖（torch / torchvision，版本以 README 为准）。
4. 跑 ERM 基线（`--alpha=0`），记录 CIFAR-10 测试精度。
5. 跑 mixup（`--alpha=0.2`），记录测试精度。
6. 把实测数字填入下面的基线表，这就是 ground truth。

**ground truth 基线表（待填）**：

| 配置 | 实测测试精度 | 命令 / 随机种子 / 备注 |
|---|---|---|
| ERM（alpha=0） | ____ | |
| mixup（alpha=0.2） | ____ | |

**论文参考值（仅供对照，不作硬性判据）**：CIFAR-10 上 mixup 约比 ERM 高 1 个百分点左右（方向确定，绝对值随配方浮动）。

---

## 6. 测试设计

### 6.1 goal 原文（喂给系统的输入）

> 复现 ICLR 2018 论文《mixup: Beyond Empirical Risk Minimization》在 CIFAR-10 上的核心结果。使用官方仓库 https://github.com/facebookresearch/mixup-cifar10。先复现 ERM 基线（不带 mixup，alpha=0），再运行 mixup 训练（alpha=0.2），对比两者的测试精度，判断「mixup 相比 ERM 能提升泛化」这一核心主张是否成立。

（这段文本要**原样**给系统，保证它拿到论文、仓库、配置、待判定假设四个要素。）

### 6.2 预期链路

```
ExpAgent 规划：reproduce(ERM 基线) + execute(mixup) + analyze(对比)
ReproAgent 复现 ERM 基线
ReproAgent 运行 mixup
ExpAgent 分析：mixup vs ERM → 结论
```

### 6.3 各环节预期产物

| 环节 | 模块 | 预期产物 |
|---|---|---|
| 复现/运行 | ReproAgent | repro_result（两个测试精度数字 + 日志 + result.md） |
| 分析 | ExpAgent | scientific_decision（conclusion + evidence + result_analysis） |
| 编排 | ResAgent | state.json（任务池、决策记录、观察） |

---

## 7. 评分标准（硬指标，可复用）

三条**全部通过**才算这个 case 通过：

| # | 指标 | 判据 |
|---|---|---|
| 1 | 复现 | 系统报的 ERM 精度落在基线 ± 0.5% 内 |
| 2 | 方向与量级 | 系统报的 mixup 精度 > ERM 精度，且差距量级合理（约 1%，不出现 0.1% 这种噪声级或 10% 这种异常） |
| 3 | 结论 | ExpAgent conclusion = supported，且理由正确（引用了对比数据 + 解释了「为何支持」，而非空泛的「精度涨了所以好」） |

**结果记录表（待填）**：

| # | 指标 | ground truth | 系统实测 | 是否通过（Y/N） |
|---|---|---|---|---|
| 1 | ERM 精度 | | | |
| 2 | mixup 精度 | | | |
| 3 | 结论正确性 | —— | | |

---

## 8. 失败诊断指引（可复用）

症状 → 定位到具体环节，避免盲目改：

| 症状 | 定位 |
|---|---|
| 复现数字与基线差很多 | ReproAgent：环境/配方/数据集/随机种子 |
| 方向反了，或量级明显不对 | 运行环节：参数没切对、训练不充分、数据问题 |
| 结论判错，或理由空泛 | ExpAgent：分析环节（prompt/校验/证据注入） |
| 链路没走通（卡在某步、漏了分析、提前 finish） | ResAgent：编排（路由/依赖/收口/分析覆盖） |
| 环境反复失败、装不上 | M2 内容寻址环境（spec 指纹/缓存/镜像） |

**诊断时必收集的证据**（缺一不可）：state.json、scientific_decision.json、result.md、关键日志、任务 manifest。

---

## 9. 结果记录与归档

1. 每次测试保存：`state.json`、`scientific_decision.json`、`result.md`、日志。
2. 填 §7 结果记录表。
3. 记录一次完整 run 的产物路径（便于回查）。
4. 记录本次硬件的驱动/CUDA 版本、依赖版本（`pip freeze`）。

---

## 10. 升级路径（复用）

- **L1（Mixup 复现）已过** → 当前推进 **L2（SE block 原创代码，见 §12）**。
- **过 L2** → 上 **L3**：完全开放式的改进任务，或需要多步科学决策、失败归因、动态改方向的 case。

每升一级，只替换对应 case 章节的内容，框架不变。

---

## 11. 复现性备注

- 尽量固定随机种子（若仓库支持）。
- 记录硬件型号、驱动、CUDA 版本。
- 记录依赖版本（`pip freeze` 或 conda env export）。
- 同一 case 重复测试时，尽量用同一台机器、同一个 conda env，减少环境噪声。

---

## 12. L2 案例：SE block 原创代码（给方向 + 验证）

**定位**：介于 L1「纯复现」和「完全开放创新」之间——给一个明确的改进方向，让系统实现并验证，重点测 CodingAgent 的**原创代码能力**（不是照着论文推导，而是自己把方向落地成代码）。

### 12.1 背景与基线

- **基线**：mixup-cifar10 仓库的 ResNet18 + CIFAR-10（已跑通，测试精度约 94.6%）。
- **方向**：给 ResNet18 加 Squeeze-and-Excitation（SE）通道注意力模块。
- **为什么选 SE**：简单（约 20 行：global avg pool → FC → sigmoid → 通道加权）；已知能让 ResNet 在 CIFAR-10 涨约 1–2%（**半有 ground truth**，能判断实现对不对）；实现它是实打实的原创代码活（forward 接线、维度对齐、残差块改造）。

### 12.2 goal 原文（喂给系统）

> 在 ResNet18 + CIFAR-10 基线上（当前测试精度约 94.6%），尝试给网络加入 Squeeze-and-Excitation（SE）通道注意力模块，验证它能否提升测试精度。请：①在 models/resnet.py 里实现 SE block 并接入 ResNet 的残差块；②用同一训练协议（同 seed、epoch、lr）跑基线和 SE 改进版；③对比测试精度，诚实报告 SE 是否真的涨点、涨了多少。

### 12.3 预期链路

```
ExpAgent 理解方向 → 规划 modify_code（改 resnet.py 加 SE block）
CodingAgent 写 SE block 原创代码（forward 接线、维度对齐）
ReproAgent 跑基线 vs SE 版（execute_experiment）
ExpAgent 分析：SE 涨没涨、涨多少、是否显著
```

### 12.4 评分标准

| 档 | 表现 |
|---|---|
| **好** | SE 正确实现、能跑、测试精度涨约 1–2%，系统正确报告 |
| **中** | SE 实现但涨得少/没涨，系统**诚实报告**并归因 |
| **差** | 实现不了 / 报错 / 瞎报涨了 |

核心判据：**涨点量级约 1–2% + 诚实报告**。（诊断/归档/复现性注意事项复用 §8/§9/§11。）

---

## 13. 测试结果记录

### L1：Mixup 复现 —— ✅ 通过（2026-08-18）

| 指标 | 结果 |
|---|---|
| ERM（alpha=0）测试误差 | best 5.34% / final 5.50%（seed=20170922） |
| mixup（alpha=0.2）测试误差 | best 4.55% / final 4.60% |
| 结论 | mixup 相对 ERM 降低 0.79–0.90 pp，conclusion = supported（支持论文主张） |
| 全链路 | ExpAgent 规划 → ReproAgent 复现/运行 → CodingAgent 修兼容 → ExpAgent 分析，全部 completed |

**过程中发现并修复的问题**：
1. CIFAR-10 从 cs.toronto.edu 下载极慢（~50KB/s）→ 本地下载 + scp 预置解决。
2. ReproAgent 换 seed 反复重跑（seed-sweeping）→ 详见 `CAPABILITY_TEST_FINDINGS_MIXUP.md`；修复方案「去掉硬护栏 + 保留 metric-agnostic prompt 收敛规则」重测通过（ERM、mixup 各只跑一轮 seed=20170922 即收敛）。

### L2：SE block 原创代码 —— ✅ 通过（2026-08-19，详见 `CAPABILITY_TEST_FINDINGS_L2_SEBLOCK.md`）

---

## 14. L3 案例：新论文「从论文出发」复现（search_literature 能力验证）

**定位**：L1/L2 的 goal 都是手写的方法细节（"复现 mixup"、"加 SE block"），`search_literature` 从未真正跑过。L3 的 goal **只给 arXiv ID 和待判定主张**，方法、仓库、配方、结论全部让系统自己搜出来，重点验证「论文 → 检索 → 提取方法 → 复现 → 分析」这条此前没测过的链路。

### 14.1 背景与选型

**硬约束**："最新" 与 "单卡可跑" 冲突。实测搜索发现，2026-08 的新论文几乎全是扩散 / 大模型 / 医学影像等重计算工作（3B/6B 模型、440M 图像语料、1.3B LLM），4090D 单卡几小时内无法验证。因此「最新」落到实操上是「2024–2025 里最新、且单卡可复现的方法类论文」。这个区间最优的是**优化器论文**——方法自包含、CIFAR 单卡几十分钟、主张是清晰数字对比。

**主选：Schedule-Free Learning（无学习率调度优化器）**

- 论文：*The Road Less Scheduled*，Defazio et al.
- 会议：NeurIPS 2024；arXiv 2405.15682
- 官方代码：https://github.com/facebookresearch/schedule_free
- 核心主张：不用 LR schedule、无需事先知道训练步数 T、不加额外超参，就能追平/超过精心调过的 cosine 调度。
- 为什么选它：①是 2025–2026「schedule-free」热门线的锚点（大量 follow-up），检索会找到真实演进文献而非背熟的旧论文；②CIFAR-10/100 有明确 ResNet 配方的数字，ground truth 干净；③官方代码成熟（MLCommons AlgoPerf 2024 自调优赛道冠军）；④实现约 40 行，顺带复用 L2 的 modify_code。

**备选：AdEMAMix（ICLR 2025，更新）**

- 论文：*The AdEMAMix Optimizer: Better, Faster, Older*，Pagliardini et al.，arXiv 2409.03137。
- 核心主张：把单 EMA 换成两个 EMA 的混合（快 β1 + 慢 β3），让老梯度保持相关性，收敛更快更稳。
- 取舍：比 Schedule-Free 更新，但头条结果是 1.3B LLM 级，图像分类 ground truth 相对偏薄。想"最最新"换它，想"ground truth 最干净"用 Schedule-Free。

### 14.2 goal 原文（喂给系统）

> 找到并复现 arXiv:2405.15682（*The Road Less Scheduled*）在 CIFAR-10 上的核心结果。请自行检索这篇论文，提取它的方法（Schedule-Free 优化器）与官方仓库，在 CIFAR-10 上跑一个标准 ResNet 基线，对比「带 cosine 学习率调度的普通 SGD/AdamW」与「Schedule-Free（无调度）」的测试精度，判断论文核心主张「去掉学习率调度、无需训练步数 T，仍能追平/超过精心调度的基线」是否成立。

（关键点：goal **只给 arXiv ID 和主张**，不给仓库 URL、不给配方、不给超参——这些都要系统自己搜出来，这才测得到 search_literature。）

### 14.3 预期链路

```
ExpAgent search_literature：检索 arXiv:2405.15682 → 拿到标题/摘要/方法/官方仓库
ExpAgent 提取方法 → 规划 reproduce（克隆 schedule_free 跑 CIFAR）
ReproAgent 复现「cosine 调度基线」+「Schedule-Free」两轮，同协议
ExpAgent analyze：无调度 vs 有调度 → 是否追平/超过
```

（若官方仓库对 CIFAR 配方不友好，允许系统用 modify_code 从论文自己实现优化器接入现有 mixup-cifar10 基线——恰好复用 L2 的原创代码能力。）

### 14.4 ground truth 基线（人工预跑，不进系统）

复用 §5 流程：先用官方代码在本机跑出真值。

| 配置 | 实测测试精度 | 备注 |
|---|---|---|
| SGD/AdamW + cosine 调度（基线） | ____ | 同 seed、同 epoch |
| Schedule-Free（无调度） | ____ | 同 seed、同 epoch |

论文参考值：CIFAR-10 上 Schedule-Free ≈ 或略高于 cosine 基线（方向：持平/略高，不显著掉）。

### 14.5 评分标准

三条**全部通过**才算过：

| # | 指标 | 判据 |
|---|---|---|
| 1 | 检索 | 系统正确找到 arXiv:2405.15682 的标题/方法/官方仓库（schedule_free），没找错论文 |
| 2 | 复现 | Schedule-Free 实测精度落在基线 ± 0.5% 内，且 ≥ cosine 基线 − 0.5%（允许持平，不允许显著掉） |
| 3 | 结论 | ExpAgent conclusion = supported，理由引用对比数字 + 正确解释「无调度也能追平」 |

诊断/归档/复现性复用 §8/§9/§11。
