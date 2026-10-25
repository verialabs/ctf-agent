"""CTF² 快闪赛 runner — CLI 入口与主流程编排。

原 425 行单体已拆为职责单一的模块（见 runner_*.py），本文件只保留：
- CLI 入口 main()（参数解析 + 起飞前检查 + 启动 _loop）；
- 主轮询循环 _loop()（编排核心：poll → 选题 → 派发 → 回收 → 退出清理）；
- 模块再导出（保持 runner_ctf2 作为统一命名空间，测试/脚本 import runner_ctf2.* 零改动）。

拆分后的可测模块：
  runner_console.py   共享 rich console 单例
  runner_backoff.py   失败退避/沉溺保护状态机（五宗罪「死循环」护栏）
  runner_selection.py 选题策略（静态优先/附件最前）
  runner_board.py     作战板自动刷新
  runner_env.py       单题环境标记 + metadata 落盘
  runner_solve.py     单题解题编排（靶机获取 fail-closed + solver 分发）

用法：
  python runner_ctf2.py [--poll 5] [--max-concurrent 4] [--models azure/gpt-5.5 azure/gpt-5.1] [--challenges-dir challenges]

环境变量：
  CTF2_AGENT_TOKEN      Agent token（必填）
  CTF2_BASE_URL         默认 https://ctf2.dasctf.com
  CTF2_COMPETITION_ID   （可省，自动发现）
  CTF2_STAGE_ID         （可省，自动发现）
  OPENAI_NEXT_API_KEY   模型网关 key（solver 用）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import click

# ── 模块再导出：保持 runner_ctf2 统一命名空间，测试 / 脚本零改动 ──
from runner_console import console  # noqa: F401
from runner_backoff import (  # noqa: F401
    _FAILED_BACKOFF_BASE_S,
    _FAILED_BACKOFF_CAP_S,
    _FAILED_GIVE_UP,
    _effective_timeout,
    _mark_failed,
)
from runner_selection import _todo_sort_key, select_todo  # noqa: F401
from runner_board import _write_board  # noqa: F401
from runner_env import _set_active_env, _dump_metadata  # noqa: F401
from runner_solve import _solve_one  # noqa: F401
from backend.solver_base import FLAG_FOUND  # noqa: F401  (legacy re-export)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiodocker").setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%X"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


@click.command()
@click.option("--poll", default=5, type=int, help="轮询间隔（秒）")
@click.option("--max-concurrent", default=4, type=int, help="并发解题数")
@click.option("--models", multiple=True, help="模型列表（默认全部）")
@click.option("--challenges-dir", default="challenges", help="题目缓存目录")
@click.option("--no-submit", is_flag=True, help="Dry run 不提交")
@click.option("--once", is_flag=True, help="单轮后退出（调试用）")
@click.option("--per-task-timeout", default=None, type=int, help="单题硬超时秒数（止损，默认 600=10min，0=不限；可用 CTF2_PER_TASK_TIMEOUT 覆盖）")
@click.option("--container-timeout", default=None, type=int, help="靶机题单独超时秒数（默认 480=8min；靶机串行，卡住会堵死唯一环境槽；可用 CTF2_CONTAINER_TIMEOUT 覆盖）")
@click.option("--board", default=None, type=str, help="作战板输出路径（默认 board_snapshot.md；可用 CTF2_BOARD_PATH 覆盖）")
@click.option("--practice", is_flag=True, help="练习场模式：打 /user/practice/（无比赛期间的练兵/演示赛道；契约见 docs/specs/CTF2练习场集成契约）")
@click.option("--practice-ground", default=None, type=str, help="练习场 ID（默认自动选题最多的场；可用 CTF2_PRACTICE_GROUND_ID 覆盖）")
@click.option("-v", "--verbose", is_flag=True)
@click.option("--skip-memguard", is_flag=True, help="跳过起飞前内存韧性检查（显式承担 OOM 静默击杀风险）")
@click.option("--force-claim", is_flag=True, help="强夺引擎租约（另一会话在跑时接管；对方下轮 poll 会感知并退出）")
def main(poll: int, max_concurrent: int, models: tuple[str, ...], challenges_dir: str, no_submit: bool, once: bool, verbose: bool, per_task_timeout: int | None, container_timeout: int | None, board: str | None, practice: bool, practice_ground: str | None, skip_memguard: bool, force_claim: bool) -> None:
    _setup_logging(verbose)

    token = os.environ.get("CTF2_AGENT_TOKEN", "")
    if not token:
        console.print("[red]缺少 CTF2_AGENT_TOKEN（环境变量）[/red]")
        sys.exit(1)

    from backend.config import Settings
    from backend.ctf2_client import CTF2Client
    from backend.models import DEFAULT_MODELS

    base_url = os.environ.get("CTF2_BASE_URL", "https://ctf2.dasctf.com")
    settings = Settings()
    model_specs = list(models) if models else list(DEFAULT_MODELS)
    challenges_root = Path(challenges_dir)
    challenges_root.mkdir(parents=True, exist_ok=True)

    # 网关 key 注入 settings（solver 的 resolve_model azure 分支读取）
    if not settings.azure_openai_api_key:
        settings.azure_openai_api_key = os.environ.get("OPENAI_NEXT_API_KEY", "")

    # 内存韧性守卫（2026-10-24 两次静默 OOM 击杀的工程解；阈值 CTF2_MIN_FREE_MB/CTF2_LOW_FREE_MB 可覆盖）
    from backend.memguard import assess_memory
    allowed_models, mem_warnings = assess_memory(len(model_specs), skip=skip_memguard)
    for w in mem_warnings:
        console.print(f"[yellow]{w}[/yellow]")
    if allowed_models == 0:
        sys.exit(1)
    if allowed_models < len(model_specs):
        model_specs = model_specs[:allowed_models]

    # 引擎独占租约（2026-10-24 快闪赛"多会话互踩容器"事故的工程解：一场一引擎）
    from backend.runner_lease import acquire_lease
    lease_ok, lease_msg = acquire_lease(force=force_claim)
    console.print(f"[{'green' if lease_ok else 'red'}]{lease_msg}[/]")
    if not lease_ok:
        sys.exit(1)

    ctfd = CTF2Client(base_url=base_url, token=token,
                      practice_mode=practice, practice_ground_id=practice_ground or "")
    if per_task_timeout is None:
        per_task_timeout = int(os.environ.get("CTF2_PER_TASK_TIMEOUT", "600"))
    if container_timeout is None:
        container_timeout = int(os.environ.get("CTF2_CONTAINER_TIMEOUT", "480"))
    if board is None:
        board = os.environ.get("CTF2_BOARD_PATH", "board_snapshot.md")

    console.print("[bold]CTF² Runner (ctf-agent v2 engines)[/bold]")
    console.print(f"  Base: {base_url}")
    console.print(f"  Mode: {'练习场 PRACTICE（无比赛练兵/演示赛道）' if practice else '比赛 COMPETITION'}")
    console.print(f"  Models: {', '.join(model_specs)}")
    console.print(f"  Poll: {poll}s, Max concurrent: {max_concurrent}")
    console.print(f"  单题超时: {per_task_timeout}s（0=不限）, 靶机题: {container_timeout}s")
    console.print()

    asyncio.run(_loop(ctfd, settings, model_specs, challenges_root, max_concurrent, no_submit, poll, once,
                      per_task_timeout, container_timeout, board))


async def _loop(
    ctfd, settings, model_specs, challenges_root, max_concurrent, no_submit, poll, once,
    per_task_timeout, container_timeout, board,
) -> None:
    from backend.cost_tracker import CostTracker
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore
    from backend.runner_lease import heartbeat as _lease_heartbeat
    from backend.runner_lease import lease_still_ours as _lease_ours
    from backend.runner_lease import release_lease

    configure_semaphore(max_concurrent * len(model_specs))
    try:
        await cleanup_orphan_containers()
    except Exception:
        pass
    _set_active_env(None)  # 清掉上次崩溃残留的标记，避免守护进程误判

    cost_tracker = CostTracker()
    running: dict[str, asyncio.Task] = {}
    solved: set[str] = set()
    failed: dict[str, list] = {}  # name -> [失败次数, 下次可重试 epoch]（换题决策：失败退避，3 次本场放弃）
    container_running = 0  # 平台限制 Agent 同时 1 个靶机环境
    touched_envs: set[str] = set()  # 本场起过环境的 challenge_id，退出时统一交还

    try:
        while True:
            # 租约心跳 + 被强夺感知（2026-10-24 多会话互踩事故的工程解）
            _lease_heartbeat()
            if not _lease_ours():
                console.print("[red]引擎租约已被其他会话强夺——体面退出（在跑的 solver 由平台侧回收）[/red]")
                break
            try:
                try:
                    stubs = await ctfd.fetch_challenge_stubs()
                except Exception as e:
                    console.print(f"[yellow]poll 失败: {e}[/yellow]")
                    await asyncio.sleep(5)
                    continue

                # 已解题目
                newly_solved = {s["name"] for s in stubs if s.get("is_solved")}
                if newly_solved - solved:
                    for n in newly_solved - solved:
                        console.print(f"[green]已解: {n}[/green]")
                solved |= newly_solved

                # 读题并行化（零成本版）：每次 poll 自动刷新全量作战板
                _write_board(stubs, board)

                # 待解：未解 & 未在跑 & 未失败退避中（换题决策：失败退避未到/已达放弃门槛的题不再选）
                # select_todo 抽出为纯函数（见 runner_selection），此处只做 max_concurrent / 容器串行的派发约束
                todo = select_todo(stubs, solved, running, failed)

                for stub in todo:
                    if len(running) >= max_concurrent:
                        break
                    if stub.get("has_container") and container_running >= 1:
                        continue  # 容器题串行：等上一个环境释放
                    name = stub["name"]
                    console.print(f"[cyan]开始: {name} ({stub.get('category', '')}, {stub.get('value', 0)}pts)[/cyan]")
                    if stub.get("has_container"):
                        container_running += 1
                        touched_envs.add(stub["id"])
                    task = asyncio.create_task(
                        _solve_one(ctfd, settings, model_specs, challenges_root, cost_tracker, stub, no_submit,
                                   _effective_timeout(stub, per_task_timeout, container_timeout)),
                        name=f"solve-{name}",
                    )
                    running[name] = task

                # 回收完成的任务
                done = [n for n, t in running.items() if t.done()]
                for n in done:
                    t = running.pop(n)
                    try:
                        r = await t
                        if r and r.get("flag"):
                            console.print(f"[bold green]FLAG: {n} → {r['flag']}[/bold green]")
                        elif r and r.get("status") == "target_unavailable":
                            # 环境槽位争用导致地址拿不到：非能力失败，下个轮询周期自然重试
                            console.print(f"[yellow]{n} 靶机地址获取失败（环境争用），下轮重试[/yellow]")
                        else:
                            console.print(f"[red]未解: {n}[/red]")
                            _mark_failed(failed, n)
                    except Exception as e:
                        console.print(f"[red]{n} 异常: {e}[/red]")
                        _mark_failed(failed, n)
                    finally:
                        # 容器题完成后释放环境槽位
                        if running.get(n) is None:
                            stub_by_name = next((s for s in stubs if s["name"] == n), None)
                            if stub_by_name and stub_by_name.get("has_container"):
                                container_running = max(0, container_running - 1)

                if not running:
                    console.print(f"[dim]空转一轮，{poll}s 后再查…[/dim]")
                if once:
                    if running:
                        console.print(f"[dim]--once：等待 {len(running)} 个任务完成…[/dim]")
                        done_all = await asyncio.gather(*running.values(), return_exceptions=True)
                        for n, r in zip(list(running.keys()), done_all):
                            if isinstance(r, dict) and r.get("flag"):
                                console.print(f"[bold green]FLAG: {n} → {r['flag']}[/bold green]")
                            elif isinstance(r, Exception):
                                console.print(f"[red]{n} 异常: {r}[/red]")
                            else:
                                console.print(f"[dim]{n} 完成（无 flag）[/dim]")
                        running.clear()
                    console.print("[dim]--once：结束，退出[/dim]")
                    break
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 单轮异常绝不退出：沙箱被删、平台抖动、磁盘满都只丢一轮
                console.print(f"[red]本轮异常（已兜住，继续跑）: {type(e).__name__}: {e}[/red]")
                logging.getLogger(__name__).exception("loop tick failed")
                await asyncio.sleep(max(poll, 5))

    except asyncio.CancelledError:
        console.print("[yellow]退出中…[/yellow]")
        for t in running.values():
            t.cancel()
        await asyncio.gather(*running.values(), return_exceptions=True)
    finally:
        # 退出前把本场起过的靶机环境交还平台。
        # 被 kill / 异常退出时 runner 不会自己释放，槽位会一直被占，
        # 而平台每个 Agent 只给 1 个环境 → 之后所有容器题全 409（2026-10-24 实锤）。
        for cid in list(touched_envs):
            try:
                await ctfd.close_environment(cid)
            except Exception:
                pass
        _set_active_env(None)
        # 释放引擎租约（正常/异常退出都释放；被 kill 杀则留给心跳过期自动接管）
        try:
            release_lease()
        except Exception:
            pass
        # 成本落盘（2026-10-24 快闪赛教训：只进内存=盲飞，runner 死后数据全丢；
        # $300 网关额度必须可度量。路径可用 CTF2_COST_PATH 指定）
        try:
            cost_path = os.environ.get("CTF2_COST_PATH") or f"logs/cost_{time.strftime('%Y%m%d_%H%M%S')}.json"
            written = cost_tracker.dump_json(cost_path)
            logging.getLogger(__name__).info("%s → %s", cost_tracker.fmt_summary(), written)
        except Exception as e:
            logging.getLogger(__name__).warning("cost 落盘失败: %s", e)
        await ctfd.close()


if __name__ == "__main__":
    main()
