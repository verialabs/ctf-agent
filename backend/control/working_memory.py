from __future__ import annotations

import re
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

    def verified_findings_for_promotion(self) -> list[str]:
        return [finding.strip() for finding in self.verified_findings if finding.strip()]


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
            if event_type == "tool_result":
                finding = _extract_verified_finding(str(event.get("result", "")))
                if finding and finding not in memory.verified_findings:
                    memory.verified_findings.append(finding)
        return memory


def _is_failed_submit_result(result: str) -> bool:
    normalized = result.strip().lower()
    if not normalized:
        return False

    # Ignore user-provided flag text when classifying result status.
    outside_quotes = re.sub(r'"[^"]*"', '""', normalized)

    failure_markers = (
        "incorrect",
        "rejected",
        "reject",
        "denied",
        "wrong answer",
        "not correct",
        "bad flag",
        "invalid flag",
        "submit failed",
        "submission failed",
    )
    if any(marker in outside_quotes for marker in failure_markers):
        return True

    if (
        re.search(r"\bcorrect\b", outside_quotes) is not None
        or "already solved" in outside_quotes
        or "accepted" in outside_quotes
        or "success" in outside_quotes
        or "confirmed" in outside_quotes
        or "您已提交了正确的flag" in normalized
        or "已提交了正确的flag" in normalized
    ):
        return False
    return False


def _extract_verified_finding(result: str) -> str:
    finding = result.strip()
    if not finding:
        return ""
    lowered = finding.lower()
    prefixes = ("platform rule:", "category rule:", "exploit pattern:")
    if any(marker in lowered for marker in prefixes):
        return finding
    return ""
