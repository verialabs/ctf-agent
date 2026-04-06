from pathlib import Path

import pytest
import yaml

from backend.challenge_import import (
    ManualChallengeImportError,
    ManualChallengeImportSpec,
    import_manual_challenge,
)


def test_import_manual_challenge_writes_metadata_and_recursive_distfiles(tmp_path: Path) -> None:
    file_attachment = tmp_path / "task.zip"
    file_attachment.write_bytes(b"zip-data")

    attachment_dir = tmp_path / "bundle"
    (attachment_dir / "src").mkdir(parents=True)
    (attachment_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    challenge_dir = import_manual_challenge(
        ManualChallengeImportSpec(
            name="登录器",
            category="web",
            description="分析登录逻辑并找到 flag。",
            output_dir=tmp_path / "challenges",
            connection_info="http://target.example.com",
            attachments=(file_attachment,),
            attachment_dirs=(attachment_dir,),
            value=100,
            tags=("web", "login"),
            hints=("先看登录流程",),
        )
    )

    assert challenge_dir == tmp_path / "challenges" / "登录器"

    metadata = yaml.safe_load((challenge_dir / "metadata.yml").read_text(encoding="utf-8"))
    assert metadata == {
        "name": "登录器",
        "category": "web",
        "description": "分析登录逻辑并找到 flag。",
        "value": 100,
        "connection_info": "http://target.example.com",
        "tags": ["web", "login"],
        "hints": [{"cost": 0, "content": "先看登录流程"}],
        "solves": 0,
    }
    assert (challenge_dir / "distfiles" / "task.zip").read_bytes() == b"zip-data"
    assert (challenge_dir / "distfiles" / "src" / "main.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_import_manual_challenge_restores_existing_directory_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "challenges"
    existing_dir = output_dir / "login"
    existing_dir.mkdir(parents=True)
    (existing_dir / "metadata.yml").write_text("name: old\n", encoding="utf-8")
    (existing_dir / "keep.txt").write_text("keep-me", encoding="utf-8")

    def raise_move_error(src: str, dst: str) -> str:
        raise OSError("simulated move failure")

    monkeypatch.setattr("backend.challenge_import.shutil.move", raise_move_error)

    with pytest.raises(OSError, match="simulated move failure"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="login",
                category="web",
                description="desc",
                output_dir=output_dir,
                connection_info="http://target.example.com",
            )
        )

    assert (existing_dir / "metadata.yml").read_text(encoding="utf-8") == "name: old\n"
    assert (existing_dir / "keep.txt").read_text(encoding="utf-8") == "keep-me"


def test_import_manual_challenge_overwrites_existing_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "challenges"
    stale_dir = output_dir / "demo"
    (stale_dir / "distfiles").mkdir(parents=True)
    (stale_dir / "distfiles" / "old.txt").write_text("stale\n", encoding="utf-8")

    fresh_file = tmp_path / "fresh.txt"
    fresh_file.write_text("fresh\n", encoding="utf-8")

    challenge_dir = import_manual_challenge(
        ManualChallengeImportSpec(
            name="demo",
            category="misc",
            description="new description",
            output_dir=output_dir,
            attachments=(fresh_file,),
        )
    )

    assert challenge_dir == stale_dir
    assert not (challenge_dir / "distfiles" / "old.txt").exists()
    assert (challenge_dir / "distfiles" / "fresh.txt").read_text(encoding="utf-8") == "fresh\n"


def test_import_manual_challenge_overwrites_existing_file_target(tmp_path: Path) -> None:
    output_dir = tmp_path / "challenges"
    output_dir.mkdir(parents=True)
    stale_file = output_dir / "demo"
    stale_file.write_text("stale-file\n", encoding="utf-8")

    challenge_dir = import_manual_challenge(
        ManualChallengeImportSpec(
            name="demo",
            category="misc",
            description="new description",
            output_dir=output_dir,
            connection_info="http://target.example.com",
        )
    )

    assert challenge_dir == stale_file
    assert challenge_dir.is_dir()
    metadata = yaml.safe_load((challenge_dir / "metadata.yml").read_text(encoding="utf-8"))
    assert metadata["name"] == "demo"
    assert not any(output_dir.glob(".backup-demo*"))


def test_import_manual_challenge_rejects_missing_payload_sources(tmp_path: Path) -> None:
    with pytest.raises(ManualChallengeImportError, match="连接信息、附件、附件目录不能同时为空"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="empty",
                category="web",
                description="no payload",
                output_dir=tmp_path / "challenges",
            )
        )


def test_import_manual_challenge_rejects_empty_attachment_dirs_without_other_payload_sources(tmp_path: Path) -> None:
    empty_attachment_dir = tmp_path / "empty-bundle"
    empty_attachment_dir.mkdir()

    with pytest.raises(ManualChallengeImportError, match="连接信息、附件、附件目录不能同时为空"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="empty-dir-only",
                category="web",
                description="no payload files",
                output_dir=tmp_path / "challenges",
                attachment_dirs=(empty_attachment_dir,),
            )
        )


def test_import_manual_challenge_rejects_missing_attachment_file(tmp_path: Path) -> None:
    with pytest.raises(ManualChallengeImportError, match="附件文件不存在"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="missing-file",
                category="misc",
                description="missing file",
                output_dir=tmp_path / "challenges",
                attachments=(tmp_path / "missing.zip",),
            )
        )


def test_import_manual_challenge_rejects_attachment_conflicts(tmp_path: Path) -> None:
    loose_file = tmp_path / "readme.txt"
    loose_file.write_text("from file\n", encoding="utf-8")

    attachment_dir = tmp_path / "bundle"
    attachment_dir.mkdir()
    (attachment_dir / "readme.txt").write_text("from dir\n", encoding="utf-8")

    with pytest.raises(ManualChallengeImportError, match=r"附件重名冲突：distfiles/readme.txt"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="conflict",
                category="misc",
                description="conflict payload",
                output_dir=tmp_path / "challenges",
                attachments=(loose_file,),
                attachment_dirs=(attachment_dir,),
            )
        )


def test_import_manual_challenge_rejects_case_only_attachment_conflicts(tmp_path: Path) -> None:
    loose_file = tmp_path / "readme.txt"
    loose_file.write_text("from file\n", encoding="utf-8")

    attachment_dir = tmp_path / "bundle"
    attachment_dir.mkdir()
    (attachment_dir / "README.txt").write_text("from dir\n", encoding="utf-8")

    with pytest.raises(ManualChallengeImportError, match=r"附件重名冲突：distfiles/README.txt"):
        import_manual_challenge(
            ManualChallengeImportSpec(
                name="case-conflict",
                category="misc",
                description="case conflict payload",
                output_dir=tmp_path / "challenges",
                attachments=(loose_file,),
                attachment_dirs=(attachment_dir,),
            )
        )


def test_import_manual_challenge_normalizes_text_and_filters_blank_tags_hints(tmp_path: Path) -> None:
    challenge_dir = import_manual_challenge(
        ManualChallengeImportSpec(
            name="  Demo Name  ",
            category="  misc  ",
            description="  normalized description  ",
            output_dir=tmp_path / "challenges",
            connection_info="  nc 127.0.0.1 2333  ",
            tags=(" tag1 ", " ", "", "tag2"),
            hints=(" hint1 ", "", "   "),
        )
    )

    metadata = yaml.safe_load((challenge_dir / "metadata.yml").read_text(encoding="utf-8"))
    assert metadata["name"] == "Demo Name"
    assert metadata["category"] == "misc"
    assert metadata["description"] == "normalized description"
    assert metadata["connection_info"] == "nc 127.0.0.1 2333"
    assert metadata["tags"] == ["tag1", "tag2"]
    assert metadata["hints"] == [{"cost": 0, "content": "hint1"}]
