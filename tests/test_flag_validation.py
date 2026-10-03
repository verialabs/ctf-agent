import pytest

from backend.flag_validation import validate_flag_candidate


@pytest.mark.parametrize("candidate", ["", "  ", "CTF{flag}", "flag{placeholder}", "x\n y"])
def test_rejects_invalid_or_placeholder_flags(candidate: str) -> None:
    valid, _, _ = validate_flag_candidate(candidate)
    assert not valid


@pytest.mark.parametrize("candidate", ["CTF{real_value}", "answer-without-braces", "比赛{答案}"])
def test_accepts_competition_agnostic_flags(candidate: str) -> None:
    valid, reason, normalized = validate_flag_candidate(f"  {candidate}  ")
    assert valid, reason
    assert normalized == candidate
