# M2-P1 修复交办 #2：resolved 指纹必须覆盖 spec 包清单

**日期**：2026-08-16
**仓库/分支**：reproagent `feat/content-addressed-envs`（当前顶端 `c732348`）
**严重度**：阻塞 M2-P5（stage 4 漂移检测无效）
**证据**：`/root/autodl-tmp/resagent-workspace/m2-p5-20260816-r4/`
（报告 + `m2-resources/environments/resenv_m2proj_5fc39b00bedc/` 的 manifest 与 audits/）

## 现象与已排除项

漂移注入成功（env B 的 six 已卸载），run4 也走了复用路径（audit 有记录），
但 `actual == expected`，未标 drifted。run4 重算值是正确的"仅 numpy"哈希
——**所以错的是 manifest 里存的 expected**：它也是"仅 numpy"哈希，
尽管 env B 在 stage 3 时确实装有 six。

## 代码层已确认的事实（总体会话静态走查）

1. `_create_content_addressed`（environment.py）在 `conda create -p prefix python=X`
   之后**立刻** `collect_resolved_inventory` + `mark_ready`——此时
   requirements 里的包（numpy/six）**尚未安装**（它们由实验 loop 后续安装）。
   即创建时记录的库存不含 spec 包。
2. `finalize_manifest_after_audit`（env_manager.py）设计上会在审计后重算并
   更新 resolved_fingerprint——若它正常工作，stage 3 结束时应写入含 six 的
   库存哈希。证据表明写入的值仍不含 six。
3. run4 的 audit 只跑了 `policy` + `framework_import`（torch 探测）——
   即便指纹对了，审计也没有任何"spec 声明的包是否真实存在"的检查。

## 需要先回答的判别问题（用 r4 现场的 manifest/audit 即可回答）

- env B manifest 里的 `resolved_fingerprint` 究竟等于什么？与
  `conda run -p <B的prefix> python -m pip list --format=json` 现场重算对比；
- stage 3 的 finalize 是否执行、其 inventory 是否包含 six？
  （若包含，则指纹应≠仅 numpy 哈希；若不包含，定位 probe 为何漏掉 six——
  注意 `conda run -p <prefix> bash -c "python -m pip list"` 的解析对象是否正确。）

## 修复要求

1. **权威 resolved 指纹必须代表"装完 spec 包之后"的库存**：创建时可以先记
   bare-python 状态，但 ready 状态的指纹必须在依赖安装完成后刷新
   （finalize 是自然的刷新点）；
2. 审计增加 spec 合规检查：requirements 声明的分发包（名称+版本约束）
   用 importlib.metadata / pip list 逐一核验存在性，缺失即 fail——
   这是漂移检测的第二道防线；
3. 复用路径的比较逻辑（`actual != expected` → mark_drifted + 结构化拒绝）
   本身已正确，不要改坏。

## 必须新增的测试

- 创建的 env 装 numpy+six 后，manifest 的 resolved_fingerprint 必须因
  six 的存在而与"仅 numpy"库存不同；
- 卸载 six 后再走复用校验 → 必须触发 drift（mark_drifted + 拒绝）；
- spec 合规审计：缺包即 fail；
- 既有 206 测试不回退。

## 验收

修复推送后总体会话重跑云端 `m2-env-reuse` 四段 + `--case all`。
