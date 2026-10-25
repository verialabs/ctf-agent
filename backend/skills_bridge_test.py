#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skills_bridge 关键词排序的回归 + 变异测试（2026-10-25 接线缺口）。

背景（真事故）：`skills_context` 曾用 `kw in (name + purpose)` 拿「题名 + 描述」
**整串**去比对单一 skill 的文本 —— 恒为 False，排序等于没写。池子于是退化成
文件名字母序，再被 `MAX_SKILLS_PER_PROMPT = 12` 截断。

实测后果（不是推测）：misc 类 21 个 skill 截前 12，pickle 反序列化题也拿不到
`source_audit_patterns`（字母序第 16）—— 技能写完了，引擎永远看不见。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend import skills_bridge as sb


def _mk_skills(root: Path, items):
    for name, purpose, cats in items:
        (root / f"{name}.json").write_text(
            json.dumps({"name": name, "purpose": purpose, "input_spec": "run(...)",
                        "categories": cats}, ensure_ascii=False), encoding="utf-8")


class TestKeywordRanking(unittest.TestCase):
    def setUp(self):
        self._orig = sb.SKILLS_DIR
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        # 19 个 filler 的字母序都在 target 之前 → 不修排序 target 必被截掉
        items = [(f"aaa_filler{i:02d}", f"填充技能 {i}", ["misc"]) for i in range(19)]
        items.append(("source_audit_patterns",
                      "源码审计：pickle.loads 反序列化 RCE / eval / os.system", ["misc"]))
        _mk_skills(root, items)
        sb.SKILLS_DIR = root
        sb._load_index.cache_clear()

    def tearDown(self):
        sb.SKILLS_DIR = self._orig
        sb._load_index.cache_clear()
        self._td.cleanup()

    def test_english_keyword_promotes_out_of_budget_skill(self):
        """21 个里只注入 12 个，字母序末尾的技能必须靠关键词挤进去。"""
        ctx = sb.skills_context("misc", "ezpickle python pickle deserialization getshell")
        self.assertIn("source_audit_patterns", ctx,
                      "pickle 关键词没能把 source_audit_patterns 排进前 12")

    def test_chinese_keyword_promotes(self):
        """中文 2 字即有语义，不能被英文 3 字符门槛砍掉。"""
        ctx = sb.skills_context("misc", "反序列化 打点")
        self.assertIn("source_audit_patterns", ctx)

    def test_chinese_long_phrase_uses_bigram(self):
        """中文没有分词器：整段"源码找漏洞"比不中"源码审计"，须退化为 2-gram。"""
        ctx = sb.skills_context("misc", "给一个 py 源码找漏洞")
        self.assertIn("source_audit_patterns", ctx, "中文 2-gram 退让没生效")

    def test_no_keyword_keeps_original_order(self):
        """不传关键词时必须保持旧行为（字母序 + 截断），不得顺手改动。"""
        ctx = sb.skills_context("misc", "")
        self.assertNotIn("source_audit_patterns", ctx)

    def test_zero_hit_keyword_is_stable(self):
        """零命中时顺序不变（sort 稳定性），不能因为分词把池子乱排。"""
        self.assertEqual(sb.skills_context("misc", "zzz_nomatch_word"),
                         sb.skills_context("misc", ""))

    def test_short_ascii_tokens_ignored(self):
        """2 字母英文词不参与打分，否则 'is'/'to' 会命中一切 purpose。"""
        ctx = sb.skills_context("misc", "is to of")
        self.assertNotIn("source_audit_patterns", ctx)

    def test_mutation_whole_string_match_would_fail(self):
        """变异护栏：整串匹配写法一旦回归，分词排序会被静默废掉。"""
        src = Path(sb.__file__).read_text(encoding="utf-8")
        self.assertNotIn('0 if kw in (e["name"] + e["purpose"]).lower() else 1', src,
                         "整串匹配写法回归了")
        self.assertIn("_TOKEN_RE", src)


class TestRealSkillsDir(unittest.TestCase):
    """真实 skills/ 目录冒烟：目录存在才跑，缺失 SKIP（不伪造绿）。"""

    def test_pickle_question_gets_source_audit(self):
        if not (sb.SKILLS_DIR / "source_audit_patterns.json").is_file():
            self.skipTest("skills/ 未包含 source_audit_patterns.json（干净 clone 的正常状态）")
        sb._load_index.cache_clear()
        ctx = sb.skills_context("misc", "ezpickle python pickle 反序列化 getshell")
        self.assertIn("source_audit_patterns", ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
