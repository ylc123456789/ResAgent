# M2-P1 修复交办 #3：accelerator 采集 + 锁恢复接线

**日期**：2026-08-16
**仓库/分支**：reproagent `feat/content-addressed-envs`（当前顶端 `4fd0f8c`）
**依据**：`contracts/README.md` 新增"采集语义"一节（ResAgent `codex/resource-cleanup-m2-p4` @ `57cc557`），本单是规范性对齐，不是自由发挥。

## 1. accelerator 采集重写（阻塞项）

现状 `_detect_accelerator`（env_identity.py）两个错误：
① `nvidia-smi --query-gpu=...,cuda_version` 在服务器上被拒绝 → 静默降级 cpu；
② 把驱动支持的最高 CUDA 版本映射成 wheel 变体（13.0 → cu130，不存在的 wheel）。

按契约重写：

- `type`：`task.requires_gpu` 为真且本机有可用 GPU → `cuda`，否则 `cpu`；
- `variant`：任务/框架约束显式给出才填（如 `torch==2.6.*+cu124`），否则 `""`；
  创建后把真实值记入 `manifest.resolved.frameworks`；
- 驱动探测只用于可行性（"这机器能不能跑 cu124"），**不进身份**；
- nvidia-smi 解析改健壮写法：直接读 `nvidia-smi` 输出的 `CUDA Version: X.Y`
  头部（无 --query-gpu 字段依赖），失败仅影响可行性提示，不得再静默把
  type 降为 cpu 而不留痕迹（写一条 warning 到 setup 日志）。

## 2. 创建锁恢复接入主线（加固项）

- `recover_stale_lock` 已实现但未接入：`_create_content_addressed` /
  `_wait_then_retry` 在锁持有者是**本机死进程**时应调用它接管/重建，
  而不是等 120s 超时；
- `_pid_alive` 增加 host 判定：锁/lease 记录的 host 与本机不同 → 保守视为
  存活（共享 resource root 场景），本机才做 `kill(pid, 0)`。

## 必增测试

- 无 nvidia-smi / nvidia-smi 无 cuda_version 字段 → 不崩溃、type 判定走
  requires_gpu 规则、日志留 warning；
- requires_gpu=True + 有 GPU → cuda；False → cpu；driver 13.0 不得出现 cu130；
- 死进程持锁 → recover_stale_lock 被调用且创建继续（不是超时）；
- 远端 host 的锁 → 视为活跃，不接管；
- 既有 212 测试不回退。

## 验收

推送后总体会话重跑云端 m2-env-reuse + 升级链新段 + legacy。
