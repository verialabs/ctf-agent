# skills/ — 可复用的解题配方（prompt 知识注入 + 沙箱内可执行）

每个配方由一对同名文件组成：

| 文件 | 作用 |
|---|---|
| `<name>.json` | 元数据，被 `backend/skills_bridge.py` 索引后注入 solver 的 system prompt |
| `<name>.py` | 可选。存在时即"可执行型"：容器内直接 `python3 /opt/ctf_skills/<name>.py` |

## `<name>.json` 字段

```json
{
  "name": "source_audit_patterns",
  "purpose": "一句话说明这题怎么打（会被截断到 220 字符塞进 prompt）",
  "input_spec": "run({...}) 的参数长得什么样",
  "output_spec": "{ok, ...} 返回什么",
  "categories": ["misc", "web"]
}
```

`categories` 决定命中哪个题类（`misc` / `web` / `pwn` / `crypto` / `rev`…）。
同一题类超过 `MAX_SKILLS_PER_PROMPT`（12）个时，按题名/描述关键词分词打分排序，
分数低的被截断——所以 `purpose` 里放**这类题的标志性词汇**（`pickle`、`反序列化`、
`ret2win`）比放形容词有用得多。

## 两种配方，选对

- **方法论型**（只有 `.json`）：让模型知道"这类题有现成打法"。对需要临场判断的题够用。
- **可执行型**（`.json` + `.py`）：精确字节、脚本化、易错的题（ELF 偏移计算、
  pickle 载荷构造、多步 API 串联）必须给可执行文件。只写方法论，模型会重新踩一遍坑。

## 挂载

`backend/sandbox.py` 把本目录只读挂到容器 `/opt/ctf_skills`（目录不存在则跳过）。
prompt 里给的是**容器内**路径——宿主机路径对容器内的模型没有意义。

## 关于配方语料

本目录只随仓提供少量示例配方。完整语料是部署方自己的资产：实战沉淀的配方里
往往包含真实赛题的 flag 与附件线索，不适合进公开仓库。把语料放在
`<repo>/skills/` 即可自动生效，无需改代码。
