"""
Unit tests for ε-greedy bandit exploration (pure + FakeSession integration).
"""
import uuid

import pytest
from backend.app.domain.experiment.models import BanditReward
from backend.app.services.bandit_service import BanditService
from backend.tests.fakes import FakeSession


class StubRNG:
    """Fully controllable stand-in for random.Random."""

    def __init__(self, random_value: float, choice_value: str):
        self._random_value = random_value
        self._choice_value = choice_value

    def random(self) -> float:
        return self._random_value

    def choice(self, seq):
        return self._choice_value


def _bandit(epsilon: float = 0.1, rng=None) -> BanditService:
    return BanditService(epsilon=epsilon, rng=rng or StubRNG(0.5, ""))


def test_greedy_choice_exploits_best_arm():
    svc = _bandit(rng=StubRNG(random_value=0.5, choice_value=""))
    assert svc.greedy_choice({"a": 0.9, "b": 0.2}) == "a"


def test_greedy_choice_explores_with_probability_epsilon():
    svc = _bandit(rng=StubRNG(random_value=0.0, choice_value="b"))
    assert svc.greedy_choice({"a": 0.9, "b": 0.2}) == "b"


def test_greedy_choice_empty_means():
    assert _bandit().greedy_choice({}) == ""


def _bandit_definition(variants, bandit=True):
    return {"variants": variants, "bandit": bandit}


def _monkeypatch_definition(monkeypatch, definition):
    class FakeExperimentService:
        def definition(self, experiment: str):
            return definition

    monkeypatch.setattr(
        "backend.app.services.bandit_service.experiment_service",
        FakeExperimentService(),
    )


@pytest.mark.asyncio
async def test_select_is_noop_unless_bandit_mode_enabled(monkeypatch):
    _monkeypatch_definition(monkeypatch, _bandit_definition(["control", "canary"], bandit=False))
    fake = FakeSession()
    svc = _bandit()
    assert await svc.select(fake, "chat_system_prompt") == ""


@pytest.mark.asyncio
async def test_select_cold_start_explores_uniformly(monkeypatch):
    _monkeypatch_definition(monkeypatch, _bandit_definition(["a", "b"]))
    fake = FakeSession()
    svc = _bandit(rng=StubRNG(random_value=0.5, choice_value="b"))
    assert await svc.select(fake, "chat_system_prompt") == "b"


@pytest.mark.asyncio
async def test_select_exploits_best_variant_from_reward_stream(monkeypatch):
    _monkeypatch_definition(monkeypatch, _bandit_definition(["a", "b"]))
    fake = FakeSession()
    fake.seed(
        BanditReward,
        [
            BanditReward(experiment_key="chat_system_prompt", variant="a", reward=1.0),
            BanditReward(experiment_key="chat_system_prompt", variant="a", reward=1.0),
            BanditReward(experiment_key="chat_system_prompt", variant="b", reward=0.0),
        ],
    )
    svc = _bandit(rng=StubRNG(random_value=0.5, choice_value=""))
    assert await svc.select(fake, "chat_system_prompt") == "a"


@pytest.mark.asyncio
async def test_record_reward_persists_and_ignores_default():
    fake = FakeSession()
    svc = _bandit()
    rec = await svc.record_reward(fake, "chat_system_prompt", "canary_v1", 1.0, user_id=uuid.uuid4())
    assert rec is not None
    assert len(fake.rows[BanditReward]) == 1
    assert await svc.record_reward(fake, "chat_system_prompt", "default", 1.0) is None


@pytest.mark.asyncio
async def test_stats_aggregate_empirical_win_rates():
    fake = FakeSession()
    fake.seed(
        BanditReward,
        [
            BanditReward(experiment_key="chat_system_prompt", variant="a", reward=1.0),
            BanditReward(experiment_key="chat_system_prompt", variant="a", reward=0.0),
            BanditReward(experiment_key="chat_system_prompt", variant="b", reward=1.0),
            BanditReward(experiment_key="researcher", variant="x", reward=1.0),
        ],
    )
    svc = _bandit()
    stats = await svc.stats(fake)
    by_variant = {(s["experiment"], s["variant"]): s for s in stats}
    assert by_variant[("chat_system_prompt", "a")]["mean_reward"] == pytest.approx(0.5)
    assert by_variant[("chat_system_prompt", "a")]["reward_count"] == 2
    assert by_variant[("researcher", "x")]["mean_reward"] == pytest.approx(1.0)

    filtered = await svc.stats(fake, experiment="chat_system_prompt")
    assert {s["variant"] for s in filtered} == {"a", "b"}
