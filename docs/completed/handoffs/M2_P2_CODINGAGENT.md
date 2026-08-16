# M2-P2 交办单：CodingAgent 环境政策对齐 V1 契约

**日期**：2026-08-16
**权威方案**：[`../RESOURCE_MANAGEMENT_MILESTONE_2.md`](../RESOURCE_MANAGEMENT_MILESTONE_2.md)（§4–§8、§9.3、§11、§12）
**分支**：`codex/environment-resource-v1`（CodingAgent 仓）
**前置依赖**：M2-P0 契约冻结（由总体会话交付并评审通过后开工）

## 交付物（对应方案 §9.3）

1. `auto` 模式按 V1 spec/manifest 创建或复用 **verification 级**环境；
2. `reuse_only` 只绑定符合契约的现有 env：复用前校验 manifest + 重算 resolved fingerprint，禁止重型框架就地漂移；
3. `frozen` 不修改 env；漂移或缺依赖时返回**结构化 blocked**（不伪造可用）；
4. 写 certification 时只写 `verification`，**不得越权写 `experiment`**（实验级认证权专属 reproagent）；
5. 独立调用与 ResAgent/reproagent 绑定模式共用同一契约（同一套 spec/fingerprint/manifest 读写）；
6. 暴露自己管理的 env 的 inspect/prune 入口（默认 dry-run）；
7. 保持通用编程 agent 定位：不加科研特化 prompt，不加科研特化逻辑。

## 边界（不得触碰）

- 不改 agentic loop、clone、verify 命令执行、会话绑定的现有语义；
- 不实现 cleanup apply 路径；
- 不修改其他三个仓库；
- 指纹/manifest/锁的判定全部为确定性代码，LLM 不参与。

## 验收门禁

- CodingAgent 现有测试全绿（当前基线 86）；
- 与 P0 golden fixtures 指纹对拍一致；
- `auto` 独立创建的环境可被登记为 verification；同 spec 第二次零创建；
- `frozen` 遇漂移/缺依赖返回结构化 blocked（含原因与所需动作）；
- legacy 模式行为不变。

## 工作纪律

- 小步提交，测试随改随绿；完成后报告：分支、commit、测试数、新增文件清单、fixture 对拍结果；
- 契约歧义带回总体会话，不自行解释。
