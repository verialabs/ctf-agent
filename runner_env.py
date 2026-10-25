"""单题环境生命周期：靶机环境标记（env_guardian 区分在用/泄漏）+ metadata 落盘。

从原 runner_ctf2 抽出，集中环境副作用，避免散落各处。
"""
from __future__ import annotations

import json
import time
import yaml
from pathlib import Path

# 正在使用的靶机环境标记：scripts/env_guardian.py 靠它区分「在用」与「泄漏」。
# 必须在 ensure_environment 之前写入（pull_challenge 内部会起环境），否则守护进程会误杀。
_ACTIVE_ENV_FILE = Path(__file__).resolve().parent / "logs" / "active_env.json"


def _set_active_env(challenge_id, name: str = "") -> None:
    try:
        _ACTIVE_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        if challenge_id:
            _ACTIVE_ENV_FILE.write_text(
                json.dumps({"challenge_id": challenge_id, "name": name, "since": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif _ACTIVE_ENV_FILE.exists():
            _ACTIVE_ENV_FILE.unlink()
    except Exception:
        pass


def _dump_metadata(ch_dir: Path, meta) -> None:
    """把 ChallengeMeta 写回 metadata.yml（与 pull_challenge 一致的字段集合）。"""
    data = {
        "name": meta.name,
        "category": meta.category,
        "description": meta.description,
        "value": meta.value,
        "connection_info": meta.connection_info,
        "tags": meta.tags,
        "solves": meta.solves,
    }
    (ch_dir / "metadata.yml").write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
