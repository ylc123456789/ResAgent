# CodingAgent 问题：dataset_cache / mirror 移植的两个缺口

- **日期**: 2026-08-20
- **来源**: 把 ReproAgent 的 dataset_cache + mirror 机制移植到 CodingAgent(commit `0b9702c`;ResAgent 线程 `a0cd0dd`)
- **归属**: CodingAgent(本文件供 CodingAgent AI 按最小修法修复)
- **状态**: 待修

---

## 问题 1：`dataset_cache.py` 没有测试

`src/coding_agent/runtime/dataset_cache.py` 是 231 行的新模块(从 ReproAgent 移植),但移植时没有一起搬测试。这个模块是"正则扫描 + 符号链接 + 安全边界"的组合,很容易被后续改动破坏而无人发现。

需要覆盖的行为(已有 15 个测试、全过,说明实现对;缺的是"以后改坏了会报错"的保护网):

1. **正则误伤**:`data_dir = 'data'` 出现在注释或字符串里怎么办;
2. **链接粒度**:什么情况链到 cache 根、什么情况链到数据集子目录;
3. **安全边界**:shared 模式下会不会在 workspace 外建链接。

**修法**:把 ReproAgent 侧 `runtime/dataset_cache.py` 的 15 个测试搬到 CodingAgent 的 `tests/`。

## 问题 2：`mirror_profile` 与 `pip_index_profile` 两个字段缺一条推导

`CodeTaskSpec` 现在有两个表达"镜像策略"的字段:

| 字段 | 层 | 语义 |
|---|---|---|
| `mirror_profile` | 任务层输入 | config `repro_mirror_profile`,驱动 prompt(`_mirror_block` 指导 LLM 用哪个镜像) |
| `pip_index_profile` | 环境层记录 | M2 env spec 字段,写进 manifest(审计/复现用) |

**ReproAgent 的规矩**(`env_identity.py:156`):设了 `mirror_profile="cn"` 就自动把 spec 里的 `pip_index_profile` 也写成 "cn"。

**CodingAgent 现在缺这个自动映射**——若调用方(ResAgent)只传 `mirror_profile="cn"` 而没传 `pip_index_profile`,那么:

- prompt 里会正确指导 LLM 用国内镜像 ✅
- 但写进 manifest 的环境规格里 `pip_index_profile` 是空的 ❌

于是同一个环境,ReproAgent 建的记录写 "cn",CodingAgent 建的写 "",manifest 跨模块不一致。

**影响**:纯一致性瑕疵(f1bf535 已把 `pip_index_profile` 排除出身份指纹,不影响环境复用判断),只是 manifest 记录不一致。

**修法(最小,一行)**:在环境创建处(`agent.py` 读 `spec.pip_index_profile` 的地方,约 302 行)让 `pip_index_profile` 缺省时从 `mirror_profile` 推导:

```python
pip_index_profile = spec.pip_index_profile or spec.mirror_profile
```

> 注:更干净的长远做法是 `CodeTaskSpec` 删掉 `pip_index_profile` 这个 task 字段(它是历史遗留,ReproAgent 的 `ReproTask` 里没有),只留 `mirror_profile`、内部推导。但那是动契约的大改,暂不推荐;当前按最小修法。
