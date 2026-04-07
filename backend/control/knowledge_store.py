from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from backend.control.working_memory import ChallengeWorkingMemory


@dataclass(slots=True)
class KnowledgeEntry:
    id: str
    scope: str
    kind: str
    content: str
    evidence: str
    confidence: float
    source_challenge: str
    applicability: dict[str, str] = field(default_factory=dict)


class KnowledgeStore:
    def __init__(self) -> None:
        self._entries: dict[str, KnowledgeEntry] = {}
        self._entry_by_key: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], str] = {}
        self._promotion_seen_by_challenge: dict[str, set[str]] = {}

    def upsert(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        evidence: str,
        confidence: float,
        source_challenge: str,
        applicability: dict[str, str] | None = None,
    ) -> KnowledgeEntry:
        normalized_scope = _norm(scope)
        normalized_kind = _norm(kind)
        normalized_content = _norm(content)
        normalized_applicability = _normalize_applicability(applicability or {})
        key = (
            normalized_scope,
            normalized_kind,
            normalized_content,
            _applicability_key(normalized_applicability),
        )

        existing_id = self._entry_by_key.get(key)
        if existing_id is not None:
            entry = self._entries[existing_id]
            if evidence and evidence not in entry.evidence:
                entry.evidence = f"{entry.evidence}\n{evidence}".strip()
            entry.confidence = max(entry.confidence, _normalize_confidence(confidence))
            if source_challenge and not entry.source_challenge:
                entry.source_challenge = source_challenge
            return entry

        entry_id = _make_entry_id(key)
        entry = KnowledgeEntry(
            id=entry_id,
            scope=normalized_scope,
            kind=normalized_kind,
            content=content.strip(),
            evidence=evidence.strip(),
            confidence=_normalize_confidence(confidence),
            source_challenge=source_challenge.strip(),
            applicability=normalized_applicability,
        )
        self._entries[entry_id] = entry
        self._entry_by_key[key] = entry_id
        return entry

    def match(
        self,
        *,
        category: str,
        challenge_name: str,
        applied_ids: set[str] | None = None,
        platform: str = "",
    ) -> list[KnowledgeEntry]:
        normalized_category = _norm(category)
        normalized_challenge_name = _norm(challenge_name)
        normalized_platform = _norm(platform)
        applied = applied_ids or set()

        matched: list[KnowledgeEntry] = []
        for entry in self._entries.values():
            if entry.id in applied:
                continue
            if _norm(entry.source_challenge) == normalized_challenge_name:
                continue
            if not _entry_matches_applicability(
                entry,
                category=normalized_category,
                platform=normalized_platform,
            ):
                continue
            matched.append(entry)

        return sorted(matched, key=lambda item: (-item.confidence, item.id))

    def promote_from_memory(
        self,
        *,
        challenge_name: str,
        category: str,
        memory: ChallengeWorkingMemory,
        platform: str = "",
    ) -> list[KnowledgeEntry]:
        promoted: list[KnowledgeEntry] = []
        seen = self._promotion_seen_by_challenge.setdefault(challenge_name, set())
        normalized_category = _norm(category)
        normalized_platform = _norm(platform)

        for finding in memory.verified_findings_for_promotion():
            if finding in seen:
                continue
            entry = self._promote_verified_finding(
                challenge_name=challenge_name,
                category=normalized_category,
                platform=normalized_platform,
                finding=finding,
            )
            seen.add(finding)
            if entry is not None:
                promoted.append(entry)

        return promoted

    def summary_for(
        self,
        *,
        category: str,
        challenge_name: str,
        applied_ids: set[str] | None = None,
        platform: str = "",
    ) -> str:
        matched = self.match(
            category=category,
            challenge_name=challenge_name,
            applied_ids=applied_ids or set(),
            platform=platform,
        )
        if not matched:
            return ""

        lines = ["Reusable knowledge:"]
        for entry in matched:
            lines.append(
                f"- [{entry.scope}/{entry.kind}] {entry.content} (confidence={entry.confidence:.2f})"
            )
        return "\n".join(lines)

    def _promote_verified_finding(
        self,
        *,
        challenge_name: str,
        category: str,
        platform: str,
        finding: str,
    ) -> KnowledgeEntry | None:
        lower_finding = finding.lower()

        if lower_finding.startswith("platform rule:"):
            content = finding.split(":", 1)[1].strip() or finding
            applicability: dict[str, str] = {}
            if platform:
                applicability["platform"] = platform
            elif "lingxu" in lower_finding:
                applicability["platform"] = "lingxu-event-ctf"
            if category:
                applicability["category"] = category
            return self.upsert(
                scope="platform",
                kind="platform_rule",
                content=content,
                evidence=f"verified in {challenge_name}: {finding}",
                confidence=0.92,
                source_challenge=challenge_name,
                applicability=applicability,
            )

        if lower_finding.startswith("exploit pattern:"):
            content = finding.split(":", 1)[1].strip() or finding
            applicability = {"category": category} if category else {}
            return self.upsert(
                scope="category",
                kind="exploit_pattern",
                content=content,
                evidence=f"verified in {challenge_name}: {finding}",
                confidence=0.85,
                source_challenge=challenge_name,
                applicability=applicability,
            )

        if lower_finding.startswith("category rule:"):
            content = finding.split(":", 1)[1].strip() or finding
            applicability = {"category": category} if category else {}
            return self.upsert(
                scope="category",
                kind="exploit_pattern",
                content=content,
                evidence=f"verified in {challenge_name}: {finding}",
                confidence=0.85,
                source_challenge=challenge_name,
                applicability=applicability,
            )

        return None


def _normalize_confidence(confidence: float) -> float:
    return max(0.0, min(1.0, float(confidence)))


def _normalize_applicability(applicability: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in applicability.items():
        key_norm = _norm(key)
        value_norm = _norm(value)
        if key_norm and value_norm:
            normalized[key_norm] = value_norm
    return normalized


def _entry_matches_applicability(entry: KnowledgeEntry, *, category: str, platform: str) -> bool:
    expected_platform = _norm(entry.applicability.get("platform", ""))
    platform_matches = (
        not expected_platform
        or expected_platform == "*"
        or (bool(platform) and expected_platform == platform)
    )
    expected_category = _norm(entry.applicability.get("category", ""))
    category_matches = (
        not expected_category
        or expected_category == "*"
        or (bool(category) and expected_category == category)
    )

    if expected_platform and expected_category:
        return platform_matches or category_matches
    if expected_platform:
        return platform_matches
    if expected_category:
        return category_matches
    return True


def _make_entry_id(key: tuple[str, str, str, tuple[tuple[str, str], ...]]) -> str:
    scope, kind, content, applicability_key = key
    applicability_text = ";".join(f"{item_key}={item_value}" for item_key, item_value in applicability_key)
    raw = f"{scope}|{kind}|{content}|{applicability_text}".encode()
    digest = hashlib.sha1(raw).hexdigest()[:12]
    return f"k-{digest}"


def _applicability_key(applicability: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(applicability.items()))


def _norm(text: str) -> str:
    return str(text).strip().lower()
