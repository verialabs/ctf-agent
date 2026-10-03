"""Background CTFd poller — detects new and solved challenges every 5 seconds."""

import asyncio
import logging
from dataclasses import dataclass, field

from backend.ctfd import CTFdClient

logger = logging.getLogger(__name__)


@dataclass
class PollEvent:
    kind: str  # "new_challenge" | "challenge_solved"
    challenge_name: str
    details: dict = field(default_factory=dict)


@dataclass
class CTFdPoller:
    """Polls CTFd every interval_s seconds, emits events for new/solved challenges."""

    ctfd: CTFdClient
    interval_s: float = 5.0

    _known_challenges: set[str] = field(default_factory=set)
    _challenge_details: dict[str, dict] = field(default_factory=dict)
    _known_solved: set[str] = field(default_factory=set)
    _event_queue: asyncio.Queue[PollEvent] = field(default_factory=asyncio.Queue)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        """Do initial poll (silent — no events) and start the background loop."""
        await self._seed()
        logger.info(
            "Poller initialized: %d challenges, %d solved",
            len(self._known_challenges),
            len(self._known_solved),
        )
        self._task = asyncio.create_task(self._loop(), name="ctfd-poller")

    async def _seed(self) -> None:
        """Initial fetch — just populate known state, no events."""
        try:
            stubs = await self.ctfd.fetch_challenge_stubs()
            self._challenge_details = {ch["name"]: ch for ch in stubs}
            self._known_challenges = {ch["name"] for ch in stubs}
            self._known_solved = await self.ctfd.fetch_solved_names()
        except Exception as e:
            logger.warning("Initial poll error: %s", e)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def get_event(self, timeout: float = 1.0) -> PollEvent | None:
        """Non-blocking get — returns None if no event within timeout."""
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            return None

    def drain_events(self) -> list[PollEvent]:
        """Drain all pending events without blocking."""
        events: list[PollEvent] = []
        while not self._event_queue.empty():
            try:
                events.append(self._event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    @property
    def known_challenges(self) -> set[str]:
        return set(self._known_challenges)

    @property
    def known_solved(self) -> set[str]:
        return set(self._known_solved)

    @property
    def challenge_details(self) -> dict[str, dict]:
        return {name: dict(details) for name, details in self._challenge_details.items()}

    async def _poll_once(self) -> None:
        try:
            stubs = await self.ctfd.fetch_challenge_stubs()
            current_details = {ch["name"]: ch for ch in stubs}
            current_names = {ch["name"] for ch in stubs}
            current_solved = await self.ctfd.fetch_solved_names()

            # Sanity check: if results look bogus compared to what we know, skip.
            if self._known_challenges and len(current_names) < len(self._known_challenges) // 2:
                logger.warning(f"Poll returned suspicious data ({len(current_names)} challenges vs {len(self._known_challenges)} known) — skipping")
                return
            # Don't let solved count regress (API might return empty on errors)
            if self._known_solved and not current_solved:
                logger.warning("Poll returned 0 solved (had %d) — skipping", len(self._known_solved))
                return

            # Detect new challenges
            new_challenges = current_names - self._known_challenges
            for name in new_challenges:
                logger.info("New challenge detected: %s", name)
                self._event_queue.put_nowait(
                    PollEvent("new_challenge", name, current_details.get(name, {}))
                )

            # Detect newly solved
            new_solves = current_solved - self._known_solved
            for name in new_solves:
                logger.info("Challenge solved: %s", name)
                self._event_queue.put_nowait(
                    PollEvent("challenge_solved", name)
                )

            self._known_challenges = current_names
            self._challenge_details = current_details
            self._known_solved = current_solved

        except Exception as e:
            logger.warning(f"Poll error: {e}")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.interval_s)
            await self._poll_once()
