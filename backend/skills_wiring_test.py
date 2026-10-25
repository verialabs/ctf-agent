"""技能接线的端到端护栏（prompt 注入 + 沙箱挂载）。

背景：2026-10-25 实地核对发现实战分支 fix/runner-god-module 上
`grep -rli skill --include=*.py .` **零命中**——153 个 skill（含 3 个新写的
可执行配方）对引擎完全不可见：prompts.py 从不调用 skills_context，
sandbox.py 也不挂载 skills/。这类失效**没有任何报错**，题照样跑、只是白跑，
所以只能靠断言锁死两条链路：

1. build_prompt 里出现 skill 知识段，且可执行配方给出**容器内**路径
   （给宿主机路径等于没给：模型在 docker 里跑）；
2. build_binds 真的把 skills/ 只读挂到 SKILLS_DIR_IN_CONTAINER。

用例默认用 tempfile 造夹具 skill，不依赖仓里 153 个真实资产（精简 clone /
CI 上也能跑）；最后一条是真实资产集成用例，目录缺失时 SKIP。

跑法：python -m unittest backend.skills_wiring_test
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import sandbox as sbx  # noqa: E402
from backend import skills_bridge as sb  # noqa: E402
from backend.prompts import ChallengeMeta, build_prompt  # noqa: E402


def _meta(category: str, name: str = "ezpickle",
          desc: str = "python pickle 反序列化 getshell") -> ChallengeMeta:
    return ChallengeMeta(name=name, category=category, description=desc)


class _Fixture(unittest.TestCase):
    """造一个超过 prompt 预算的临时库，把两个模块的 SKILLS_DIR 都指过去。

    ⚠️ 必须造 > MAX_SKILLS_PER_PROMPT 个 skill：只有池子会被截断时，
    "字母序靠后 + 关键词命中"的排序才起作用——不然排序坏了用例也照样绿。
    """

    FILLERS = 12  # 12 个 filler + 1 个目标 = 13 > MAX_SKILLS_PER_PROMPT(12)

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        for i in range(self.FILLERS):
            (root / f"aaa_filler_{i:02d}.json").write_text(json.dumps({
                "name": f"aaa_filler_{i:02d}",
                "purpose": "通用杂项打法",
                "input_spec": "附件",
                "categories": ["misc"],
            }), encoding="utf-8")
        (root / "zzz_pickle_audit.json").write_text(json.dumps({
            "name": "zzz_pickle_audit",
            "purpose": "pickle 反序列化 RCE 审计与载荷构造",
            "input_spec": "源码或服务",
            "categories": ["misc"],
        }), encoding="utf-8")
        # 同名 .py = 可执行型配方
        (root / "zzz_pickle_audit.py").write_text("print('hi')\n", encoding="utf-8")
        self.root = root
        self._orig_sb = sb.SKILLS_DIR
        self._orig_sbx = sbx.SKILLS_DIR
        sb.SKILLS_DIR = root
        sbx.SKILLS_DIR = root
        sb._load_index.cache_clear()

    def tearDown(self):
        sb.SKILLS_DIR = self._orig_sb
        sbx.SKILLS_DIR = self._orig_sbx
        sb._load_index.cache_clear()
        self._td.cleanup()


class TestPromptInjection(_Fixture):
    def test_keyword_promotes_late_alphabet_skill(self):
        """字母序靠后的 skill 必须靠关键词挤进预算内（否则等于写了看不见）。"""
        prompt = build_prompt(_meta("misc"), [])
        self.assertIn("Skill Library", prompt)
        self.assertIn("zzz_pickle_audit", prompt)
        # 预算仍生效：不能因为排序把 13 个全灌进去
        listed = sum(1 for ln in prompt.splitlines() if ln.startswith("- **"))
        self.assertLessEqual(listed, sb.MAX_SKILLS_PER_PROMPT, f"超出预算: {listed}")

    def test_executable_skill_gives_container_path(self):
        """可执行配方给容器内路径；宿主机绝对路径不该进 prompt。"""
        prompt = build_prompt(_meta("misc"), [])
        self.assertIn(f"{sb.SKILLS_DIR_IN_CONTAINER}/zzz_pickle_audit.py", prompt)
        host = str(self.root.resolve()).replace("\\", "/").lower()
        self.assertNotIn(host, prompt.replace("\\", "/").lower())

    def test_no_skill_category_no_empty_section(self):
        """没有同类 skill 时不加空段落（别给模型灌一个空标题）。"""
        prompt = build_prompt(_meta("osint", name="x", desc="查人"), [])
        self.assertNotIn("Skill Library", prompt)

    def test_injection_failure_is_silent(self):
        """注入失败必须静默降级：prompt 构建在解题主链路上。"""
        orig = sb.skills_context

        def _boom(*a, **k):
            raise RuntimeError("模拟索引损坏")

        sb.skills_context = _boom
        try:
            prompt = build_prompt(_meta("misc"), [])
            self.assertIn("## Instructions", prompt)
        finally:
            sb.skills_context = orig


class TestSandboxMount(_Fixture):
    def test_skills_mounted_readonly(self):
        binds = sbx.build_binds("/tmp/ws", "/tmp/chal")
        hit = [b for b in binds if sb.SKILLS_DIR_IN_CONTAINER in b]
        self.assertEqual(len(hit), 1, f"skills 挂载缺失: {binds}")
        self.assertTrue(hit[0].endswith(":ro"), "技能目录必须只读挂载")

    def test_skills_dir_missing_no_crash(self):
        sbx.SKILLS_DIR = Path("/nonexistent/definitely-not-here")
        binds = sbx.build_binds("/tmp/ws", "/tmp/chal")
        self.assertFalse(any(sb.SKILLS_DIR_IN_CONTAINER in b for b in binds))

    def test_workspace_bind_always_present(self):
        binds = sbx.build_binds("/tmp/ws", "/tmp/chal")
        self.assertIn("/tmp/ws:/challenge/workspace:rw", binds)


class TestRealAssets(unittest.TestCase):
    """真实 skill 库集成用例：仓里没有 skills/ 时 SKIP（不假红）。"""

    def test_real_library_reaches_prompt(self):
        if not Path(sb.SKILLS_DIR).is_dir() or not any(Path(sb.SKILLS_DIR).glob("*.json")):
            self.skipTest("skills/ 无 json（精简 clone）")
        sb._load_index.cache_clear()
        prompt = build_prompt(_meta("misc"), [])
        self.assertIn("Skill Library", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
