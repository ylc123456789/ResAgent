# M2-P2 修复交办：采集语义对齐 + provenance + lease 路径

**日期**：2026-08-16
**仓库/分支**：CodingAgent `codex/environment-resource-v1`（当前顶端 `a099afa`）
**依据**：`contracts/README.md` "采集语义"一节（ResAgent @ `57cc557`）。
本单三条都是"与冻结契约对齐"，不允许另行解释。

## 1. spec 采集对齐（阻塞项——跨模块指纹一致性）

现状三处偏离契约：

- `_detect_accelerator` 读**当前进程的 torch** 推断 CUDA——宿主机状态决定
  身份，禁止。改为接受调用方传入（ResAgent 将按任务的 requires_gpu 传入
  accelerator type/variant；独立调用时按契约规则自判）；
- `pip_index_profile` 硬编码 `""`——改为从调用方/配置取镜像策略名；
- `_dependency_files` 的哈希是 `read_bytes().decode(errors="ignore")` 后再
  哈希——**改为原始字节** `sha256(path.read_bytes())`；文件集合以契约
  清单为准（含 `setup.cfg`、`*.lock` 等你当前缺失的项）。

验收：reproagent 与 CodingAgent 对同一 fixture repo 必须算出**逐字节相同**
的 spec_fingerprint（总体会话提供跨仓对拍脚本复核）。

## 2. manifest provenance 必填（阻塞项——跨 run 可见性）

现状 CodingAgent 建 env 时 provenance 留空，ResAgent 跨 run 发现按
`provenance.repo_path` 匹配，导致你们建的 env 永远不可见。创建时必须写：

- `repo_path` = 任务工作区绝对路径；
- `repo_commit` = git HEAD（是 git 仓库时）；
- `repo_origin` = repo_url 或 `local`。

## 3. lease 路径（次要项）

`resources.py` 的 lease 扫描从 `<root>/leases/*.json` 改为
`<root>/environments/<env_id>/usage/lease_*.json`（契约已冻结此布局，
ResAgent/reproagent 都写这里）。

## 必增测试

- 对拍 fixture：与 canonical golden 指纹一致（保留）+ 与 reproagent 同
  repo 同指纹（新增）；
- 新建 manifest 的 provenance 三字段齐全；
- 活跃 lease（usage/ 路径）下的 env 不进 prune 候选；
- 既有 134 测试不回退。

## 验收

推送后总体会话做跨仓指纹对拍 + 云端升级链验收。
