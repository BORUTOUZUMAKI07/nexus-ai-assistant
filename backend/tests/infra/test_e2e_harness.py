"""
The e2e harness must talk to *this* app.

Both failures below happened for real, and neither produced a red test — they
produced a green-looking run that measured nothing:

1. Next 16 silently moves to a random port when 3000 is busy, so Playwright
   polled the configured url until the 120s timeout and reported a broken run.
2. `reuseExistingServer: true` locally means any listener on 3000 is adopted.
   This machine runs other projects on that port, so a run drove another app's
   UI and would have reported its result as Nexus's.
"""
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "frontend" / "playwright.config.ts"
E2E_DIR = CONFIG.parent / "e2e"


def _config() -> str:
    assert CONFIG.exists(), f"playwright config missing: {CONFIG}"
    return CONFIG.read_text(encoding="utf-8")


def test_dev_server_port_is_pinned_on_the_command_line():
    """`-p 3000` is what makes a busy port fail fast instead of drifting."""
    config = _config()
    assert "-p 3000" in config, (
        "pin the port: `npx next dev -p 3000`. Without it Next 16 picks another "
        "port when 3000 is busy and the run times out instead of failing clearly"
    )
    assert 'url: "http://localhost:3000"' in config, (
        "the readiness url must be the same port the server is pinned to"
    )
    assert 'baseURL: "http://localhost:3000"' in config, (
        "tests must navigate to the pinned port, not a stale one"
    )


def test_an_existing_server_is_never_adopted():
    """A foreign app on :3000 must fail the run, not be tested as ours."""
    config = _config()
    assert "reuseExistingServer: false" in config, (
        "reuseExistingServer must be false: adopting whatever listens on 3000 "
        "means a run can drive another project's UI and report it as Nexus"
    )


def test_e2e_specs_exist_so_the_harness_is_not_vacuous():
    specs = sorted(E2E_DIR.glob("*.spec.ts"))
    assert specs, f"no specs in {E2E_DIR} — `make e2e` would pass by running nothing"
