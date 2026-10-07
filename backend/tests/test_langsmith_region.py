"""Which LangSmith deployment is this project pointed at, and would the SDK guess right?

The failure this guards, measured 2026-10-05: tracing was enabled, the run
produced all ten of its spans, and every ingest POST returned 403. The cause was
not a bad credential. LangSmith is four independent deployments (GCP US / GCP
EU / GCP APAC / AWS US), each with its own accounts and its own Supabase auth;
the key was minted on AWS US and the SDK silently defaulted to `api.smith.
langchain.com`, which has never heard of it.

That produced two distinct mistakes, and only the first is a config bug:

  * the app sent traces somewhere that would reject them, and
  * the diagnosis blamed the credential and told the operator to issue a new
    `lsv2_sk_` service key, which would have 403'd identically.

So the first test below is the regression guard for the missing setting. The
second exists because the thing that was checked *instead* — "`/info` answers
200, therefore the key works" — is false on all four hosts, and a reader of the
old note would repeat the original mistake.

The endpoints are not asserted against a hardcoded table from a doc page: they
are read out of the installed `langsmith` package, which is what actually
decides the default. A hardcoded copy is a second source of truth that drifts
the moment LangChain renames a host, and a test asserting a stale constant is
worse than no test.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ENV_EXAMPLE = BACKEND / ".env.example"
CONFIG = BACKEND / "app" / "core" / "config.py"

# Where the SDK's own default lives, read from the installed package rather than
# from a constant written by hand. See the module docstring.
#
# It is `langsmith/utils.py`, not `client.py`: `client.py` only *validates* that
# LANGSMITH_ENDPOINT and LANGSMITH_RUNS_ENDPOINTS are not both set. The default
# itself comes from `get_env_var("ENDPOINT", default="https://api.smith.
# langchain.com")`. The first version of this helper grepped client.py for an
# `or "<host>"` chain that does not exist there, and failed against a perfectly
# good install - the regex was written from memory rather than read off the file.
LANGSMITH_UTILS = BACKEND / ".venv" / "Lib" / "site-packages" / "langsmith" / "utils.py"


def _sdk_default_host() -> str:
    """The URL the SDK falls back to when LANGSMITH_ENDPOINT is unset.

    Read with `ast` rather than a regex: the literal is a keyword argument to a
    call, and locating it by shape means a reformatted line cannot silently turn
    the lookup into a pattern that matches nothing (which is how the client.py
    version of this helper failed).

    Returns the full URL, scheme included - that is what the SDK stores and
    what a doc line has to reproduce exactly, so the value is not trimmed here.
    """
    if not LANGSMITH_UTILS.exists():
        pytest.skip("langsmith/utils.py not found in the venv at the expected path")
    tree = ast.parse(LANGSMITH_UTILS.read_text(encoding="utf-8"))
    hosts = [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "ENDPOINT"
        for kw in node.keywords
        if kw.arg == "default" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
    ]
    assert len(hosts) == 1, (
        "Expected exactly one get_env_var('ENDPOINT', default=...) call in "
        f"langsmith/utils.py, found {len(hosts)}. This test verifies the "
        "library's own behaviour, so if the call moved the test must be "
        "updated deliberately - not deleted."
    )
    return hosts[0]


def _settings_field_names() -> set[str]:
    """Field names declared on core.config.Settings, via ast (CRLF-safe)."""
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("Settings class not found in core/config.py")


def _example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


def test_the_sdk_default_host_is_the_gcp_us_deployment() -> None:
    """Baseline for the claim above: the unset-endpoint default is GCP US.

    If this ever stops being true, the "wrong region" story needs rewriting
    before the tests below mean anything. Asserted against the bare host as
    well as the URL, because a default that silently grew a non-https scheme or
    a regional prefix would break every host comparison in the file.
    """
    assert _sdk_default_host() == "https://api.smith.langchain.com"
    assert urlsplit(_sdk_default_host()).hostname == "api.smith.langchain.com"
    assert urlsplit(_sdk_default_host()).scheme == "https"


def test_langsmith_endpoint_is_declared_in_settings() -> None:
    """The setting exists in core.config.Settings.

    Declared so it is documented and type-checked alongside the other three
    LANGSMITH_* fields. The field is read by the SDK, not by this codebase -
    load_dotenv() at config.py import puts it in os.environ - so a test
    asserting "something references settings.LANGSMITH_ENDPOINT" would be
    asserting something untrue. What must hold is that the surface is declared.
    """
    assert "LANGSMITH_ENDPOINT" in _settings_field_names(), (
        "LANGSMITH_ENDPOINT must be declared on Settings. Without it the "
        "variable is undocumented and untype-checked, and a fresh clone has "
        "nothing telling it that the SDK default is only correct for GCP US."
    )


def test_env_example_ships_the_endpoint_variable() -> None:
    """A fresh clone has to be able to discover the region trap.

    This is the regression guard for the actual outage: `.env.example` listed
    API_KEY, TRACING and PROJECT, so an operator copying it produced a config
    that could not work on any non-GCP-US deployment, with nothing to tell them
    why.
    """
    values = _example_values()
    assert "LANGSMITH_ENDPOINT" in values, (
        ".env.example must ship LANGSMITH_ENDPOINT. Without it, copying the "
        "example to .env yields a configuration that 403s on every ingest "
        "unless the account happens to be on the SDK's default region."
    )


def test_env_example_names_the_sdk_default_region_as_a_trap() -> None:
    """The example must say which host is the SDK's default, and why it bites.

    A bare `LANGSMITH_ENDPOINT=` teaches nothing: the operator still does not
    know that leaving it empty is the dangerous state. The comment has to name
    api.smith.langchain.com explicitly so the string a reader greps for is the
    one the SDK actually picks.
    """
    default_host = _sdk_default_host()
    assert default_host in ENV_EXAMPLE.read_text(encoding="utf-8"), (
        f".env.example must name {default_host} as the SDK's default endpoint. "
        "It is the host that silently accepts traces nowhere; a reader who does "
        "not know it is the default cannot recognise the config that caused the "
        "403."
    )


def test_env_example_documented_host_is_an_api_host() -> None:
    """If .env.example proposes a LANGSMITH_ENDPOINT value, it must be an api host.

    The UI host is the trap inside this trap. `aws.smith.langchain.com` looks
    exactly like the right answer - it is the URL a human types to look at
    traces - and it is not an ingest endpoint. Shipping it as a copyable value
    produces a configuration that fails differently and more confusingly than
    the original 403.

    Scoped to the value line and to `docs/`, because the region tables elsewhere
    legitimately name UI hosts in the "UI" column. Asserting the shape ("api.
    in, smith. out") rather than a fixed list, so the rule survives LangChain
    renaming a host.
    """
    offenders: list[str] = []

    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith("LANGSMITH_ENDPOINT=") and line.strip() != "LANGSMITH_ENDPOINT=":
            offenders.extend(_ui_hosts_in(line))

    for path in (REPO / "docs").rglob("*.md"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "LANGSMITH_ENDPOINT=" in line and "LANGSMITH_ENDPOINT`" not in line:
                offenders.extend(_ui_hosts_in(line))

    assert not offenders, (
        "A line that assigns LANGSMITH_ENDPOINT names a UI host rather than an "
        f"API host: {offenders}"
    )


def _ui_hosts_in(line: str) -> list[str]:
    """smith.*.langchain.com hosts in a line, i.e. UI hosts, not api.* ones."""
    return [
        host
        for host in re.findall(r"https://([A-Za-z0-9.-]+)", line)
        if host.endswith(".smith.langchain.com") and "api.smith" not in host
    ]
