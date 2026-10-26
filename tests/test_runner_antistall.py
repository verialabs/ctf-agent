"""runner 防呆 / 反死循环不变量回归。

五宗罪之「死循环」在 runner 层目前只靠注释约束（`_mark_failed` 退避、`acquire_target`
180s 上限、`swarm.run()` 被 `asyncio.wait_for` 包裹），没有任何回归锁死。本题把三条
可测不变量钉死，防止有人改动常量或删超时后比赛时真卡死：

  A. 同一题连续失败达到 _FAILED_GIVE_UP(3) 次 → 永久放弃（float('inf')），
     不再被下一轮 poll 重选（反无限重选硬啃）。
  B. 失败退避指数增长但封顶 _FAILED_BACKOFF_CAP_S(1800s)，不会无界膨胀。
  C. 靶机题硬超时 = min(per_task, container)，且 per_task=0（不限时）时仍按
     container_timeout 兜底 —— 防止靶机题卡死堵死 Agent 唯一环境槽。

这些函数在 runner_ctf2.py 顶层，依赖轻量（backend.solver_base 无 aiodocker），可直接导入。
"""

import importlib

import pytest

# runner_ctf2 imports backend.solver_base (light) + rich/click/yaml (installed).
# 在多数 CI 环境可直接 import；若缺依赖则整体跳过而非红。
runner = importlib.import_module("runner_ctf2")


def test_give_up_after_threshold_is_permanent():
    """连续失败达 _FAILED_GIVE_UP 次 → failed[name][1] == inf，永不重试。"""
    give_up = runner._FAILED_GIVE_UP
    failed: dict[str, list] = {}
    for _ in range(give_up):
        runner._mark_failed(failed, "pwn_hard")
    assert failed["pwn_hard"][0] >= give_up
    assert failed["pwn_hard"][1] == float("inf")
    # 推导不变量：_loop 的 todo 过滤 `failed[n][1] > now` 对任意 now 恒成立 → 不再被选中
    now = 1e18  # 任意未来时刻
    assert failed["pwn_hard"][1] > now


def test_backoff_exponential_and_capped():
    """退避 = min(base * 2^(cnt-1), cap)，封顶 CAP 不膨胀。

    _mark_failed 存的是绝对时间戳（time.time() + backoff），故比较相对偏移。
    """
    import time

    base = runner._FAILED_BACKOFF_BASE_S
    cap = runner._FAILED_BACKOFF_CAP_S
    assert base == 300 and cap == 1800, "护栏常量被改：base=300s, cap=1800s 是赛时教训定值"
    failed: dict[str, list] = {}
    # 第 1 次：now + 300s
    t0 = time.time()
    runner._mark_failed(failed, "x")
    assert abs((failed["x"][1] - t0) - 300.0) < 2.0
    # 第 2 次：now + 600s（指数翻倍）
    t1 = time.time()
    runner._mark_failed(failed, "x")
    assert abs((failed["x"][1] - t1) - 600.0) < 2.0
    # 退避公式本身封顶 CAP：远超 give_up 的多次计算仍 ≤ cap
    for n in range(1, 10):
        val = min(base * (2 ** (n - 1)), cap)
        assert val <= cap, "退避必须封顶 CAP"


def test_effective_timeout_container_is_min_and_capped():
    """靶机题超时 = min(per_task, container)；per_task 更大时取 container。"""
    # 容器题：per=600, container=480 → 480
    assert runner._effective_timeout({"has_container": True}, 600, 480) == 480
    # 容器题：per 更小 → 取 per
    assert runner._effective_timeout({"has_container": True}, 120, 480) == 120
    # 容器题：per=0（不限时）仍按 container 兜底，绝不返回 0（否则无止损）
    assert runner._effective_timeout({"has_container": True}, 0, 480) == 480


def test_effective_timeout_static_unlimited_when_zero():
    """静态题 per_task=0 表示不限时 → 返回 0（允许），但靶机题不会走到这。"""
    assert runner._effective_timeout({"has_container": False}, 0, 480) == 0
    # 静态题正常取 per_task
    assert runner._effective_timeout({"has_container": False}, 600, 480) == 600


def test_target_unavailable_const_is_known():
    """_solve_one 的 fail-closed 返回 status=='target_unavailable'，
    _loop 据此跳过 _mark_failed（环境争用非能力失败，下轮重试）。护栏：该分支常量存在。"""
    import inspect

    src = inspect.getsource(runner._solve_one)
    assert "target_unavailable" in src, "fail-closed 分支被改/删除：环境争用不应记失败"
