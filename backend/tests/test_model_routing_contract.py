"""
Contract tests for the LiteLLM model routing tables.

Why this file exists
--------------------
``litellm_client.py`` carries three tables that have to agree with each other
and with the router's own fallback declarations:

  * ``model_list``            -- what the router will actually call
  * ``_GROUP_TO_MODEL``       -- group -> model, used for token/cost attribution
  * ``_MODEL_GROUP_ALIASES``  -- a model name a caller passed -> group

Nothing enforced that agreement, and the tables had already drifted once: after
the free-tier remap, ``qwen3.8-27b`` was deployed as ``fast_chat`` while the
alias map still pointed it at ``complex_reasoning``, which serves a *different*
model. Every such call would have run one model and had its tokens and cost
recorded against another, and nothing in the test suite could see it. That is the
same class of defect as the repository-filter operator gap in the D3 tests -- a
table that is right in isolation and wrong in composition.

The checks below are about agreement between tables, so they are cheap and need
no network, no API key, and no database.
"""

from backend.app.infrastructure.ai import litellm_client as lc

GROUPS = [entry["model_name"] for entry in lc.model_list]


def _model_of(group: str) -> str:
    for entry in lc.model_list:
        if entry["model_name"] == group:
            return entry["litellm_params"]["model"]
    raise AssertionError(f"{group} is not in model_list")


# ── model_list itself ────────────────────────────────────────────────────────


def test_every_group_is_unique():
    """A duplicate model_name makes the router's fallback map ambiguous."""
    duplicates = sorted({g for g in GROUPS if GROUPS.count(g) > 1})
    assert duplicates == [], f"duplicate model_name entries: {duplicates}"


def test_every_group_declares_a_model_and_a_key():
    for entry in lc.model_list:
        params = entry["litellm_params"]
        assert params.get("model"), f"{entry['model_name']} has no model"
        # None is the dangerous value: LiteLLM does not reject it at construction,
        # it fails at call time with a provider auth error, which the fallback
        # chain then turns into a slower but still-working request. So the check
        # is that the key is *plumbed*, not that it is set in this environment.
        assert "api_key" in params, f"{entry['model_name']} never sets api_key"


def test_groups_that_share_a_model_are_declared_not_accidental():
    """A shared slug is allowed, but only if the code says so on purpose.

    `large_context` deliberately reuses `fast_chat`'s model (see the comment on
    the entry). This test exists so that adding a *second* silent repeat -- which
    is what turns one provider outage into a total outage -- has to be
    acknowledged here first.
    """
    documented = {("fast_chat", "large_context")}
    seen: dict[str, list[str]] = {}
    for group in GROUPS:
        seen.setdefault(_model_of(group), []).append(group)
    for model, groups in seen.items():
        if len(groups) < 2:
            continue
        for pair in zip(sorted(groups), sorted(groups)[1:]):
            assert (pair[0], pair[1]) in documented or (pair[1], pair[0]) in documented, (
                f"{model} is now shared by {groups} but that pairing is not "
                f"documented; add it to `documented` only if it is deliberate"
            )


# ── _GROUP_TO_MODEL vs model_list ────────────────────────────────────────────


def test_group_to_model_covers_exactly_the_deployed_groups():
    assert set(lc._GROUP_TO_MODEL) == set(GROUPS), (
        "_GROUP_TO_MODEL and model_list disagree; a group missing from one is "
        "either unroutable or unattributable"
    )


def test_group_to_model_names_the_model_that_group_actually_calls():
    """The drift this file exists for.

    `_GROUP_TO_MODEL` is what token and cost maths reads. If it disagrees with
    `model_list`, usage is attributed to a model that was never called -- the
    live-evals cost table and the spend meter both trust it.
    """
    for group, model in lc._GROUP_TO_MODEL.items():
        actual = _model_of(group)
        assert model == actual, (
            f"_GROUP_TO_MODEL[{group!r}] says {model!r} but model_list runs {actual!r}"
        )


def test_resolve_provider_model_agrees_with_the_table():
    for group in GROUPS:
        assert lc.resolve_provider_model(group) == lc._GROUP_TO_MODEL[group]


# ── aliases ──────────────────────────────────────────────────────────────────


def test_every_alias_points_at_a_real_group():
    for alias, group in lc._MODEL_GROUP_ALIASES.items():
        assert group in GROUPS, (
            f"alias {alias!r} -> {group!r}, which is not a deployed model group"
        )


def test_current_aliases_resolve_to_the_group_running_that_model():
    """A current alias is a promise; a legacy one is a courtesy.

    `_CURRENT_MODEL_ALIASES` claims these names are the models we deploy, so the
    group they resolve to has to be a group that serves them. This is the check
    that fails for `qwen3.8-27b -> complex_reasoning`.
    """
    for alias, group in lc._CURRENT_MODEL_ALIASES.items():
        served = _model_of(group)
        # Compare on the last path segment so `groq/qwen/qwen3.8-27b` and
        # `qwen3.8-27b` both match a served model of `groq/qwen/qwen3.8-27b`.
        bare = alias.split("/", 1)[-1]
        assert bare in served.split("/", 1)[-1], (
            f"{alias!r} is declared as a currently-deployed model but resolves to "
            f"{group!r}, which serves {served!r}"
        )


def test_current_and_legacy_aliases_do_not_overlap():
    overlap = sorted(set(lc._CURRENT_MODEL_ALIASES) & set(lc._LEGACY_MODEL_ALIASES))
    assert overlap == [], (
        f"{overlap} are in both tables; the current one wins, so the legacy entry "
        f"is dead and one of the two claims is wrong"
    )


def test_every_deployed_model_is_reachable_by_name():
    """A model the router calls should be addressable without knowing the group.

    Without this, a caller can only reach a deployed model by knowing the
    internal group name, which is the thing the aliases exist to hide.
    """
    for group in GROUPS:
        model = _model_of(group)
        bare = model.split("/", 1)[-1]
        assert (
            bare in lc._MODEL_GROUP_ALIASES or model in lc._MODEL_GROUP_ALIASES
        ), f"deployed model {model!r} has no alias"


def test_config_defaults_all_resolve_to_a_deployed_group():
    """A default that resolves to nothing falls through the router unrecognised.

    `FAST_MODEL` and `DEFAULT_MODEL` in core/config.py still carry Groq slugs
    from before the remap, so they land in the legacy table. That is the intended
    behaviour, but it is only safe while the legacy table keeps covering them --
    and a renamed model in config.py would otherwise look like a working
    default right up until a request was made.
    """
    from backend.app.core.config import settings

    for setting_name in ("DEFAULT_MODEL", "FAST_MODEL", "MEMORY_EXTRACTION_MODEL"):
        value = getattr(settings, setting_name, None)
        if not value:
            continue
        assert lc.resolve_model_group(value) in GROUPS, (
            f"{setting_name}={value!r} does not resolve to a deployed group"
        )
