"""Docker sandbox for CTF challenge solving — native async via aiodocker."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shlex
import socket
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiodocker

logger = logging.getLogger(__name__)

CONTAINER_LABEL = "ctf-agent"
# Owner of a container, so a new ctf-solve only cleans up after runs that are gone
OWNER_PID_LABEL = "ctf-agent.pid"
OWNER_HOST_LABEL = "ctf-agent.host"

# Concurrency control
_start_semaphore: asyncio.Semaphore | None = None
_active_count: int = 0
_count_lock = asyncio.Lock()

_WARN_THRESHOLDS = {100, 200, 500}


def configure_semaphore(max_concurrent: int = 50) -> None:
    """Set the max concurrent container starts. Call once at startup."""
    global _start_semaphore
    _start_semaphore = asyncio.Semaphore(max_concurrent)


async def _track_start() -> None:
    global _active_count
    async with _count_lock:
        _active_count += 1
        if _active_count in _WARN_THRESHOLDS:
            logger.warning("Active containers: %d", _active_count)


async def _track_stop() -> None:
    global _active_count
    async with _count_lock:
        _active_count = max(0, _active_count - 1)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


def _other_ctf_solve_running() -> bool:
    """Whether another ctf-solve process is alive here (Linux /proc; assume yes elsewhere)."""
    proc = Path("/proc")
    if not proc.is_dir():
        return True
    me = os.getpid()
    for p in proc.iterdir():
        if not p.name.isdigit() or int(p.name) == me:
            continue
        try:
            cmdline = (p / "cmdline").read_bytes()
        except OSError:
            continue
        if b"ctf-solve" in cmdline:
            return True
    return False


def _is_orphan(labels: dict[str, str]) -> bool:
    """True if no running ctf-solve process owns a container with these labels."""
    pid = labels.get(OWNER_PID_LABEL, "")
    if not pid.isdigit():
        # Created before owner labels existed: only safe to remove when no other run is active
        return not _other_ctf_solve_running()
    if labels.get(OWNER_HOST_LABEL) != socket.gethostname():
        return False  # another machine's run on a shared Docker daemon
    return not _pid_alive(int(pid))


async def cleanup_orphan_containers() -> None:
    """Remove leftover ctf-agent containers whose ctf-solve process is gone.

    Containers of other ctf-solve runs that are still going are left alone, so
    several runs can share the Docker daemon.
    """
    try:
        docker = aiodocker.Docker()
        try:
            containers = await docker.containers.list(
                all=True,
                filters={"label": [CONTAINER_LABEL]},
            )
            removed = kept = 0
            for c in containers:
                if not _is_orphan(c["Labels"] or {}):
                    kept += 1
                    continue
                try:
                    await c.delete(force=True)
                    removed += 1
                except Exception:
                    pass
            if removed:
                logger.info("Cleaned up %d orphan container(s)", removed)
            if kept:
                logger.info("Left %d container(s) of other running ctf-solve processes", kept)
        finally:
            await docker.close()
    except Exception as e:
        logger.warning("Orphan cleanup failed: %s", e)


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class DockerSandbox:
    """Isolated Docker container for a single solver agent."""

    image: str
    challenge_dir: str
    memory_limit: str = "16g"
    workspace_dir: str = ""
    _container: Any = field(default=None, repr=False)
    _docker: Any = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def container_id(self) -> str:
        """The Docker container ID, available after start()."""
        if not self._container:
            raise RuntimeError("Sandbox not started")
        return self._container.id

    def _parse_memory_limit(self) -> int:
        s = self.memory_limit.strip().lower()
        try:
            if s.endswith("g"):
                return int(s[:-1]) * 1024 * 1024 * 1024
            if s.endswith("m"):
                return int(s[:-1]) * 1024 * 1024
            return int(s)
        except (ValueError, IndexError):
            logger.warning("Invalid memory_limit %r, defaulting to 4GB", self.memory_limit)
            return 4 * 1024 * 1024 * 1024

    async def start(self) -> None:
        sem = _start_semaphore or asyncio.Semaphore(50)
        async with sem:
            self._docker = aiodocker.Docker()

            self.workspace_dir = tempfile.mkdtemp(prefix="ctf-workspace-")

            challenge_root = Path(self.challenge_dir).resolve()
            distfiles = str(challenge_root / "distfiles")
            meta_yml = str(challenge_root / "metadata.yml")

            binds: list[str] = [f"{self.workspace_dir}:/challenge/workspace:rw"]
            if Path(distfiles).exists():
                binds.append(f"{distfiles}:/challenge/distfiles:ro")
            if Path(meta_yml).exists():
                binds.append(f"{meta_yml}:/challenge/metadata.yml:ro")

            config = {
                "Image": self.image,
                "Cmd": ["sleep", "infinity"],
                "WorkingDir": "/challenge",
                "Tty": False,
                "Labels": {
                    CONTAINER_LABEL: "true",
                    OWNER_PID_LABEL: str(os.getpid()),
                    OWNER_HOST_LABEL: socket.gethostname(),
                },
                "HostConfig": {
                    "Binds": binds,
                    "ExtraHosts": ["host.docker.internal:host-gateway"],
                    "CapAdd": ["SYS_ADMIN", "SYS_PTRACE"],
                    "SecurityOpt": ["seccomp=unconfined"],
                    "Devices": [{"PathOnHost": "/dev/loop-control", "PathInContainer": "/dev/loop-control", "CgroupPermissions": "rwm"}],
                    "Memory": self._parse_memory_limit(),
                    "NanoCpus": int(2 * 1e9),
                },
            }

            self._container = await self._docker.containers.create(config)
            await self._container.start()
            await _track_start()

            info = await self._container.show()
            short_id = info["Id"][:12]
            logger.info("Sandbox started: %s", short_id)

    async def exec(self, command: str, timeout_s: int = 300) -> ExecResult:
        if not self._container:
            raise RuntimeError("Sandbox not started")

        async with self._lock:
            try:
                return await self._exec_inner(command, timeout_s)
            except aiodocker.exceptions.DockerError as e:
                # Container was deleted (e.g., sibling solver found the flag)
                return ExecResult(exit_code=-1, stdout="", stderr=f"Container gone: {e}")

    async def _exec_inner(self, command: str, timeout_s: int) -> ExecResult:
        # Wrap command with `timeout` so the container kills the process on expiry.
        # --signal=KILL ensures hard kill; --kill-after=5 is a safety net.
        wrapped = f"timeout --signal=KILL --kill-after=5 {timeout_s} bash -c {shlex.quote(command)}"
        exec_instance = await self._container.exec(
            cmd=["bash", "-c", wrapped],
            stdout=True,
            stderr=True,
            tty=False,
        )

        stream = exec_instance.start(detach=False)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _collect() -> None:
            while True:
                msg = await stream.read_out()
                if msg is None:
                    break
                if msg.stream == 1:
                    stdout_chunks.append(msg.data)
                else:
                    stderr_chunks.append(msg.data)

        try:
            # Give extra margin beyond the container-side timeout
            await asyncio.wait_for(_collect(), timeout=timeout_s + 30)
        except TimeoutError:
            try:
                await stream.close()
            except Exception:
                pass
            return ExecResult(
                exit_code=-1,
                stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
                stderr="Command timed out",
            )

        inspect = await exec_instance.inspect()
        exit_code = inspect.get("ExitCode", 0)

        return ExecResult(
            exit_code=exit_code,
            stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        )

    async def read_file(self, path: str) -> str | bytes:
        """Read a file from the container. Returns str for text, bytes for binary."""
        if not self._container:
            raise RuntimeError("Sandbox not started")

        try:
            tar = await asyncio.wait_for(
                self._container.get_archive(path),
                timeout=30,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Timed out reading {path}") from e

        # aiodocker 0.26.0 returns tarfile.TarFile directly
        with tar:
            for member in tar:
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        data = f.read()
                        try:
                            return data.decode("utf-8")
                        except UnicodeDecodeError:
                            return data
        raise FileNotFoundError(f"No file found at {path}")

    async def read_file_bytes(self, path: str) -> bytes:
        """Read a file from the container as raw bytes."""
        result = await self.read_file(path)
        if isinstance(result, str):
            return result.encode("utf-8")
        return result

    async def write_file(self, path: str, content: str | bytes) -> None:
        """Write a file into the container via tar archive."""
        if not self._container:
            raise RuntimeError("Sandbox not started")

        if isinstance(content, str):
            content = content.encode("utf-8")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=Path(path).name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        buf.seek(0)

        try:
            await asyncio.wait_for(
                self._container.put_archive(str(Path(path).parent), buf.getvalue()),
                timeout=30,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Timed out writing {path}") from e

    async def copy_from(self, container_path: str, host_path: str) -> None:
        """Copy a file from the container to the host."""
        data = await self.read_file_bytes(container_path)
        Path(host_path).parent.mkdir(parents=True, exist_ok=True)
        Path(host_path).write_bytes(data)

    async def stop(self) -> None:
        if self._container:
            try:
                await self._container.delete(force=True)
            except Exception:
                pass
            self._container = None
            await _track_stop()

        if self._docker:
            try:
                await self._docker.close()
            except Exception:
                pass
            self._docker = None

        if self.workspace_dir:
            import shutil
            try:
                shutil.rmtree(self.workspace_dir, ignore_errors=True)
            except Exception:
                pass
            self.workspace_dir = ""
        logger.info("Sandbox stopped")
