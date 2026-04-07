from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChallengeWorkingMemory:
    challenge_name: str
    attempted_actions: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    open_hypotheses: list[str] = field(default_factory=list)
    verified_findings: list[str] = field(default_factory=list)
    useful_artifacts: list[str] = field(default_factory=list)
    last_guidance: list[str] = field(default_factory=list)

    def to_summary(self) -> str:
        return "\n".join(
            [
                f"failed_hypotheses={self.failed_hypotheses[:3]}",
                f"open_hypotheses={self.open_hypotheses[:3]}",
                f"verified_findings={self.verified_findings[:3]}",
                f"useful_artifacts={self.useful_artifacts[:3]}",
                f"last_guidance={self.last_guidance[-2:]}",
            ]
        )


class WorkingMemoryStore:
    def __init__(self) -> None:
        self._memories: dict[str, ChallengeWorkingMemory] = {}

    def get(self, challenge_name: str) -> ChallengeWorkingMemory:
        return self._memories.setdefault(challenge_name, ChallengeWorkingMemory(challenge_name))

    def apply_trace_events(self, challenge_name: str, events: list[Any]) -> ChallengeWorkingMemory:
        memory = self.get(challenge_name)
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "tool_result" and event.get("tool") == "submit_flag":
                tool = str(event.get("tool", "")).strip()
                result = str(event.get("result", "")).strip()
                if tool and result and _is_failed_submit_result(result):
                    summary = f"{tool} returned {result}"
                    if summary not in memory.failed_hypotheses:
                        memory.failed_hypotheses.append(summary)
            if event_type == "bump":
                insight = str(event.get("insights", "")).strip()
                if insight and insight not in memory.last_guidance:
                    memory.last_guidance.append(insight)
            if event_type == "tool_result" and "/challenge/" in str(event.get("result", "")):
                artifact = str(event.get("result", "")).strip()
                if artifact and artifact not in memory.useful_artifacts:
                    memory.useful_artifacts.append(artifact)
            if event_type == "tool_result" and "platform rule:" in str(event.get("result", "")):
                finding = str(event.get("result", "")).strip()
                if finding and finding not in memory.verified_findings:
                    memory.verified_findings.append(finding)
        return memory


def _is_failed_submit_result(result: str) -> bool:
    normalized = result.strip().lower()
    if not normalized:
        return False

    failure_markers = (
        "incorrect",
        "wrong",
        "invalid",
        "rejected",
        "reject",
        "denied",
        "not correct",
        "bad flag",
        "failed",
    )
    if any(marker in normalized for marker in failure_markers):
        return True

    success_markers = (
        "correct",
        "accepted",
        "success",
        "confirmed",
        "already solved",
    )
    if any(marker in normalized for marker in success_markers):
        return False
    return False
