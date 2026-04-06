from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml


class ManualChallengeImportError(ValueError):
    """用户可读的导入失败错误。"""


@dataclass(slots=True)
class ManualChallengeImportSpec:
    name: str
    category: str
    description: str
    output_dir: Path
    connection_info: str = ""
    attachments: tuple[Path, ...] = ()
    attachment_dirs: tuple[Path, ...] = ()
    value: int = 0
    tags: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


def slugify_challenge_name(name: str) -> str:
    slug = re.sub(r'[<>:"/\\\\|?*.\x00-\x1f]', "", name.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-") or "challenge"


def _normalize_required_text(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ManualChallengeImportError(f"参数错误：{label}不能为空")
    return normalized


def _validate_spec(spec: ManualChallengeImportSpec) -> ManualChallengeImportSpec:
    name = _normalize_required_text("题目名称", spec.name)
    category = _normalize_required_text("题目类型", spec.category)
    description = _normalize_required_text("题目描述", spec.description)
    connection_info = spec.connection_info.strip()

    for file_path in spec.attachments:
        if not file_path.exists() or not file_path.is_file():
            raise ManualChallengeImportError(f"附件文件不存在：{file_path}")

    has_attachment_dir_files = False
    for attachment_dir in spec.attachment_dirs:
        if not attachment_dir.exists() or not attachment_dir.is_dir():
            raise ManualChallengeImportError(f"附件目录不存在：{attachment_dir}")
        if not has_attachment_dir_files:
            has_attachment_dir_files = any(path.is_file() for path in attachment_dir.rglob("*"))

    if not connection_info and not spec.attachments and not has_attachment_dir_files:
        raise ManualChallengeImportError("参数错误：连接信息、附件、附件目录不能同时为空")

    return ManualChallengeImportSpec(
        name=name,
        category=category,
        description=description,
        output_dir=spec.output_dir,
        connection_info=connection_info,
        attachments=spec.attachments,
        attachment_dirs=spec.attachment_dirs,
        value=spec.value,
        tags=tuple(tag.strip() for tag in spec.tags if tag.strip()),
        hints=tuple(hint.strip() for hint in spec.hints if hint.strip()),
    )


def _build_copy_plan(spec: ManualChallengeImportSpec) -> dict[Path, Path]:
    copy_plan: dict[Path, Path] = {}
    normalized_targets: dict[str, Path] = {}

    def register_copy_target(target: Path, source_path: Path) -> None:
        target_key = target.as_posix().casefold()
        if target_key in normalized_targets:
            raise ManualChallengeImportError(f"附件重名冲突：{target.as_posix()}")
        normalized_targets[target_key] = target
        copy_plan[target] = source_path

    for file_path in spec.attachments:
        target = Path("distfiles") / file_path.name
        register_copy_target(target, file_path)

    for attachment_dir in spec.attachment_dirs:
        for source_path in sorted(path for path in attachment_dir.rglob("*") if path.is_file()):
            target = Path("distfiles") / source_path.relative_to(attachment_dir)
            register_copy_target(target, source_path)

    return copy_plan


def _build_metadata(spec: ManualChallengeImportSpec) -> dict[str, object]:
    metadata: dict[str, object] = {
        "name": spec.name,
        "category": spec.category,
        "description": spec.description,
        "value": spec.value,
        "connection_info": spec.connection_info,
        "tags": list(spec.tags),
        "solves": 0,
    }
    if spec.hints:
        metadata["hints"] = [{"cost": 0, "content": hint} for hint in spec.hints]
    return metadata


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def import_manual_challenge(spec: ManualChallengeImportSpec) -> Path:
    spec = _validate_spec(spec)
    copy_plan = _build_copy_plan(spec)
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    challenge_dir = spec.output_dir / slugify_challenge_name(spec.name)

    with tempfile.TemporaryDirectory(dir=spec.output_dir, prefix=".tmp-import-") as tmp_root:
        temp_dir = Path(tmp_root) / "challenge"
        distfiles_dir = temp_dir / "distfiles"
        distfiles_dir.mkdir(parents=True)

        (temp_dir / "metadata.yml").write_text(
            yaml.dump(_build_metadata(spec), allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        for relative_target, source_path in copy_plan.items():
            target_path = temp_dir / relative_target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)

        backup_dir: Path | None = None
        if challenge_dir.exists():
            backup_dir = spec.output_dir / f".backup-{challenge_dir.name}"
            backup_suffix = 0
            while backup_dir.exists():
                backup_suffix += 1
                backup_dir = spec.output_dir / f".backup-{challenge_dir.name}-{backup_suffix}"
            challenge_dir.rename(backup_dir)

        try:
            shutil.move(str(temp_dir), str(challenge_dir))
        except Exception:
            if challenge_dir.exists():
                _remove_path(challenge_dir)
            if backup_dir is not None and backup_dir.exists():
                backup_dir.rename(challenge_dir)
            raise
        else:
            if backup_dir is not None and backup_dir.exists():
                _remove_path(backup_dir)

    return challenge_dir
