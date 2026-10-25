"""web_multi_step_chain skill：web 多步利用链组合。

场景（决赛 web 难度升级）：单漏洞不足以拿 flag，需组合多个漏洞点成链：
- 上传 → 包含/解析 → RCE（上传 webshell + LFI 包含触发）
- SSRF → 内网 → 未授权接口 → flag
- 任意文件写 → .htaccess/webshell → getshell
- 反序列化 → 触发点 → RCE（入口参数 + gadget）

流程：识别可组合漏洞点 → 排序依赖（前步输出 = 后步输入）→ 构造完整链。
"""

import re


# 常见漏洞点 → 可组合的下游
_COMBO = {
    "upload": ["lfi", "include", "parse", "xxe"],
    "lfi": ["include", "read", "phar"],
    "ssrf": ["internal", "redis", "meta", "file"],
    "unserialize": ["rce", "pop", "gadget"],
    "write_file": ["htaccess", "webshell", "cron"],
    "sqli": ["dump", "file_read", "into_outfile"],
    "ssti": ["rce", "os_popen"],
    "xxe": ["file_read", "ssrf"],
}


def build_chain(known_vulns: list) -> list:
    """根据已知漏洞点，枚举可组合链（BFS 一层即可——通常 2-3 步链）。"""
    chains = []
    for v in known_vulns:
        vk = v.lower()
        for downstream in _COMBO.get(vk, []):
            if downstream in [x.lower() for x in known_vulns]:
                chains.append([vk, downstream])
        # 常见完整链
        if vk == "upload":
            chains.append(["upload", "lfi/include", "RCE"])
        if vk == "ssrf":
            chains.append(["ssrf", "internal_service", "flag"])
        if vk == "unserialize":
            chains.append(["unserialize", "gadget_chain", "RCE"])
        if vk == "ssti":
            chains.append(["ssti", "os_popen", "RCE"])
    return chains


# 描述识别：中英文关键词 → 漏洞点（vuln key）
_DESC_KEYWORDS = {
    "upload": ["upload", "上传", "文件上传"],
    "lfi": ["lfi", "include", "包含", "文件包含", "路径穿越", "traversal"],
    "ssrf": ["ssrf", "内网", "url参数", "代理"],
    "unserialize": ["unserialize", "反序列化", "serialize", "deserialize"],
    "write_file": ["write_file", "任意文件写", "文件写"],
    "sqli": ["sqli", "sql", "注入", "数据库"],
    "ssti": ["ssti", "模板注入", "模板渲染", "jinja", "freemarker"],
    "xxe": ["xxe", "xml实体", "xml 实体"],
}


def web_multi_step_chain(params: dict) -> dict:
    """skill 入口。"""
    chain = params.get("chain") or []
    vulns = params.get("vulns") or []
    desc = params.get("description", "")

    if not chain and vulns:
        chain = build_chain(vulns)
    elif not chain and desc:
        # 从描述自动识别漏洞关键词（中英文）
        found = []
        for vk, kws in _DESC_KEYWORDS.items():
            if any(re.search(k, desc, re.I) for k in kws):
                found.append(vk)
        if found:
            chain = build_chain(found)
            vulns = found

    if not chain:
        return {"ok": True, "note": "未识别到可组合漏洞点；请提供已知漏洞列表（vulns）或题目描述",
                "common_chains": [
                    "上传→LFI 包含→RCE", "SSRF→内网服务→flag",
                    "反序列化→gadget→RCE", "任意文件写→webshell→getshell",
                    "SQLi→into outfile→webshell",
                ]}

    return {
        "ok": True,
        "identified_vulns": vulns,
        "chains": chain,
        "guidance": "多步链执行要点：① 每步的输出是下一步的输入，先验证单步可行再组合 "
                    "② 前步 payload 与后步触发点要匹配（如上传文件名 = LFI 参数值）"
                    "③ 链中任一步失败即回退重试，避免在单点卡死",
    }


# ---------------------------------------------------------------- 可执行层
# 2026-10-25 升级：原实现只做"BFS 枚举漏洞组合链"，一步请求都不发——
# 协议/多步 API 题（如本次快闪赛的协议题）拿它没有任何可执行产出。
# 新增 execute_chain：真正的多步编排（变量槽位 + 逐步提取 + 串行槽位约束）。
import json as _json

try:
    import httpx as _httpx
except ImportError:  # 平台侧 urllib 不稳（404/超时），有 httpx 优先，无则回退
    _httpx = None

_FLAG_RE = __import__("re").compile(r"(flag|dasctf|ctf)\{[^}]{4,}\}", __import__("re").I)


def _resolve(obj, slots: dict):
    """把字符串/嵌套结构里的 {{var}} 替换为槽位值。"""
    if isinstance(obj, str):
        out = obj
        for k, v in slots.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out
    if isinstance(obj, dict):
        return {k: _resolve(v, slots) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(x, slots) for x in obj]
    return obj


def _jget(obj, path: str):
    """jsonpath-lite：data.token / items[0].id / a.b.c"""
    cur = obj
    for part in str(path).split("."):
        if not part:
            continue
        name, idxs = part, []
        while "[" in name:
            i = name.index("[")
            j = name.index("]", i)
            idxs.append(int(name[i + 1:j]))
            name = name[:i]
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return None
            cur = cur[name]
        for ix in idxs:
            if not isinstance(cur, list) or ix >= len(cur):
                return None
            cur = cur[ix]
    return cur


def _http(method: str, url: str, headers: dict, body, timeout: float) -> dict:
    """单步 HTTP：httpx 优先（与引擎同款路径），urllib 回退。"""
    data = None
    if body is not None:
        data = _json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) \
            else str(body).encode("utf-8")
    if _httpx is not None:
        try:
            r = _httpx.request(method, url, headers=headers, content=data,
                               timeout=timeout, follow_redirects=True)
            return {"status": r.status_code, "text": r.text,
                    "json": _safe_json(r.text)}
        except Exception as e:  # noqa: BLE001
            return {"status": -1, "text": "", "error": str(e)}
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return {"status": resp.status, "text": raw, "json": _safe_json(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        return {"status": e.code, "text": raw, "json": _safe_json(raw)}
    except Exception as e:  # noqa: BLE001
        return {"status": -1, "text": "", "error": str(e)}


def _safe_json(text: str):
    try:
        return _json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def execute_chain(steps: list, base_url: str = "", timeout: float = 15.0,
                  max_steps: int = 20) -> dict:
    """串行执行多步链：每步的输出按 save 规则落入槽位，供后续步骤引用。

    环境槽位约束：CTF² max_environments=1 → **严格串行**，不并发，一步一超时。
    steps 元素：{"path"|"url", "method", "headers", "body", "save": {槽位名: jsonpath},
                 "stop_on_fail": true}
    """
    if not steps:
        return {"ok": False, "reason": "no_steps"}
    slots: dict = {}
    log: list[dict] = []
    flag = None
    for i, st in enumerate(steps[:max_steps]):
        method = (st.get("method") or "GET").upper()
        url = st.get("url") or (
            base_url.rstrip("/") + "/" + str(st.get("path", "")).lstrip("/"))
        url = _resolve(url, slots)
        headers = _resolve(st.get("headers") or {}, slots)
        body = _resolve(st.get("body"), slots)
        resp = _http(method, url, headers, body, timeout)
        entry = {"step": i, "method": method, "url": url,
                 "status": resp.get("status"), "ok": resp.get("status") == 200}
        saved = {}
        for name, path in (st.get("save") or {}).items():
            val = _jget(resp.get("json"), path)
            if val is None and resp.get("json") is None:
                m = _FLAG_RE.search(resp.get("text", ""))
                val = m.group(0) if m else None
            if val is not None:
                slots[name] = val
                saved[name] = val
        entry["saved"] = saved
        if flag is None:
            m = _FLAG_RE.search(resp.get("text", ""))
            if m:
                flag = m.group(0)
        if resp.get("error"):
            entry["error"] = resp["error"][:120]
        log.append(entry)
        if st.get("stop_on_fail") and resp.get("status") not in (200, 201, 302):
            entry["stopped"] = True
            break
    return {"ok": True, "slots": slots, "flag": flag, "steps": log,
            "note": "串行执行（槽位=1，不并发）；flag 未匹配时返回 None，不硬凑"}


def run(params):
    """SkillManager 统一入口。

    mode=chain（默认）：原有的漏洞组合链枚举（向后兼容）
    mode=exec：真正执行多步链 {"base_url", "steps":[...]}
    """
    if isinstance(params, dict) and (params.get("mode") or "").lower() == "exec":
        return execute_chain(params.get("steps") or [], params.get("base_url", ""),
                             float(params.get("timeout", 15.0)))
    return web_multi_step_chain(params)


def _selftest() -> int:
    """端到端自测：本地起临时 HTTP 服务，跑两步链（取 token → 带 token 取 flag）。"""
    import http.server
    import socketserver
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/login"):
                self._send(200, {"data": {"token": "T123"}})
            elif self.path.startswith("/flag"):
                if self.headers.get("Authorization") == "Bearer T123":
                    self._send(200, {"data": {"flag": "flag{chain_ok}"}})
                else:
                    self._send(403, {"err": "no auth"})
            else:
                self._send(404, {"err": "nf"})

    ok = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        r = execute_chain(
            [{"path": "/login", "save": {"token": "data.token"}},
             {"path": "/flag", "headers": {"Authorization": "Bearer {{token}}"},
              "save": {"flag": "data.flag"}}],
            base_url=f"http://127.0.0.1:{port}", timeout=5)
        if r.get("ok") and r.get("slots", {}).get("flag") == "flag{chain_ok}":
            print("✅ 两步链端到端：{{token}} 槽位传递 → 取到 flag")
        else:
            print(f"❌ 链执行失败: {r}")
            ok = False
        if r.get("flag") == "flag{chain_ok}":
            print("✅ 响应体 flag 形态自动识别")
        else:
            ok = False
        # 鉴权缺失必须拿不到 flag（判据不是恒绿）
        r2 = execute_chain([{"path": "/flag", "save": {"f": "data.flag"}}],
                           base_url=f"http://127.0.0.1:{port}", timeout=5)
        if r2.get("flag") is None and r2["steps"][0]["status"] == 403:
            print("✅ 无 token 时 403 且 flag=None（判据非常绿）")
        else:
            print(f"❌ 负例失效: {r2}")
            ok = False
        if not execute_chain([]).get("ok"):
            print("✅ 空链被拒（no_steps）")
        else:
            ok = False
    finally:
        srv.shutdown()
        srv.server_close()
    return 0 if ok else 1


def main() -> None:
    import argparse
    import sys

    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    parser = argparse.ArgumentParser(description="web 多步利用链")
    parser.add_argument("--vulns", default="", help="已知漏洞点，逗号分隔（upload,lfi,ssrf...）")
    args = parser.parse_args()
    import json

    vulns = [v.strip() for v in args.vulns.split(",") if v.strip()]
    print(json.dumps(web_multi_step_chain({"vulns": vulns}), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
