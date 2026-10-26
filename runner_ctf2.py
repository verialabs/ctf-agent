"""CTF² 快闪赛 runner — 无 coordinator 的轮询 + solver swarm 自动解题提交。

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
import json
import logging
import os
import sys
import time

import yaml
from pathlib import Path

import click
from rich.console import Console

from backend.solver_base import FLAG_FOUND

# 正在使用的靶机环境标记：scripts/env_guardian.py 靠它区分「在用」与「泄漏」。
# 必须在 ensure_environment 之前写入（pull_challenge 内部会起环境），否则守护进程会误杀。
_ACTIVE_ENV_FILE = Path(__file__).resolve().parent / "logs" / "active_env.json"


def _set_active_env(challenge_id: str | None, name: str = "") -> None:
    try:
        _ACTIVE_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        if challenge_id:
            _ACTIVE_ENV_FILE.write_text(
                json.dumps({"challenge_id": challenge_id, "name": name, "since": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif _ACTIVE_ENV_FILE.exists():
            _ACTIVE_ENV_FILE.unlink()
    except Exception:
        pass


def _dump_metadata(ch_dir: Path, meta) -> None:
    """把 ChallengeMeta 写回 metadata.yml（与 pull_challenge 一致的字段集合）。"""
    data = {
        "name": meta.name,
        "category": meta.category,
        "description": meta.description,
        "value": meta.value,
        "connection_info": meta.connection_info,
        "tags": meta.tags,
        "solves": meta.solves,
    }
    (ch_dir / "metadata.yml").write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


console = Console()


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
@click.option("-v", "--verbose", is_flag=True)
def main(poll: int, max_concurrent: int, models: tuple[str, ...], challenges_dir: str, no_submit: bool, once: bool, verbose: bool, per_task_timeout: int | None, container_timeout: int | None, board: str | None) -> None:
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

    ctfd = CTF2Client(base_url=base_url, token=token)
    if per_task_timeout is None:
        per_task_timeout = int(os.environ.get("CTF2_PER_TASK_TIMEOUT", "600"))
    if container_timeout is None:
        container_timeout = int(os.environ.get("CTF2_CONTAINER_TIMEOUT", "480"))
    if board is None:
        board = os.environ.get("CTF2_BOARD_PATH", "board_snapshot.md")

    console.print("[bold]CTF² Runner (ctf-agent v2 engines)[/bold]")
    console.print(f"  Base: {base_url}")
    console.print(f"  Models: {', '.join(model_specs)}")
    console.print(f"  Poll: {poll}s, Max concurrent: {max_concurrent}")
    console.print(f"  单题超时: {per_task_timeout}s（0=不限）, 靶机题: {container_timeout}s")
    console.print()

    asyncio.run(_loop(ctfd, settings, model_specs, challenges_root, max_concurrent, no_submit, poll, once,
                      per_task_timeout, container_timeout, board))


def _write_board(stubs: list[dict], board_path: str) -> None:
    """每次 poll 自动刷新作战板（读题并行化的零成本版）。

    赛时教训（2026-10-24 羊城杯）：16 题只人工读了 1 题（15 行「未读」），
    读题吞吐是第一瓶颈。stub 已含题名/类型/分值/容器/附件/解出数——直接生成
    全量板子，人力只补「初步方向」一列，静态题立刻可见可分派。
    """
    try:
        from datetime import datetime

        now = datetime.now().strftime("%H:%M:%S")
        solved_n = sum(1 for s in stubs if s.get("is_solved"))
        rows = []
        for i, s in enumerate(sorted(stubs, key=lambda x: (bool(x.get("has_container")), x.get("category", ""))), 1):
            state = "✅已解" if s.get("is_solved") else ("▶在打" if s.get("_running") else "待打")
            rows.append(
                f"| {i} | {s.get('id','')} | {s.get('name','')} | {s.get('category','')} "
                f"| {s.get('value',0)} | {'靶机' if s.get('has_container') else ''}"
                f"{'附件' if s.get('has_attachments') else ''} | | {state} | |"
            )
        content = (
            f"# CTF² 作战板（runner 自动刷新 {now}）\n\n"
            f"> 已解 {solved_n}/{len(stubs)}；排序：静态优先、容器题排后。\n"
            f"> 人力只需补「初步方向」列；失败退避/换题由 runner 自动执行。\n\n"
            f"| # | 题号 | 题名 | 类型 | 分值 | 靶机/附件 | 初步方向 | 状态 | flag |\n"
            f"|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
        )
        Path(board_path).write_text(content, encoding="utf-8")
    except Exception as e:
        logging.getLogger(__name__).debug("board 刷新失败: %s", e)


# ── 换题决策（沉溺保护）：失败退避 + 放弃门槛 + 单题硬超时 ──
_FAILED_BACKOFF_BASE_S = 300  # 首次失败退避 5 分钟
_FAILED_BACKOFF_CAP_S = 1800  # 退避封顶 30 分钟
_FAILED_GIVE_UP = 3           # 连续失败 3 次 → 本场放弃（时间留给未做题）


def _mark_failed(failed: dict, name: str) -> None:
    """任务完成未解出/异常/超时后登记：指数退避封顶 30min，3 次本场放弃。

    赛时教训（2026-10-24 羊城杯）：无失败记录的题会被下一轮 poll 无限重选，
    单题硬啃（如 ML 题枚举 215 个文件）吃掉整场时间。
    """
    import time as _time

    cnt, _ = failed.get(name, [0, 0.0])
    cnt += 1
    if cnt >= _FAILED_GIVE_UP:
        failed[name] = [cnt, float("inf")]
        console.print(f"[yellow]换题决策: {name} 已失败 {cnt} 次，本场放弃（时间留给其他题）[/yellow]")
        return
    backoff = min(_FAILED_BACKOFF_BASE_S * (2 ** (cnt - 1)), _FAILED_BACKOFF_CAP_S)
    failed[name] = [cnt, _time.time() + backoff]
    console.print(f"[dim]退避: {name} 失败 {cnt} 次，{backoff // 60}min 后可重试[/dim]")


def _effective_timeout(stub: dict, per_task_timeout: int, container_timeout: int) -> int:
    """靶机题取两者的较小值。

    靶机题在平台上串行（同时只允许 1 个环境），一道卡住就堵死整个容器队列，
    所以它必须比静态题更早止损。`per_task_timeout=0` 表示静态题不限时，
    但靶机题仍按 container_timeout 兜底。
    """
    if not stub.get("has_container"):
        return per_task_timeout
    if per_task_timeout and per_task_timeout > 0:
        return min(per_task_timeout, container_timeout)
    return container_timeout


async def _loop(
    ctfd: CTF2Client,
    settings: Settings,
    model_specs: list[str],
    challenges_root: Path,
    max_concurrent: int,
    no_submit: bool,
    poll: int,
    once: bool,
    per_task_timeout: int,
    container_timeout: int,
    board: str,
) -> None:
    from backend.agents.swarm import ChallengeSwarm
    from backend.cost_tracker import CostTracker
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore
    from backend.solver_base import FLAG_FOUND

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
                import time as _time

                now = _time.time()
                todo = [
                    s for s in stubs
                    if not s.get("is_solved") and s["name"] not in running and s["name"] not in solved
                    and not (s["name"] in failed and failed[s["name"]][1] > now)
                ]
                # 排序：静态优先（无容器）→ 容器题排后（平台限制同时 1 环境）
                todo.sort(key=lambda s: (bool(s.get("has_container")), s.get("solves") or 0))

                for stub in todo:
                    if len(running) >= max_concurrent:
                        break
                    if stub.get("has_container") and container_running >= 1:
                        continue  # 容器题串行：等上一个环境释放
                    name = stub["name"]
                    console.print(f"[cyan]开始: {name} ({stub.get('category','')}, {stub.get('value',0)}pts)[/cyan]")
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
        await ctfd.close()


async def _solve_one(
    ctfd: CTF2Client,
    settings: Settings,
    model_specs: list[str],
    challenges_root: Path,
    cost_tracker: CostTracker,
    stub: dict,
    no_submit: bool,
    per_task_timeout: int,
) -> dict | None:
    from backend.agents.swarm import ChallengeSwarm
    from backend.prompts import ChallengeMeta

    try:
        # 先登记「我正在用这个环境」，再拉题目（pull_challenge 内部会起环境）
        if stub.get("has_container") and stub.get("id"):
            _set_active_env(stub["id"], stub.get("name", ""))

        # 拉取题目（附件 + metadata）
        ch_dir = await ctfd.pull_challenge(stub, str(challenges_root))
        meta = ChallengeMeta.from_yaml(Path(ch_dir) / "metadata.yml")

        # 容器题：阻塞获取靶机地址；拿不到则 fail-closed，绝不带空地址盲启 solver
        if stub.get("has_container") and not meta.connection_info and stub.get("id"):
            target = await ctfd.acquire_target(stub["id"], max_wait_s=180.0)
            if target:
                meta.connection_info = target
                _dump_metadata(ch_dir, meta)
                logging.getLogger(__name__).info("[%s] connection_info acquired: %s", stub.get("name"), target)
            else:
                logging.getLogger(__name__).error(
                    "[%s] 无法获取靶机地址（环境槽位争用/超时），fail-closed 放弃本题，不盲启 solver",
                    stub.get("name"),
                )
                _set_active_env(None)
                return {"flag": None, "status": "target_unavailable"}

        swarm = ChallengeSwarm(
            challenge_dir=ch_dir,
            meta=meta,
            ctfd=ctfd,
            cost_tracker=cost_tracker,
            settings=settings,
            model_specs=model_specs,
            no_submit=no_submit,
        )
        # 单题硬超时（止损）：超时计入失败退避，防单题硬啃吃掉整场（沉溺保护）
        if per_task_timeout and per_task_timeout > 0:
            outcome = await asyncio.wait_for(swarm.run(), timeout=per_task_timeout)
        else:
            outcome = await swarm.run()
        if isinstance(outcome, tuple):
            result = outcome[0]
        else:
            result = outcome
        if result is not None and result.status == FLAG_FOUND:
            return {"flag": result.flag, "status": result.status}
        return {"flag": None, "status": getattr(result, "status", None)}
    except Exception as e:
        logging.getLogger(__name__).error(f"[{stub.get('name')}] solve failed: {e}", exc_info=True)
        return None
    finally:
        # 容器题：解完（或失败）即释放环境，避免占用 Agent 唯一环境槽位
        if stub.get("has_container") and stub.get("id"):
            try:
                await ctfd.close_environment(stub["id"])
            except Exception:
                pass
            _set_active_env(None)


if __name__ == "__main__":
    main()
