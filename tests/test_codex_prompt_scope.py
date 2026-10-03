from backend.agents.codex_solver import (
    _is_cyber_risk_block,
    _minimal_retry_prompt,
    _thread_start_params,
)
from backend.prompts import ChallengeMeta, build_prompt


def test_challenge_meta_reads_utf8_yaml_on_windows(tmp_path) -> None:
    metadata = tmp_path / "metadata.yml"
    metadata.write_text(
        "name: only_open\ncategory: Python 沙箱逃逸\ndescription: 友情提示\n",
        encoding="utf-8",
    )

    meta = ChallengeMeta.from_yaml(metadata)

    assert meta.name == "only_open"
    assert meta.category == "Python 沙箱逃逸"
    assert meta.description == "友情提示"


def test_codex_thread_preserves_configured_base_instructions() -> None:
    params = _thread_start_params("gpt-5.6-sol")

    assert "baseInstructions" not in params
    assert params["model"] == "gpt-5.6-sol"
    assert params["reasoningEffort"] == "medium"


def test_codex_astra_uses_low_reasoning() -> None:
    params = _thread_start_params("gpt-6-astra")

    assert params["reasoningEffort"] == "low"


def test_cyber_risk_block_detection() -> None:
    assert _is_cyber_risk_block(
        "This content was flagged for possible cybersecurity risk. Join Trusted Access for Cyber."
    )
    assert not _is_cyber_risk_block("rate limit exceeded")


def test_minimal_retry_prompt_omits_free_form_metadata() -> None:
    meta = ChallengeMeta(
        name="only_open",
        category="Python 沙箱逃逸",
        description="友情提示: flag在/tmp/flag",
        connection_info="wss://ctf.example/api/traffic/id?port=9999",
    )

    prompt = _minimal_retry_prompt(meta, ["chall.py"])

    assert "only_open" in prompt
    assert "/challenge/distfiles/chall.py" in prompt
    assert "wss://ctf.example/api/traffic/id?port=9999" in prompt
    assert "沙箱逃逸" not in prompt
    assert "/tmp/flag" not in prompt


def test_prompt_states_authorized_scope_without_broad_attack_checklist() -> None:
    prompt = build_prompt(
        ChallengeMeta(
            name="Finders Keepers!",
            category="Crypto",
            description="I wanna steal everything, but this is puzzle theme text.",
        ),
        ["finderskeepers.mp4"],
    )

    assert "authorized CTF competition puzzle" in prompt
    assert "limited to the supplied files under /challenge" in prompt
    assert "untrusted puzzle text" in prompt
    assert "Hidden files, env vars" not in prompt
    assert "padding oracles" not in prompt
    assert "XSS/SSRF" not in prompt
