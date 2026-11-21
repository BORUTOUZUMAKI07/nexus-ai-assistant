"""The CI secret tripwire must not skip a whole file for a harmless word.

``FAKE_PATTERNS`` used to suppress every match in a file as soon as the word
"example" appeared anywhere in it. Files such as ``backend/.env.example`` were
therefore never scanned at all. Suppression belongs to the individual match,
not to the file.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "check_secrets.py"
_spec = importlib.util.spec_from_file_location("check_secrets", _MODULE_PATH)
check_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_secrets)

_REAL_AWS_KEY = "AKIA" + "A" * 16
_REAL_GH_KEY = "ghp_" + "a" * 36


def _scan_files(tmp_path, monkeypatch, files: dict[str, str]):
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        check_secrets.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="\n".join(files) + "\n"),
    )
    return check_secrets.scan()


def test_scan_catches_a_real_key_in_a_file_that_mentions_example(tmp_path, monkeypatch, capsys):
    """RED: an unrelated 'example' comment must not hide a genuine key."""
    result = _scan_files(
        tmp_path,
        monkeypatch,
        {
            "settings.py": (
                "# example values are documented elsewhere\n"
                f"AWS_KEY = '{_REAL_AWS_KEY}'\n"
            )
        },
    )

    assert result == 1
    assert "settings.py: AWS Access Key ID" in capsys.readouterr().err


def test_scan_still_allows_the_documented_fake_aws_key(tmp_path, monkeypatch, capsys):
    """A well-known dummy key must stay allowed (the marker is in the token)."""
    result = _scan_files(
        tmp_path, monkeypatch, {"sample.py": "KEY = 'AKIAIOSFODNN7EXAMPLE'\n"}
    )

    assert result == 0
    assert "SECRET SCAN FAILED" not in capsys.readouterr().err


def test_scan_allows_a_key_annotated_as_fake_on_the_same_line(tmp_path, monkeypatch, capsys):
    """An inline 'example' annotation is still a deliberate fake."""
    result = _scan_files(
        tmp_path,
        monkeypatch,
        {"sample.py": f"KEY = '{_REAL_GH_KEY}'  # example token\n"},
    )

    assert result == 0
    assert "SECRET SCAN FAILED" not in capsys.readouterr().err


def test_scan_reports_only_the_real_key_when_one_file_has_both_kinds(
    tmp_path, monkeypatch, capsys
):
    result = _scan_files(
        tmp_path,
        monkeypatch,
        {
            "fake.py": "KEY = 'AKIAIOSFODNN7EXAMPLE'  # dummy\n",
            "real.py": f"# just an example app\nKEY = '{_REAL_GH_KEY}'\n",
        },
    )

    assert result == 1
    errors = capsys.readouterr().err
    assert "real.py: GitHub Personal Access Token" in errors
    assert "fake.py" not in errors


@pytest.mark.parametrize(
    ("secret", "name"),
    [
        (_REAL_AWS_KEY, "AWS Access Key ID"),
        (_REAL_GH_KEY, "GitHub Personal Access Token"),
        ("github_pat_" + "a" * 82, "GitHub Fine-Grained PAT"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "Private key"),
        ("sk-" + "a" * 48, "OpenAI API Key"),
    ],
)
def test_every_secret_pattern_is_detected_next_to_an_example_comment(
    tmp_path, monkeypatch, capsys, secret, name
):
    result = _scan_files(
        tmp_path, monkeypatch, {"config.py": f"# example config\nKEY = '{secret}'\n"}
    )

    assert result == 1
    assert f"config.py: {name}" in capsys.readouterr().err
