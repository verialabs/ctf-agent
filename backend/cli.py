"""Click CLI entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from backend.config import Settings
from backend.models import DEFAULT_MODELS

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
@click.option("--ctfd-url", default=None, help="CTFd URL (overrides .env)")
@click.option("--ctfd-token", default=None, help="CTFd API token (overrides .env)")
@click.option(
    "--platform",
    default=None,
    type=click.Choice(["ctfd", "ret2shell", "ctfplus"]),
    help="Competition platform (overrides CTF_PLATFORM)",
)
@click.option("--game-id", default=None, type=int, help="Ret2Shell game ID")
@click.option("--competition-id", default=None, help="CTF+ competition ID")
@click.option("--problem-bank-id", default=None, help="CTF+ problem bank ID (optional)")
@click.option("--image", default="ctf-sandbox", help="Docker sandbox image name")
@click.option("--models", multiple=True, help="Model specs (default: all configured)")
@click.option(
    "--race-models",
    is_flag=True,
    help="Run every configured model on every challenge instead of splitting models across challenges",
)
@click.option("--no-prefetch", is_flag=True, help="Disable startup prefetch of all unsolved challenge metadata/files")
@click.option("--prefetch-concurrency", default=6, type=int, help="Concurrent challenge pulls during startup prefetch")
@click.option("--challenge", default=None, help="Solve a single challenge directory")
@click.option("--category", multiple=True, help="Coordinator category filter (repeatable)")
@click.option("--challenges-dir", default="challenges", help="Directory for challenge files")
@click.option("--no-submit", is_flag=True, help="Dry run — don't submit flags")
@click.option(
    "--coordinator-model",
    default=None,
    help="Coordinator model override (Codex default: gpt-5.5)",
)
@click.option("--coordinator", default="claude", type=click.Choice(["claude", "codex"]), help="Coordinator backend")
@click.option(
    "--max-challenges",
    default=10,
    type=int,
    help="Reusable concurrent challenge slots; queued challenges backfill freed slots",
)
@click.option("--msg-port", default=0, type=int, help="Operator message port (0 = auto)")
@click.option("--writeups-dir", default="writeups", help="Output directory for automatic Markdown write-ups")
@click.option("--no-writeups", is_flag=True, help="Disable automatic write-up generation")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def main(
    ctfd_url: str | None,
    ctfd_token: str | None,
    platform: str | None,
    game_id: int | None,
    competition_id: str | None,
    problem_bank_id: str | None,
    image: str,
    models: tuple[str, ...],
    race_models: bool,
    no_prefetch: bool,
    prefetch_concurrency: int,
    challenge: str | None,
    category: tuple[str, ...],
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator: str,
    max_challenges: int,
    msg_port: int,
    writeups_dir: str,
    no_writeups: bool,
    verbose: bool,
) -> None:
    """CTF Agent — multi-model solver swarm.

    Run without --challenge to start the full coordinator (Ctrl+C to stop).
    """
    _setup_logging(verbose)

    settings = Settings(sandbox_image=image)
    if ctfd_url:
        settings.ctfd_url = ctfd_url
    if ctfd_token:
        settings.ctfd_token = ctfd_token
    if platform:
        settings.ctf_platform = platform
    if game_id is not None:
        settings.ret2shell_game_id = game_id
    if competition_id:
        settings.ctfplus_competition_id = competition_id
    if problem_bank_id:
        settings.ctfplus_problem_bank_id = problem_bank_id
    settings.max_concurrent_challenges = max_challenges
    settings.split_models_across_challenges = not race_models
    settings.prefetch_all_challenges = not no_prefetch
    settings.challenge_prefetch_concurrency = prefetch_concurrency
    settings.writeups_dir = writeups_dir
    settings.generate_writeups = not no_writeups

    model_specs = list(models) if models else list(DEFAULT_MODELS)

    console.print("[bold]CTF Agent v2[/bold]")
    console.print(f"  CTFd: {settings.ctfd_url}")
    console.print(f"  Platform: {settings.ctf_platform}")
    if settings.ctf_platform == "ret2shell":
        console.print(f"  Game ID: {settings.ret2shell_game_id or 'auto'}")
    if settings.ctf_platform == "ctfplus":
        console.print(f"  Competition ID: {settings.ctfplus_competition_id or 'unset'}")
        if settings.ctfplus_problem_bank_id:
            console.print(f"  Problem bank ID: {settings.ctfplus_problem_bank_id}")
    console.print(f"  Models: {', '.join(model_specs)}")
    console.print(f"  Model assignment: {'race' if race_models else 'split'}")
    console.print(f"  Prefetch: {'disabled' if no_prefetch else f'concurrency={prefetch_concurrency}'}")
    console.print(f"  Image: {settings.sandbox_image}")
    console.print(f"  Max challenges: {max_challenges}")
    console.print(f"  Write-ups: {writeups_dir if not no_writeups else 'disabled'}")
    console.print()

    if challenge:
        asyncio.run(_run_single(settings, challenge, model_specs, no_submit, max_challenges))
    else:
        asyncio.run(_run_coordinator(settings, model_specs, challenges_dir, no_submit, coordinator_model, coordinator, max_challenges, msg_port, set(category)))


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
    from backend.prompts import ChallengeMeta
    from backend.ret2shell import create_competition_client
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore

    max_containers = max(1, len(model_specs))
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()

    challenge_path = Path(challenge_dir)
    meta_path = challenge_path / "metadata.yml"
    if not meta_path.exists():
        console.print(f"[red]No metadata.yml found in {challenge_dir}[/red]")
        sys.exit(1)

    meta = ChallengeMeta.from_yaml(meta_path)
    ctfd = create_competition_client(settings)
    if not meta.connection_info and hasattr(ctfd, "get_connection_info"):
        meta.connection_info = await ctfd.get_connection_info(meta.name)
        if meta.connection_info:
            console.print(f"[green]Instance:[/green] {meta.connection_info}")
        else:
            console.print("[yellow]No running instance endpoint was returned.[/yellow]")

    console.print(f"[bold]Challenge:[/bold] {meta.name} ({meta.category}, {meta.value} pts)")

    cost_tracker = CostTracker()

    swarm = ChallengeSwarm(
        challenge_dir=str(challenge_path),
        meta=meta,
        ctfd=ctfd,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=model_specs,
        no_submit=no_submit,
    )

    try:
        result = await swarm.run()
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
        await ctfd.close()


async def _run_coordinator(
    settings: Settings,
    model_specs: list[str],
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator_backend: str,
    max_challenges: int,
    msg_port: int = 0,
    allowed_categories: set[str] | None = None,
) -> None:
    """Run the full coordinator (continuous until Ctrl+C)."""
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore

    split_models = getattr(settings, "split_models_across_challenges", True)
    max_containers = max_challenges if split_models else max_challenges * len(model_specs)
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()
    console.print(f"[bold]Starting coordinator ({coordinator_backend}, Ctrl+C to stop)...[/bold]\n")

    if coordinator_backend == "codex":
        from backend.agents.codex_coordinator import run_codex_coordinator
        results = await run_codex_coordinator(
            settings=settings,
            model_specs=model_specs,
            challenges_root=challenges_dir,
            no_submit=no_submit,
            coordinator_model=coordinator_model,
            msg_port=msg_port,
            allowed_categories=allowed_categories,
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
            allowed_categories=allowed_categories,
        )

    console.print("\n[bold]Final Results:[/bold]")
    for challenge, data in results.get("results", {}).items():
        console.print(f"  {challenge}: {data.get('flag', 'no flag')}")
    console.print(f"\n[bold]Total cost: ${results.get('total_cost_usd', 0):.2f}[/bold]")


@click.command()
@click.argument("message")
@click.option("--port", default=9400, type=int, help="Coordinator message port")
@click.option("--host", default="127.0.0.1", help="Coordinator host")
def msg(message: str, port: int, host: str) -> None:
    """Send a message to the running coordinator."""
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
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        console.print("Is the coordinator running?")
        sys.exit(1)


if __name__ == "__main__":
    main()
