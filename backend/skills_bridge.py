"""Skill 库桥接：把 battle-tested 的 skill 方法论注入 solver prompt。

背景（2026-10-24 羊城杯 1/26 复盘）：runner 是裸 LLM 循环，零领域知识；
西湖论剑时代 170+ 场实战沉淀的 151 个 skill（crypto 36/web 20/misc 19/
rev 16/pwn 12/zip 7…）躺在融合仓没接线。本模块按题目 category 检索
skill 元数据（name + purpose + input_spec），拼成 prompt 知识段——
solver 由此知道「这类题有现成打法与参数」，再在沙箱里按方法论实现。

只做知识注入（prompt），不自动执行 skill 函数：确定性优先，
agent 判断该用哪个 skill 后在沙箱 import/复现（skills/ 已随仓内置）。
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# skill 库根目录（ctf-agent/skills/——与仓同生命周期）
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# 单题 prompt 里的 skill 知识预算：最多列 N 个、每个 purpose 截断 M 字符——
# 防止 36 个 crypto skill 全量灌入把 system prompt 撑爆（token 成本×注意力稀释）。
MAX_SKILLS_PER_PROMPT = 12
PURPOSE_MAX_CHARS = 220

# 沙箱内的挂载点（backend/sandbox.py 把 SKILLS_DIR 只读挂到这里）。
# prompt 里必须给出**容器内**路径：模型在 docker 里跑，宿主机路径它够不着。
SKILLS_DIR_IN_CONTAINER = "/opt/ctf_skills"

# 关键词分词：英文/数字按非字母数字切；中文按连续段切（\u4e00-\u9fff 保留）
_TOKEN_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def _is_cjk(w: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in w)


def _is_useful_token(w: str) -> bool:
    """过短的英文词（is/to/of…）不参与打分，避免噪声命中拉偏排序。

    中文 2 字即有语义（"协议"/"审计"），不能按 3 字符门槛砍掉。
    """
    if not w:
        return False
    if _is_cjk(w):
        return len(w) >= 2
    return len(w) >= 3


@lru_cache(maxsize=1)
def _load_index() -> dict[str, list[dict]]:
    """扫描 skills/*.json，按 category 建索引。

    json 字段：name / purpose / input_spec / output_spec / categories。
    解析失败的文件跳过并告警（不阻断启动）。
    """
    index: dict[str, list[dict]] = {}
    if not SKILLS_DIR.is_dir():
        logger.warning("skills 目录不存在: %s", SKILLS_DIR)
        return index
    for jf in sorted(SKILLS_DIR.glob("*.json")):
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("skill 元数据解析失败 %s: %s", jf.name, e)
            continue
        name = meta.get("name") or jf.stem
        purpose = (meta.get("purpose") or "").strip()
        input_spec = (meta.get("input_spec") or "").strip()
        cats = meta.get("categories") or []
        if not isinstance(cats, list):
            cats = [str(cats)]
        entry = {"name": name, "purpose": purpose, "input_spec": input_spec,
                 # 同名 .py 存在 = 可执行型配方（能在沙箱里直接跑，不只是方法论）
                 "exe": (SKILLS_DIR / f"{jf.stem}.py").is_file()}
        for c in cats:
            index.setdefault(str(c).strip().lower(), []).append(entry)
    return index


def skills_context(category: str, extra_keywords: str = "") -> str:
    """按 category 返回 skill 知识段（可直接嵌入 system prompt）。

    extra_keywords: 题名/描述关键词，用于二次匹配（skill 名/用途含该词的优先）。
    无匹配返回空串（调用方按空跳过，不加空段落）。
    """
    index = _load_index()
    cat = (category or "").strip().lower()
    pool = list(index.get(cat, []))
    # 兼容别名：WEB↔web、PWN↔pwn、REVERSE↔rev
    alias = {"web": ["web"], "pwn": ["pwn"], "misc": ["misc"],
             "crypto": ["crypto"], "reverse": ["rev", "reverse"],
             "rev": ["rev", "reverse"], "ai": ["misc", "ai"]}
    for a in alias.get(cat, []):
        for e in index.get(a, []):
            if e not in pool:
                pool.append(e)
    kw = (extra_keywords or "").strip().lower()
    if kw and pool:
        # ⚠️ 必须**分词**匹配。调用方传进来的是「题名 + 描述」整句
        # （prompts.py: f"{meta.name} {meta.description}"），拿整串做
        # `kw in (name + purpose)` 恒为 False —— 排序整个失效，池子退化成
        # 文件名字母序，再被 MAX_SKILLS_PER_PROMPT 截断。
        # 实测（2026-10-25）：misc 21 个 skill 截前 12，pickle 反序列化题
        # 排不进 source_audit_patterns（字母序第 16）—— 等于写了技能引擎看不见。
        tokens = [w for w in _TOKEN_RE.split(kw) if _is_useful_token(w)]
        if tokens:
            def _score(e: dict) -> int:
                hay = (e["name"] + " " + e["purpose"]).lower()
                hits = 0
                for w in tokens:
                    if w in hay:
                        hits += 1
                    elif _is_cjk(w) and len(w) > 2:
                        # 中文没有分词器，"源码找漏洞" 整段比不中 "源码审计"。
                        # 退化为 2-gram 子串探测（有命中即计 1 次，不重复计分）。
                        if any(w[i:i + 2] in hay for i in range(len(w) - 1)):
                            hits += 1
                # 负数：命中越多越靠前；零命中保持原序（sort 稳定）
                return -hits
            pool.sort(key=_score)
    if not pool:
        return ""
    pool = pool[:MAX_SKILLS_PER_PROMPT]
    lines = ["## Battle-tested Skill Library", "",
             f"此前实战沉淀的同类题打法（配方目录 `{SKILLS_DIR_IN_CONTAINER}/` 已只读挂进沙箱，可直接 import 或照抄实现）："]
    for e in pool:
        purpose = e["purpose"][:PURPOSE_MAX_CHARS] + ("…" if len(e["purpose"]) > PURPOSE_MAX_CHARS else "")
        lines.append(f"- **{e['name']}** — {purpose}")
        if e["input_spec"]:
            lines.append(f"  - 输入: {e['input_spec'][:160]}")
        if e.get("exe"):
            lines.append(f"  - 可直接执行: `python3 {SKILLS_DIR_IN_CONTAINER}/{e['name']}.py --help`"
                         f"（`--selftest` 可先自检环境）")
    lines.append("")
    return "\n".join(lines)
