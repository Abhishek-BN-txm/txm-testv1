"""Smoke tests: environment and package are wired correctly."""

import json
import sys
from pathlib import Path


def test_python_version():
    assert sys.version_info >= (3, 11), "Python 3.11+ required"


def test_package_imports():
    import tara_automation

    assert tara_automation.__version__


def test_machine_module():
    from tara_automation.machine import EXAMPLE_FILE, REPO_ROOT, load_machine

    assert (REPO_ROOT / "pyproject.toml").exists(), "repo root resolution broken"
    assert EXAMPLE_FILE.exists(), "settings/machine.example.json missing"
    # Example file is valid JSON with the required key
    raw = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
    assert "tara_exe" in raw
    # Loading the example works end-to-end
    m = load_machine(EXAMPLE_FILE)
    assert isinstance(m.tara_exe, Path)


def test_core_dependencies_import():
    import numpy  # noqa: F401
    import openpyxl  # noqa: F401
    import pandas  # noqa: F401
    import pydantic  # noqa: F401
