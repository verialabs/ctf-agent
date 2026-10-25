"""换题决策（沉溺保护）状态机 —— 五宗罪「死循环」护栏。

从原 runner_ctf2._loop 抽出，独立成可测模块：
- 失败指数退避，封顶 30min；
- 连续失败达 _FAILED_GIVE_UP 次 → 本场永久放弃（float('inf')）；
- 靶机题超时 = min(per_task, container)，且 per_task=0 时仍按 container 兜底。

护栏常量（base=300s / cap=1800s / give_up=3）是 2026-10-24 赛时教训定值，勿改。
"""
from __future__ import annotations

import time

from runner_console import console

_FAILED_BACKOFF_BASE_S = 300  # 首次失败退避 5 分钟
_FAILED_BACKOFF_CAP_S = 1800  # 退避封顶 30 分钟
_FAILED_GIVE_UP = 3  # 连续失败 3 次 → 本场放弃


def _mark_failed(failed: dict, name: str) -> None:
    """任务完成未解出/异常/超时后登记：指数退避封顶 30min，3 次本场放弃。

    赛时教训（2026-10-24 羊城杯）：无失败记录的题会被下一轮 poll 无限重选，
    单题硬啃（如 ML 题枚举 215 个文件）吃掉整场时间。
    """
    cnt, _ = failed.get(name, [0, 0.0])
    cnt += 1
    if cnt >= _FAILED_GIVE_UP:
        failed[name] = [cnt, float("inf")]
        console.print(f"[yellow]换题决策: {name} 已失败 {cnt} 次，本场放弃（时间留给其他题）[/yellow]")
        return
    backoff = min(_FAILED_BACKOFF_BASE_S * (2 ** (cnt - 1)), _FAILED_BACKOFF_CAP_S)
    failed[name] = [cnt, time.time() + backoff]
    console.print(f"[dim]退避: {name} 失败 {cnt} 次，{backoff // 60}min 后可重试[/dim]")


def _effective_timeout(stub: dict, per_task_timeout: int, container_timeout: int) -> int:
    """靶机题取两者的较小值；静态题取 per_task（0=不限时）。

    靶机题在平台上串行（同时只允许 1 个环境），一道卡住就堵死整个容器队列，
    所以它必须比静态题更早止损。`per_task_timeout=0` 表示静态题不限时，
    但靶机题仍按 container_timeout 兜底。
    """
    if not stub.get("has_container"):
        return per_task_timeout
    if per_task_timeout and per_task_timeout > 0:
        return min(per_task_timeout, container_timeout)
    return container_timeout
