"""
Unit tests for deterministic canary/shadow experiment bucketing.
"""
from backend.app.services.experiments import (
    ExperimentService,
    bucketed_variant,
    variant_for,
)


def test_bucket_is_stable_and_in_range():
    key = "user-42"
    first = bucketed_variant(key)
    second = bucketed_variant(key)
    assert first == second
    assert 0 <= first < 100


def test_different_keys_spread_across_buckets():
    buckets = {bucketed_variant(f"user-{i}") for i in range(200)}
    assert len(buckets) > 50  # good spread across 100 buckets


def test_variant_for_respects_weights_deterministically():
    variant_a = variant_for("user-1", ["control", "canary"], weights=[90, 10])
    assert variant_a == variant_for("user-1", ["control", "canary"], weights=[90, 10])
    assert variant_a in ("control", "canary")


def test_variant_for_empty_variants_defaults():
    assert variant_for("any", []) == "default"


def test_variant_for_full_weight_gives_single_variant():
    for i in range(50):
        assert variant_for(f"key-{i}", ["only"], weights=[1]) == "only"


def test_experiment_service_unconfigured_defaults():
    service = ExperimentService(config={})
    assert service.variant_for_user("user-1", "chat_system_prompt") == "default"
    assert service.list_experiments() == []


def test_experiment_service_configured_variant():
    config = {
        "experiments": {
            "chat_system_prompt": {
                "variants": ["control", "canary_balanced"],
                "weights": [90, 10],
                "default": "control",
            }
        }
    }
    service = ExperimentService(config=config)
    variant = service.variant_for_user("stable-user-id", "chat_system_prompt")
    assert variant in ("control", "canary_balanced")
    # Deterministic: same user → same variant across calls.
    assert service.variant_for_user("stable-user-id", "chat_system_prompt") == variant
    # Distribution sanity over many users: both variants appear.
    seen = {service.variant_for_user(f"user-{i}", "chat_system_prompt") for i in range(400)}
    assert seen == {"control", "canary_balanced"}
