from backend.control.knowledge_store import KnowledgeStore
from backend.control.working_memory import ChallengeWorkingMemory


def test_promote_verified_platform_rule_from_memory() -> None:
    store = KnowledgeStore()
    memory = ChallengeWorkingMemory(
        challenge_name="hatephp",
        verified_findings=["platform rule: Lingxu env题需要先 begin/run/addr"],
    )

    promoted = store.promote_from_memory(
        challenge_name="hatephp",
        category="web",
        memory=memory,
    )

    assert len(promoted) == 1
    assert promoted[0].scope == "platform"
    assert promoted[0].kind == "platform_rule"


def test_match_returns_category_knowledge_and_skips_applied_entry() -> None:
    store = KnowledgeStore()
    entry = store.upsert(
        scope="category",
        kind="exploit_pattern",
        content="php phar deserialization first",
        evidence="confirmed in two PHP challenges",
        confidence=0.9,
        source_challenge="hatephp",
        applicability={"category": "web"},
    )

    matched = store.match(
        category="web",
        challenge_name="web2",
        applied_ids={entry.id},
    )

    assert matched == []


def test_match_includes_lingxu_platform_knowledge_even_when_category_differs() -> None:
    store = KnowledgeStore()
    entry = store.upsert(
        scope="platform",
        kind="platform_rule",
        content="always start env via begin/run/addr first",
        evidence="observed across lingxu event tasks",
        confidence=0.95,
        source_challenge="hatephp",
        applicability={"category": "web", "platform": "lingxu-event-ctf"},
    )

    matched = store.match(
        category="crypto",
        challenge_name="web2",
        applied_ids=set(),
        platform="lingxu-event-ctf",
    )

    assert len(matched) == 1
    assert matched[0].id == entry.id


def test_promote_category_rule_from_memory_as_exploit_pattern() -> None:
    store = KnowledgeStore()
    memory = ChallengeWorkingMemory(
        challenge_name="web-sql",
        verified_findings=["category rule: php web题优先检查 phar metadata deserialize"],
    )

    promoted = store.promote_from_memory(
        challenge_name="web-sql",
        category="web",
        memory=memory,
    )

    assert len(promoted) == 1
    assert promoted[0].scope == "category"
    assert promoted[0].kind == "exploit_pattern"


def test_upsert_keeps_distinct_entries_for_same_content_with_different_applicability() -> None:
    store = KnowledgeStore()
    web_entry = store.upsert(
        scope="category",
        kind="exploit_pattern",
        content="same",
        evidence="e1",
        confidence=0.9,
        source_challenge="c1",
        applicability={"category": "web"},
    )
    crypto_entry = store.upsert(
        scope="category",
        kind="exploit_pattern",
        content="same",
        evidence="e2",
        confidence=0.8,
        source_challenge="c2",
        applicability={"category": "crypto"},
    )

    matched_web = store.match(category="web", challenge_name="other", applied_ids=set())
    matched_crypto = store.match(category="crypto", challenge_name="other", applied_ids=set())

    assert web_entry.id != crypto_entry.id
    assert [entry.id for entry in matched_web] == [web_entry.id]
    assert [entry.id for entry in matched_crypto] == [crypto_entry.id]


def test_match_requires_platform_context_for_platform_knowledge() -> None:
    store = KnowledgeStore()
    entry = store.upsert(
        scope="platform",
        kind="platform_rule",
        content="always start env via begin/run/addr first",
        evidence="observed across lingxu event tasks",
        confidence=0.95,
        source_challenge="hatephp",
        applicability={"category": "web", "platform": "lingxu-event-ctf"},
    )

    matched_other_platform = store.match(
        category="crypto",
        challenge_name="web2",
        applied_ids=set(),
        platform="ctfd",
    )
    matched_lingxu = store.match(
        category="crypto",
        challenge_name="web2",
        applied_ids=set(),
        platform="lingxu-event-ctf",
    )

    assert matched_other_platform == []
    assert len(matched_lingxu) == 1
    assert matched_lingxu[0].id == entry.id


def test_summary_for_respects_platform_filter() -> None:
    store = KnowledgeStore()
    store.upsert(
        scope="platform",
        kind="platform_rule",
        content="always start env via begin/run/addr first",
        evidence="observed across lingxu event tasks",
        confidence=0.95,
        source_challenge="hatephp",
        applicability={"platform": "lingxu-event-ctf"},
    )

    assert (
        store.summary_for(
            category="misc",
            challenge_name="target",
            applied_ids=set(),
            platform="ctfd",
        )
        == ""
    )


def test_promote_platform_rule_uses_platform_only_applicability() -> None:
    store = KnowledgeStore()
    memory = ChallengeWorkingMemory(
        challenge_name="c1",
        verified_findings=["platform rule: ctfd env has special header"],
    )

    promoted = store.promote_from_memory(
        challenge_name="c1",
        category="web",
        platform="ctfd",
        memory=memory,
    )

    assert len(promoted) == 1
    assert promoted[0].scope == "platform"
    assert promoted[0].kind == "platform_rule"
    assert promoted[0].applicability == {"platform": "ctfd"}


def test_platform_rule_does_not_match_on_wrong_platform_even_with_same_category() -> None:
    store = KnowledgeStore()
    memory = ChallengeWorkingMemory(
        challenge_name="c1",
        verified_findings=["platform rule: ctfd env has special header"],
    )
    promoted = store.promote_from_memory(
        challenge_name="c1",
        category="web",
        platform="ctfd",
        memory=memory,
    )

    matched_wrong_platform = store.match(
        category="web",
        challenge_name="other",
        applied_ids=set(),
        platform="lingxu-event-ctf",
    )
    matched_correct_platform = store.match(
        category="web",
        challenge_name="other",
        applied_ids=set(),
        platform="ctfd",
    )

    assert matched_wrong_platform == []
    assert [item.id for item in matched_correct_platform] == [promoted[0].id]
