"""Runtimes for the tests: each test that needs one is skipped without it."""

import os
import shutil

import pytest


def is_installed(program):
    """Whether `program` is on PATH, and a `node` is not Bun under that name."""
    found = shutil.which(program)
    if not found:
        return False
    return program == "bun" or os.path.basename(os.path.realpath(found)) != "bun"


def _needs(program):
    if not is_installed(program):
        pytest.skip(f"{program} is not on PATH")
    return program


@pytest.fixture(params=["node", "bun"])
def runtime(request):
    """Each runtime in turn."""
    return _needs(request.param)


@pytest.fixture
def node():
    return _needs("node")


@pytest.fixture
def bun():
    return _needs("bun")
