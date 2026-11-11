"""Guards that keep version, Python and translation declarations in sync."""

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "fermax_blue"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
PYTHON_FLOOR = PYPROJECT["project"]["requires-python"].removeprefix(">=")


def _keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        keys.add(prefix + key)
        if isinstance(value, dict):
            keys |= _keys(value, f"{prefix}{key}.")
    return keys


def test_manifest_is_the_only_place_with_the_version() -> None:
    """A second copy in pyproject.toml sat at 0.9.0 for seventeen releases."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+(-beta\.\d+)?", manifest["version"])
    assert "version" not in PYPROJECT["project"]
    assert PYPROJECT["project"]["dynamic"] == ["version"]


def test_python_floor_is_the_same_everywhere() -> None:
    ruff = tomllib.loads((ROOT / "ruff.toml").read_text())
    assert ruff["target-version"] == "py" + PYTHON_FLOOR.replace(".", "")
    assert PYPROJECT["tool"]["mypy"]["python_version"] == PYTHON_FLOOR
    assert f"ARG PYTHON_VERSION={PYTHON_FLOOR}\n" in (ROOT / "Dockerfile.dev").read_text()


def test_readme_states_the_hacs_minimum() -> None:
    year, month, _ = json.loads((ROOT / "hacs.json").read_text())["homeassistant"].split(".")
    assert f"Home Assistant **{year}.{month} or newer**" in (ROOT / "README.md").read_text()


@pytest.mark.parametrize(
    "locale", sorted(p.stem for p in (COMPONENT / "translations").glob("*.json"))
)
def test_translation_keys_match_strings(locale: str) -> None:
    source = _keys(json.loads((COMPONENT / "strings.json").read_text()))
    target = _keys(json.loads((COMPONENT / "translations" / f"{locale}.json").read_text()))
    assert source == target
