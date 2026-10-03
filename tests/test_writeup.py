from pathlib import Path

from backend.prompts import ChallengeMeta
from backend.solver_base import FLAG_FOUND, SolverResult
from backend.writeup import generate_default_writeup


def test_generates_default_writeup_and_preserves_redacted_solution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "solve.py").write_text(
        "API_KEY=sk_testabcdefghijklmnop\nprint('solved')\n",
        encoding="utf-8",
    )
    (workspace / "binary.bin").write_bytes(b"\x00\x01")
    result = SolverResult(
        flag="ctf{real_flag}",
        status=FLAG_FOUND,
        findings_summary="Recovered the seed and decrypted the ciphertext.",
        step_count=12,
        cost_usd=0.25,
        log_path="logs/trace-demo.jsonl",
    )

    path = generate_default_writeup(
        meta=ChallengeMeta(name="Demo Challenge", category="Crypto", value=100, description="Demo"),
        result=result,
        winner_model="codex/gpt-5.6-sol",
        model_specs=["codex/gpt-5.6-sol", "deepseek/deepseek-flash"],
        findings={"deepseek/deepseek-flash": "Tried lattice reduction."},
        challenge_dir=str(tmp_path / "challenge"),
        workspace_dir=str(workspace),
        output_root=str(tmp_path / "writeups"),
        verified=True,
    )

    text = path.read_text(encoding="utf-8")
    copied = path.parent / "artifacts" / "solve.py"
    assert path == tmp_path / "writeups" / "Crypto" / "Demo-Challenge" / "README.md"
    assert "ctf{real_flag}" in text
    assert "VERIFIED" in text
    assert "codex/gpt-5.6-sol" in text
    assert copied.exists()
    assert "[REDACTED]" in copied.read_text(encoding="utf-8")
    assert not (path.parent / "artifacts" / "binary.bin").exists()


def test_candidate_status_for_unverified_writeup(tmp_path: Path) -> None:
    result = SolverResult("ctf{x}", FLAG_FOUND, "summary", 1, 0.0, "")
    path = generate_default_writeup(
        meta=ChallengeMeta(name="Candidate", category="Misc"),
        result=result,
        winner_model="model",
        model_specs=["model"],
        findings={},
        challenge_dir=str(tmp_path / "challenge"),
        workspace_dir="",
        output_root=str(tmp_path),
        verified=False,
    )
    assert "CANDIDATE" in path.read_text(encoding="utf-8")
