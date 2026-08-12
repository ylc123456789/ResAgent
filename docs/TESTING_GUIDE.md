# ResAgent 测试使用说明

本文说明如何验证 ResAgent 与 ExpAgent、CodingAgent、ReproAgent 的集成。
测试分层运行，不需要每次都进行完整论文训练。

## 1. 本地快速回归

用途：每次修改代码后运行。无需网络、LLM、GPU或子模块真实环境。

```bash
cd /home/cyl/ResAgent
conda activate ResAgent
pytest -q tests
```

验收：所有测试通过，命令返回码为 0。

## 2. 确定性四模块闭环验收

用途：验证任务路由、三个 adapter、ask_user、session parent、finish gate
和终态保护。使用子模块 mock API，但执行完整的 ResAgent 生命周期。

临时运行，不保留产物：

```bash
cd /home/cyl/ResAgent
conda activate ResAgent
python scripts/deterministic_system_test.py
```

保留产物并写 JSON 报告：

```bash
python scripts/deterministic_system_test.py \
  --workspace /tmp/resagent-system-test \
  --report /tmp/resagent-system-test/report.json
```

成功时输出：

```json
{
  "status": "passed",
  "run_id": "deterministic-four-module"
}
```

## 3. 真实云端验收

用途：验证真实 DeepSeek、真实三个子模块、网络、Conda、GPU和公开仓库。
它不是日常单元测试，不应由 pytest 自动收集。

准备：

```bash
cd /root/autodl-tmp/projects/ResAgent
conda activate ResAgent
git pull
pip install -e .
pytest -q tests
export DEEPSEEK_API_KEY='你的密钥'
```

运行全部云端 case：

```bash
python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace \
  --case all
```

若服务器有自定义配置文件，可增加
`--config /root/autodl-tmp/projects/ResAgent/config.yaml`。省略时使用默认配置和环境变量，
不会假定仓库中存在 `config.yaml`。

单独运行某一类：

```bash
python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace --case coding

python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace --case repro

python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace --case dependency-chain

python scripts/cloud_acceptance.py \
  --workspace /root/autodl-tmp/resagent-workspace --case env-reuse
```

云端训练应限制为 1 至 5 epoch。160 epoch 论文完整复现属于科学基准测试，
不属于系统集成验收。

## 4. 必须满足的云端断言

- 任一 observation 为 `error` 或 `rejected` 时，case 失败；
- 最终状态必须符合 case 预期，不允许 error 后伪 completed；
- 每个 required task 必须 completed、skipped 或被明确 superseded；
- CodingAgent/ReproAgent session 必须有正确 parent；
- state 中登记的 artifact 文件必须存在；
- 环境复用 case 必须完成两个 ReproTask；
- 两个 ReproTask 的 session 必须报告同一个环境 identity；
- `dependency-chain` 必须先完成 CodingAgent，再运行 ReproAgent；
- ReproAgent 的隔离快照必须包含 CodingAgent 尚未提交的修改；
- 依赖未完成、引用不存在或依赖成环时不得调度后续任务；
- GPU 可用且任务要求 GPU 时，结果中必须有 GPU 使用证据；
- bounded 测试必须在报告中说明它不是论文完整复现。

## 5. 失败排查顺序

1. 查看 JSON 测试报告中的失败 assertion。
2. 查看对应 run 的 `state.json`，定位 task、attempt 和 observation。
3. 查看 task 下的 `resagent_adapter_result.json`。
4. 最后查看子模块自己的 session card、result 和 logs。

不要仅凭终端打印的 PASS 判断系统闭环是否正确。

## 6. 测试脚本边界

- `pytest tests`：确定性代码回归；
- `deterministic_system_test.py`：确定性系统闭环；
- `cloud_acceptance.py`：真实云端集成；
- 论文完整训练：单独的科研复现任务。

这些层级相互补充，不能用一次真实 LLM 测试替代状态机单元测试。
