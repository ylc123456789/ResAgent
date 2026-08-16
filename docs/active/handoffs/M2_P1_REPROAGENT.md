# M2-P1 交办单：reproagent 环境管理器

**日期**：2026-08-16
**权威方案**：[`../RESOURCE_MANAGEMENT_MILESTONE_2.md`](../RESOURCE_MANAGEMENT_MILESTONE_2.md)（§4–§8 契约与流程、§9.2 任务、§11 验收矩阵、§12 迁移与回滚）
**分支**：`feat/content-addressed-envs`（reproagent 仓）
**前置依赖**：M2-P0 契约冻结（ResAgent 仓 `codex/resource-management-m2`，由总体会话交付并评审通过后开工）

## 交付物（对应方案 §9.2）

1. spec 收集与确定性 fingerprint（`spec_fingerprint` / `resolved_fingerprint` 两级；规范化规则见 §4.2）；
2. resource root 布局、manifest 状态机（creating/ready/drifted）、原子写、按指纹的创建锁（host/pid/心跳，获锁后二次检查）；
3. `ensure_environment` 变为"精确复用或创建"，`reuse_mode = legacy | content_addressed` 开关，**默认 legacy**；
4. 复用前重审计，写 resolved fingerprint，漂移按 §6.4 分档处理；
5. 维持实验级认证权：audit artifact 可追溯，verification → experiment 升级走 §6.3；
6. `session.yaml` bindings 增写 `manifest_path`、fingerprints、`certification`、prefix（新字段可选，旧卡可读）；
7. 可测的 inspect/prune 维护入口，**默认 dry-run**；
8. 独立 CLI 保持可用，不要求必须经 ResAgent 调用。

## 边界（不得触碰）

- 不改 V2 capability 词表、实验 loop 主流程、session 卡既有字段语义；
- 不实现清理的 apply 路径（plan/dry-run 即可，apply 属 M2-P4 由 ResAgent 侧统筹）；
- 不修改 CodingAgent/ExpAgent/ResAgent 仓库；
- LLM 不得参与 fingerprint、manifest 状态、锁、清理候选的判定（方案 §6.1）。

## 验收门禁（方案 §11 与本交办单）

- reproagent 现有测试全绿（当前基线 174）；
- 与 P0 golden fixtures 对同一输入产出**一致指纹**（跨仓一致性是 P5 硬指标）；
- 同 spec 第二次零创建；两进程同 spec 并发只创建一次；
- 手动 pip install/uninstall 制造漂移 → resolved fingerprint 不一致 → 拒绝盲复用；
- legacy 模式下行为与现状完全一致（回归测试证明）。

## 工作纪律

- 小步提交，测试随改随绿；完成后报告：分支、commit、测试数、新增文件清单、与 fixture 的对拍结果；
- 遇到契约歧义不要自行解释——带回总体会话澄清（契约问题在 P0 修，不在实现里绕）。
