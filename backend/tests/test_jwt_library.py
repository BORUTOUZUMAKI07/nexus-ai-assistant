"""Strix finding #1 -- the JWT layer must not be built on python-jose.

python-jose 3.5.0 is the terminal release: PYSEC-2026-4298 (algorithm
confusion through a DER-encoded public key) has no patched 3.x, and the package
drags in ecdsa, which carries PYSEC-2026-1325 (Minerva timing attack on P-256)
with no patched release either. PyJWT covers exactly the surface this app uses
-- HS256 signing and verification, ``JWT_ALGORITHM`` in ``config.py`` -- so the
remediation is a library swap, not a behaviour change:

* ``pyproject.toml`` declares ``pyjwt`` and no longer declares ``python-jose``
  (revert ``S1/R1`` in ``revert_strix.py``);
* ``core/security.py`` imports PyJWT and aliases its base exception to
  ``JWTError``, the only name this module's callers know;
* the one ``decode`` call still pins ``algorithms=[_algorithm()]`` (revert
  ``S1/R2``), which is the property PYSEC-2026-4298 is actually about -- an
  unpinned verifier is how an attacker swaps the algorithm out from under it.

``backend.app.core.security`` is imported *inside* each test body rather than
at module scope: ``tests/conftest.py`` imports it at module scope, so reverting
the import line would abort collection for the whole suite and leave the
harness unable to tell "the fix is load-bearing" from "pytest never ran". The
dependency declaration and the algorithm pin are therefore the two revert
surfaces, and the identity/behaviour assertions below are the controls keeping
the code half honest.
"""

import base64
import json
import re
import time
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _declared_dependencies() -> list[str]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def _requirement_names() -> set[str]:
    """Normalised distribution names of every direct dependency."""
    names: set[str] = set()
    for dep in _declared_dependencies():
        requirement = dep.split("#", 1)[0].split(";", 1)[0]
        name = re.split(r"(===|==|>=|<=|~=|!=|\[|<|>)", requirement, maxsplit=1)[0]
        names.add(name.strip().lower())
    return names


def test_pyjwt_is_declared_and_python_jose_is_not():
    """Load-bearing: revert S1/R1 puts python-jose back on the dependency list."""
    names = _requirement_names()
    assert "pyjwt" in names, (
        "PyJWT must be a direct dependency -- core/security.py imports it, and a "
        "security-critical import that only resolves through a transitive edge "
        "disappears the moment the package that carried it moves."
    )
    assert "python-jose" not in names, (
        "python-jose 3.5.0 is terminal and carries PYSEC-2026-4298 with no patched "
        "release; PyJWT is its maintained replacement."
    )


def test_the_jwt_layer_runs_on_pyjwt_not_python_jose():
    """Control: the module really binds PyJWT's top-level ``jwt``."""
    import jwt as pyjwt
    from backend.app.core import security

    # ``jose.jwt`` is a submodule of a *different* package, so identity -- not
    # duck-typing, which both libraries satisfy -- is what separates them.
    assert security.jwt is pyjwt
    assert security.JWTError is pyjwt.PyJWTError


def test_access_and_refresh_tokens_round_trip_through_the_migrated_layer():
    """Load-bearing: revert S1/R2 (the decode algorithm pin) breaks this.

    PyJWT refuses to decode a signed token without an explicit ``algorithms``
    list, so a decode that lost its pin does not silently accept everything --
    it stops accepting anything, which is exactly what this test notices.
    """
    from backend.app.core.security import (
        create_access_token,
        create_refresh_token,
        decode_token,
    )

    subject = "11111111-1111-1111-1111-111111111111"
    access = decode_token(create_access_token(subject, role="admin"))
    assert access["sub"] == subject
    assert access["type"] == "access"
    assert access["role"] == "admin"

    refresh = decode_token(create_refresh_token(subject, family_id="fam-1"))
    assert refresh["type"] == "refresh"
    assert refresh["fam"] == "fam-1"
    assert refresh["jti"]


def test_a_token_signed_with_an_unconfigured_algorithm_is_refused():
    """Algorithm pinning rejects a well-formed token built with the wrong alg."""
    import jwt as pyjwt
    from backend.app.core import security
    from backend.app.core.exceptions import InvalidTokenError

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": "access", "exp": now + 300},
        "a" * 64,
        algorithm="HS512",
    )
    with pytest.raises(InvalidTokenError):
        security.decode_token(token)


def test_an_alg_none_token_is_refused():
    """The textbook algorithm-confusion token never verifies."""
    from backend.app.core import security
    from backend.app.core.exceptions import InvalidTokenError

    def _segment(payload: dict) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    token = ".".join(
        [
            _segment({"alg": "none", "typ": "JWT"}),
            _segment(
                {
                    "sub": "11111111-1111-1111-1111-111111111111",
                    "type": "access",
                    "exp": int(time.time()) + 300,
                }
            ),
            "",
        ]
    )
    with pytest.raises(InvalidTokenError):
        security.decode_token(token)


def test_a_tampered_signature_is_refused():
    from backend.app.core.exceptions import InvalidTokenError
    from backend.app.core.security import create_access_token, decode_token

    token = create_access_token("11111111-1111-1111-1111-111111111111")
    header, payload, signature = token.split(".")
    tampered = ".".join([header, payload, ("B" if signature[0] != "B" else "A") + signature[1:]])
    with pytest.raises(InvalidTokenError):
        decode_token(tampered)
