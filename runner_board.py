"""作战板自动刷新（读题并行化的零成本版）。

从原 runner_ctf2._write_board 抽出，独立成模块便于维护。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging


def _write_board(stubs: list, board_path: str) -> None:
    """每次 poll 自动刷新作战板（读题并行化的零成本版）。

    赛时教训（2026-10-24 羊城杯）：16 题只人工读了 1 题（15 行「未读」），
    读题吞吐是第一瓶颈。stub 已含题名/类型/分值/容器/附件/解出数——直接生成
    全量板子，人力只补「初步方向」一列，静态题立刻可见可分派。
    """
    try:
        now = datetime.now().strftime("%H:%M:%S")
        solved_n = sum(1 for s in stubs if s.get("is_solved"))
        rows = []
        for i, s in enumerate(sorted(stubs, key=lambda x: (bool(x.get("has_container")), x.get("category", ""))), 1):
            state = "✅已解" if s.get("is_solved") else ("▶在打" if s.get("_running") else "待打")
            rows.append(
                f"| {i} | {s.get('id', '')} | {s.get('name', '')} | {s.get('category', '')} "
                f"| {s.get('value', 0)} | {'靶机' if s.get('has_container') else ''}"
                f"{'附件' if s.get('has_attachments') else ''} | | {state} | |"
            )
        content = (
            f"# CTF² 作战板（runner 自动刷新 {now}）\n\n"
            f"> 已解 {solved_n}/{len(stubs)}；排序：静态优先（附件题最前）、容器题排后。\n"
            f"> 人力只需补「初步方向」列；失败退避/换题由 runner 自动执行。\n\n"
            f"| # | 题号 | 题名 | 类型 | 分值 | 靶机/附件 | 初步方向 | 状态 | flag |\n"
            f"|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
        )
        Path(board_path).write_text(content, encoding="utf-8")
    except Exception as e:
        logging.getLogger(__name__).debug("board 刷新失败: %s", e)
