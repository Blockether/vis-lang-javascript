"""The runtime a project uses comes from its lockfile or package manager."""

import pytest

from vis_lang_javascript import runtimes


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({}, "node"),
        ({"bun.lock": "{}"}, "bun"),
        ({"bun.lockb": ""}, "bun"),
        ({"package.json": '{"packageManager": "bun@1.4.5"}'}, "bun"),
        (
            {"package.json": '{"packageManager": "pnpm@10.0.0"}', "bun.lock": "{}"},
            "bun",
        ),
        ({"package.json": '{"packageManager": "pnpm@10.0.0"}'}, "node"),
        ({"package-lock.json": "{}"}, "node"),
    ],
)
def test_the_project_declares_its_runtime(tmp_path, files, expected):
    (tmp_path / ".git").mkdir()
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    assert runtimes.detected(tmp_path) == expected


def test_a_workspace_package_uses_the_lockfile_of_its_root(tmp_path):
    (tmp_path / "bun.lock").write_text("{}")
    package = tmp_path / "packages" / "a"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"name": "a"}')
    assert runtimes.detected(package) == "bun"


def test_the_walk_ends_at_the_repository(tmp_path):
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert runtimes.detected(tmp_path / "repo") == "node"


def test_a_runtime_that_is_not_installed_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(Exception, match="bun is not on PATH") as caught:
        runtimes.choose(tmp_path, "bun")
    assert type(caught.value).__name__ == "ToolMissing"
