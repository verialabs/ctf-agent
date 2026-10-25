"""单题解题编排：靶机获取(fail-closed) + 沙箱编排 + solver 分发。

集中了原 runner_ctf2._solve_one 的全部逻辑（五宗罪「上帝模块」拆分的目标模块之一）：
- 先登记「正在用此环境」再拉题（避免 env_guardian 误杀）；
- 容器题阻塞获取靶机地址，拿不到则 fail-closed 返回 target_unavailable，绝不带空地址盲启 solver；
- swarm.run() 由调用方用 asyncio.wait_for 包硬超时（沉溺保护）；
- finally 释放环境槽位。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.solver_base import FLAG_FOUND
from runner_console import console
from runner_env import _set_active_env, _dump_metadata


async def _solve_one(
    ctfd, settings, model_specs, challenges_root, cost_tracker, stub, no_submit, per_task_timeout,
) -> dict | None:
    from backend.agents.swarm import ChallengeSwarm
    from backend.prompts import ChallengeMeta

    try:
        # 先登记「我正在用这个环境」，再拉题目（pull_challenge 内部会起环境）
        if stub.get("has_container") and stub.get("id"):
            _set_active_env(stub["id"], stub.get("name", ""))

        # 拉取题目（附件 + metadata）
        ch_dir = await ctfd.pull_challenge(stub, str(challenges_root))
        meta = ChallengeMeta.from_yaml(Path(ch_dir) / "metadata.yml")

        # 容器题：阻塞获取靶机地址；拿不到则 fail-closed，绝不带空地址盲启 solver
        if stub.get("has_container") and not meta.connection_info and stub.get("id"):
            target = await ctfd.acquire_target(stub["id"], max_wait_s=180.0)
            if target:
                meta.connection_info = target
                _dump_metadata(ch_dir, meta)
                logging.getLogger(__name__).info("[%s] connection_info acquired: %s", stub.get("name"), target)
            else:
                logging.getLogger(__name__).error(
                    "[%s] 无法获取靶机地址（环境槽位争用/超时），fail-closed 放弃本题，不盲启 solver",
                    stub.get("name"),
                )
                _set_active_env(None)
                return {"flag": None, "status": "target_unavailable"}

        swarm = ChallengeSwarm(
            challenge_dir=ch_dir,
            meta=meta,
            ctfd=ctfd,
            cost_tracker=cost_tracker,
            settings=settings,
            model_specs=model_specs,
            no_submit=no_submit,
        )
        # 单题硬超时（止损）：超时计入失败退避，防单题硬啃吃掉整场（沉溺保护）
        if per_task_timeout and per_task_timeout > 0:
            outcome = await asyncio.wait_for(swarm.run(), timeout=per_task_timeout)
        else:
            outcome = await swarm.run()
        if isinstance(outcome, tuple):
            result = outcome[0]
        else:
            result = outcome
        if result is not None and result.status == FLAG_FOUND:
            return {"flag": result.flag, "status": result.status}
        return {"flag": None, "status": getattr(result, "status", None)}
    except Exception as e:
        logging.getLogger(__name__).error(f"[{stub.get('name')}] solve failed: {e}", exc_info=True)
        return None
    finally:
        # 容器题：解完（或失败）即释放环境，避免占用 Agent 唯一环境槽位
        if stub.get("has_container") and stub.get("id"):
            try:
                await ctfd.close_environment(stub["id"])
            except Exception:
                pass
            _set_active_env(None)
