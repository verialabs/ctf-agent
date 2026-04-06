from pathlib import Path

from backend.prompts import ChallengeMeta, build_prompt, list_distfiles


def test_list_distfiles_returns_recursive_relative_paths(tmp_path: Path) -> None:
    distfiles = tmp_path / "distfiles"
    (distfiles / "nested").mkdir(parents=True)
    (distfiles / "top.txt").write_text("top\n", encoding="utf-8")
    (distfiles / "nested" / "inner.txt").write_text("inner\n", encoding="utf-8")

    assert list_distfiles(str(tmp_path)) == ["nested/inner.txt", "top.txt"]


def test_build_prompt_marks_nested_images_for_vision(tmp_path: Path) -> None:
    distfiles = tmp_path / "distfiles"
    (distfiles / "images").mkdir(parents=True)
    (distfiles / "images" / "badge.png").write_bytes(b"png")

    prompt = build_prompt(
        ChallengeMeta(name="img", category="web", description="see image"),
        list_distfiles(str(tmp_path)),
    )

    attached_image_line = (
        "- `/challenge/distfiles/images/badge.png`  <- "
        "**IMAGE: call `view_image` immediately** (fix magic bytes first if corrupt)"
    )
    assert attached_image_line in prompt
