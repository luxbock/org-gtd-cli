"""Shared pytest/Hypothesis configuration (issue #45).

Two Hypothesis profiles (§45 design point 3):

- ``fast`` — the default; keeps the whole default `pytest` run quick.
- ``thorough`` — opt-in deep run: ``ORG_GTD_TEST_PROFILE=thorough pytest``.

The example database is committed at ``.hypothesis-examples/`` so every
checkout (and the sandboxed `nix flake check`) replays known-interesting
cases first, deterministically.
"""

import os
from pathlib import Path

from hypothesis import HealthCheck, settings
from hypothesis.database import DirectoryBasedExampleDatabase

_DB = DirectoryBasedExampleDatabase(
    str(Path(__file__).parent / ".hypothesis-examples"))

settings.register_profile(
    "fast",
    database=_DB,
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "thorough",
    database=_DB,
    max_examples=1000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

PROFILE = os.environ.get("ORG_GTD_TEST_PROFILE", "fast")
settings.load_profile(PROFILE)


def tier2_max_examples():
    """Bounded conformance budget: modest by default (the wall-clock gate
    is the arbiter), deep under the thorough profile."""
    return 100 if PROFILE == "thorough" else 12
