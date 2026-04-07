from __future__ import annotations

from dataclasses import dataclass, field


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

    def apply_trace_events(self, challenge_name: str, events: list[dict]) -> ChallengeWorkingMemory:
        memory = self.get(challenge_name)
        for event in events:
            if event.get("type") == "tool_result" and event.get("tool") == "submit_flag":
                tool = event.get("tool", "")
                result = event.get("result", "")
                if tool and result:
                    summary = f"{tool} returned {result}".strip()
                    if summary not in memory.failed_hypotheses:
                        memory.failed_hypotheses.append(summary)
            if event.get("type") == "bump":
                insight = str(event.get("insights", "")).strip()
                if insight and insight not in memory.last_guidance:
                    memory.last_guidance.append(insight)
            if event.get("type") == "tool_result" and "/challenge/" in str(event.get("result", "")):
                artifact = str(event.get("result", "")).strip()
                if artifact and artifact not in memory.useful_artifacts:
                    memory.useful_artifacts.append(artifact)
            if event.get("type") == "tool_result" and "platform rule:" in str(event.get("result", "")):
                finding = str(event.get("result", "")).strip()
                if finding and finding not in memory.verified_findings:
                    memory.verified_findings.append(finding)
        return memory
