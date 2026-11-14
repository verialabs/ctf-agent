import logging

from backend.cost_tracker import CostTracker


def test_provider_reported_cost_avoids_alias_pricing_warning(caplog):
    tracker = CostTracker()

    with caplog.at_level(logging.WARNING):
        tracker.record_tokens(
            "challenge/opus",
            "opus",
            input_tokens=1_000,
            output_tokens=100,
            provider_spec="claude-sdk",
            cost_usd=0.0,
        )

    tracker.set_reported_cost(
        "challenge/opus",
        0.42,
        "opus",
        provider_spec="claude-sdk",
    )

    assert "Could not calculate cost" not in caplog.text
    assert tracker.total_tokens == 1_100
    assert tracker.total_cost_usd == 0.42


def test_reported_cost_replaces_previous_cumulative_value():
    tracker = CostTracker()

    tracker.set_reported_cost("challenge/opus", 0.25, "opus", "claude-sdk")
    tracker.set_reported_cost("challenge/opus", 0.40, "opus", "claude-sdk")

    assert tracker.total_cost_usd == 0.40
