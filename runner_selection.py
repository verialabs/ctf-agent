"""选题策略：静态优先（附件题最前）→ 解出数少优先；失败退避/放弃门槛过滤。

从原 runner_ctf2._loop 抽出，纯函数便于单测（test_p0_hardening 的 test_todo_sort_attachments_first）。
"""
from __future__ import annotations

import time


def _todo_sort_key(s: dict) -> tuple:
    """选题排序键：静态优先（无容器）→ 附件题（源码可审计）最前 → 解出数少的先。

    2026-10-24 快闪赛教训：Web 148 分题有源码+行号提示，本可静态 20 分钟到手，
    却排在 swarm 队列里。附件源码题是确定性分析的主战场，必须最优先。
    """
    return (bool(s.get("has_container")), not s.get("has_attachments"), s.get("solves") or 0)


def select_todo(stubs: list, solved: set, running: dict, failed: dict) -> list:
    """返回本轮候选派发列表（已过滤、已排序）。

    过滤规则（与 _loop 一致）：未解 & 未在跑 & 未放弃 & 未处于失败退避中。
    注意：max_concurrent 与容器题串行（container_running<1）约束仍由 _loop 在派发时判断，
    这里只负责"该不该进候选池"，保持纯函数便于单测。
    """
    now = time.time()
    todo = [
        s for s in stubs
        if not s.get("is_solved") and s["name"] not in running and s["name"] not in solved
        and not (s["name"] in failed and failed[s["name"]][1] > now)
    ]
    todo.sort(key=_todo_sort_key)
    return todo
