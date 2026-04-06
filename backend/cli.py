"""Click CLI entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from pydantic import ValidationError
from rich.console import Console

from backend.challenge_import import (
    ManualChallengeImportError,
    ManualChallengeImportSpec,
    import_manual_challenge,
)
from backend.config import AllSolvedPolicy, Settings, WriteupMode
from backend.models import DEFAULT_MODELS
from backend.platforms import (
    PlatformConfigError,
    create_platform_client,
    validate_platform_settings,
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
@click.option(
    "--platform",
    default=None,
    type=click.Choice(["ctfd", "lingxu-event-ctf"]),
    help="题目来源平台，默认使用 ctfd",
)
@click.option(
    "--platform-url",
    default=None,
    help="平台根地址；使用凌虚赛事 CTF 时必填",
)
@click.option(
    "--lingxu-event-id",
    default=None,
    type=int,
    help="凌虚赛事 ID；使用凌虚赛事 CTF 时必填",
)
@click.option(
    "--lingxu-cookie",
    default=None,
    help="浏览器导出的凌虚 Cookie 原文",
)
@click.option(
    "--lingxu-cookie-file",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="从文件读取凌虚 Cookie，适合避免命令历史泄露",
)
@click.option("--ctfd-url", default=None, help="CTFd 地址，优先于 .env 配置")
@click.option("--ctfd-token", default=None, help="CTFd API 令牌，优先于 .env 配置")
@click.option("--image", default="ctf-sandbox", help="Docker 沙箱镜像名称")
@click.option("--models", multiple=True, help="模型规格，可重复传入；默认使用全部已配置模型")
@click.option("--challenge", default=None, help="只求解单个本地题目目录")
@click.option("--challenges-dir", default="challenges", help="题目根目录")
@click.option("--no-submit", is_flag=True, help="仅执行求解，不提交 flag")
@click.option("--coordinator-model", default=None, help="协调器使用的模型；默认按后端选择")
@click.option(
    "--coordinator",
    default="claude",
    type=click.Choice(["claude", "codex", "none"]),
    help="协调器后端；none 表示无总控整场模式",
)
@click.option("--max-challenges", default=10, type=int, help="最大并发题目数")
@click.option(
    "--all-solved-policy",
    default=None,
    type=click.Choice(["wait", "exit", "idle"]),
    help="全部题目已解出后的处理策略：wait 持续等待，exit 直接退出，idle 空闲超时后退出",
)
@click.option(
    "--all-solved-idle-seconds",
    default=None,
    type=int,
    help="当全部题目已解出且策略为 idle 时的空闲超时秒数",
)
@click.option(
    "--writeup-mode",
    default=None,
    type=click.Choice(["off", "confirmed", "solved"]),
    help="writeup 生成模式：off 关闭，confirmed 在确认成功后生成，solved 在解题成功后生成",
)
@click.option(
    "--writeup-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="writeup 输出目录",
)
@click.option("--msg-port", default=0, type=int, help="操作员消息端口，0 表示自动选择")
@click.option("-v", "--verbose", is_flag=True, help="输出详细日志")
def main(
    platform: str | None,
    platform_url: str | None,
    lingxu_event_id: int | None,
    lingxu_cookie: str | None,
    lingxu_cookie_file: Path | None,
    ctfd_url: str | None,
    ctfd_token: str | None,
    image: str,
    models: tuple[str, ...],
    challenge: str | None,
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator: str,
    max_challenges: int,
    all_solved_policy: AllSolvedPolicy | None,
    all_solved_idle_seconds: int | None,
    writeup_mode: WriteupMode | None,
    writeup_dir: Path | None,
    msg_port: int,
    verbose: bool,
) -> None:
    """CTF Agent 多模型题目求解入口。

    不传 `--challenge` 时启动完整协调器，按 Ctrl+C 停止。
    """
    _setup_logging(verbose)

    settings_kwargs: dict[str, object] = {"sandbox_image": image}
    if all_solved_policy is not None:
        settings_kwargs["all_solved_policy"] = all_solved_policy
    if all_solved_idle_seconds is not None:
        settings_kwargs["all_solved_idle_seconds"] = all_solved_idle_seconds
    if writeup_mode is not None:
        settings_kwargs["writeup_mode"] = writeup_mode
    if writeup_dir is not None:
        settings_kwargs["writeup_dir"] = str(writeup_dir)

    if all_solved_policy == "idle" and all_solved_idle_seconds is not None and all_solved_idle_seconds <= 0:
        raise click.ClickException("--all-solved-idle-seconds 必须大于 0")

    try:
        settings = Settings(**settings_kwargs)
    except ValidationError as exc:
        if "all_solved_idle_seconds" in str(exc):
            raise click.ClickException("--all-solved-idle-seconds 必须大于 0") from exc
        raise click.ClickException(str(exc)) from exc

    if platform:
        settings.platform = platform
    if platform_url:
        settings.platform_url = platform_url
    if lingxu_event_id is not None:
        settings.lingxu_event_id = lingxu_event_id
    if lingxu_cookie:
        settings.lingxu_cookie = lingxu_cookie
    if lingxu_cookie_file:
        settings.lingxu_cookie_file = str(lingxu_cookie_file)
    if ctfd_url:
        settings.ctfd_url = ctfd_url
    if ctfd_token:
        settings.ctfd_token = ctfd_token
    settings.max_concurrent_challenges = max_challenges

    try:
        validate_platform_settings(settings)
    except PlatformConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    model_specs = list(models) if models else list(DEFAULT_MODELS)

    console.print("[bold]CTF Agent v2[/bold]")
    console.print(f"  Platform: {settings.platform}")
    if settings.platform == "ctfd":
        console.print(f"  CTFd: {settings.ctfd_url}")
    else:
        console.print(f"  Platform URL: {settings.platform_url}")
        console.print(f"  Event ID: {settings.lingxu_event_id}")
    console.print(f"  Models: {', '.join(model_specs)}")
    console.print(f"  Image: {settings.sandbox_image}")
    console.print(f"  Max challenges: {max_challenges}")
    console.print(f"  All-solved policy: {settings.all_solved_policy}")
    if settings.all_solved_policy == "idle":
        console.print(f"  Idle timeout: {settings.all_solved_idle_seconds} seconds")
    console.print(f"  Writeup mode: {settings.writeup_mode}")
    if settings.writeup_mode != "off":
        console.print(f"  Writeup dir: {settings.writeup_dir}")
    console.print()

    if challenge:
        asyncio.run(_run_single(settings, challenge, model_specs, no_submit, max_challenges))
    else:
        asyncio.run(
            _run_coordinator(
                settings,
                model_specs,
                challenges_dir,
                no_submit,
                coordinator_model,
                coordinator,
                max_challenges,
                msg_port,
            )
        )


async def _run_single(
    settings: Settings,
    challenge_dir: str,
    model_specs: list[str],
    no_submit: bool,
    max_challenges: int,
) -> None:
    """Run a single challenge with a swarm."""
    from backend.agents.swarm import ChallengeSwarm
    from backend.cost_tracker import CostTracker
    from backend.deps import CoordinatorDeps
    from backend.prompts import ChallengeMeta
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore
    from backend.solve_lifecycle import finalize_swarm_result

    max_containers = max_challenges * len(model_specs)
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()

    challenge_path = Path(challenge_dir)
    meta_path = challenge_path / "metadata.yml"
    if not meta_path.exists():
        console.print(f"[red]No metadata.yml found in {challenge_dir}[/red]")
        sys.exit(1)

    meta = ChallengeMeta.from_yaml(meta_path)
    console.print(f"[bold]Challenge:[/bold] {meta.name} ({meta.category}, {meta.value} pts)")

    platform_client = create_platform_client(settings)
    cost_tracker = CostTracker()
    deps = CoordinatorDeps(
        ctfd=platform_client,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=model_specs,
        challenges_root=str(challenge_path.parent),
        no_submit=no_submit,
        max_concurrent_challenges=max_challenges,
    )

    swarm = ChallengeSwarm(
        challenge_dir=str(challenge_path),
        meta=meta,
        ctfd=platform_client,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=model_specs,
        no_submit=no_submit,
    )

    try:
        result = await swarm.run()
        await finalize_swarm_result(
            deps=deps,
            challenge_name=meta.name,
            challenge_dir=str(challenge_path),
            meta=meta,
            swarm=swarm,
            result=result,
        )
        from backend.solver_base import FLAG_FOUND

        if result and result.status == FLAG_FOUND:
            console.print(f"\n[bold green]FLAG FOUND:[/bold green] {result.flag}")
        else:
            console.print("\n[bold red]No flag found.[/bold red]")

        console.print("\n[bold]Cost Summary:[/bold]")
        for agent_name in cost_tracker.by_agent:
            console.print(f"  {agent_name}: {cost_tracker.format_usage(agent_name)}")
        console.print(f"  [bold]Total: ${cost_tracker.total_cost_usd:.2f}[/bold]")
    finally:
        await platform_client.close()


async def _run_coordinator(
    settings: Settings,
    model_specs: list[str],
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator_backend: str,
    max_challenges: int,
    msg_port: int = 0,
) -> None:
    """Run the full coordinator (continuous until Ctrl+C)."""
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore

    max_containers = max_challenges * len(model_specs)
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()
    label = "none/headless" if coordinator_backend == "none" else coordinator_backend
    console.print(f"[bold]Starting coordinator ({label}, Ctrl+C to stop)...[/bold]\n")

    if coordinator_backend == "codex":
        from backend.agents.codex_coordinator import run_codex_coordinator

        results = await run_codex_coordinator(
            settings=settings,
            model_specs=model_specs,
            challenges_root=challenges_dir,
            no_submit=no_submit,
            coordinator_model=coordinator_model,
            msg_port=msg_port,
        )
    elif coordinator_backend == "none":
        from backend.agents.headless_coordinator import run_headless_coordinator

        results = await run_headless_coordinator(
            settings=settings,
            model_specs=model_specs,
            challenges_root=challenges_dir,
            no_submit=no_submit,
            msg_port=msg_port,
        )
    else:
        from backend.agents.claude_coordinator import run_claude_coordinator

        results = await run_claude_coordinator(
            settings=settings,
            model_specs=model_specs,
            challenges_root=challenges_dir,
            no_submit=no_submit,
            coordinator_model=coordinator_model,
            msg_port=msg_port,
        )

    console.print("\n[bold]Final Results:[/bold]")
    for challenge_name, data in results.get("results", {}).items():
        console.print(f"  {challenge_name}: {data.get('flag', 'no flag')}")
    console.print(f"\n[bold]Total cost: ${results.get('total_cost_usd', 0):.2f}[/bold]")


@click.command()
@click.argument("message")
@click.option("--port", default=9400, type=int, help="协调器消息端口")
@click.option("--host", default="127.0.0.1", help="协调器主机地址")
def msg(message: str, port: int, host: str) -> None:
    """向运行中的协调器发送消息。"""
    import json
    import urllib.request

    body = json.dumps({"message": message}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/msg",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            console.print(f"[green]Sent:[/green] {data.get('queued', message[:200])}")
    except Exception as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        console.print("Is the coordinator running?")
        sys.exit(1)


def _count_imported_attachments(challenge_dir: Path) -> int:
    distfiles_dir = challenge_dir / "distfiles"
    if not distfiles_dir.exists():
        return 0
    return sum(1 for path in distfiles_dir.rglob("*") if path.is_file())


@click.command(name="ctf-import")
@click.option("--name", required=True, help="题目名称")
@click.option("--category", required=True, help="题目类型")
@click.option("--description", required=True, help="题目描述")
@click.option("--connection-info", default="", help="连接信息，例如 URL 或 nc host port")
@click.option(
    "--attachment",
    "attachments",
    multiple=True,
    type=click.Path(path_type=Path, exists=False),
    help="单个附件文件，可重复传入",
)
@click.option(
    "--attachment-dir",
    "attachment_dirs",
    multiple=True,
    type=click.Path(path_type=Path, file_okay=False),
    help="附件目录，会递归拷贝其中的文件",
)
@click.option(
    "--output-dir",
    default=Path("challenges"),
    type=click.Path(path_type=Path, file_okay=False),
    help="导入后的题目输出目录",
)
@click.option("--value", default=0, type=int, help="题目分值")
@click.option("--tag", "tags", multiple=True, help="题目标签，可重复传入")
@click.option("--hint", "hints", multiple=True, help="题目提示，可重复传入")
def import_cmd(
    name: str,
    category: str,
    description: str,
    connection_info: str,
    attachments: tuple[Path, ...],
    attachment_dirs: tuple[Path, ...],
    output_dir: Path,
    value: int,
    tags: tuple[str, ...],
    hints: tuple[str, ...],
) -> None:
    """把手工整理的题目信息导入为本地题目目录。"""
    try:
        challenge_dir = import_manual_challenge(
            ManualChallengeImportSpec(
                name=name,
                category=category,
                description=description,
                output_dir=output_dir,
                connection_info=connection_info,
                attachments=attachments,
                attachment_dirs=attachment_dirs,
                value=value,
                tags=tags,
                hints=hints,
            )
        )
    except ManualChallengeImportError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("导入成功：")
    click.echo(f"题目名称：{name}")
    click.echo(f"题目目录：{challenge_dir}")
    click.echo(f"附件数量：{_count_imported_attachments(challenge_dir)}")
    click.echo(f"连接信息：{'已写入' if connection_info.strip() else '未写入'}")


if __name__ == "__main__":
    main()
