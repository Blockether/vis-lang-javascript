"""The runtime a project uses comes from its lockfile or package manager."""

import runpy
from pathlib import Path

import blockether.vis.extension as vis
import pytest
from vis_lang_interface import ToolMissing

from vis_lang_javascript import runner, runtimes


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


def fake_node(bin_dir, version, body=""):
    bin_dir.mkdir(parents=True, exist_ok=True)
    node = bin_dir / "node"
    node.write_text(
        f'#!/bin/sh\nif [ "$1" = --version ]; then echo {version}; exit; fi\n' + body
    )
    node.chmod(0o755)
    return node


@pytest.fixture
def nvm(tmp_path, monkeypatch):
    home = tmp_path / "home with spaces"
    monkeypatch.setenv("NVM_DIR", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    for version in ("v22.17.0", "v24.9.0", "v24.11.0", "v24.11.2", "v26.1.0"):
        fake_node(home / "versions" / "node" / version / "bin", version)
    for name, spec in (
        ("default", "24"),
        ("team", "lts/*"),
        ("lts/*", "lts/krypton"),
        ("lts/krypton", "v24.11.0"),
        ("loop", "loop2"),
        ("loop2", "loop"),
    ):
        path = home / "alias" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec + "\n")
    return home


@pytest.mark.parametrize(
    ("spec", "version"),
    [
        ("v24.11.0", "v24.11.0"),
        ("24.11.0", "v24.11.0"),
        ("24", "v24.11.2"),
        ("24.11", "v24.11.2"),
        ("node", "v26.1.0"),
        ("stable", "v26.1.0"),
        ("lts/*", "v24.11.0"),
        ("lts/krypton", "v24.11.0"),
        ("team", "v24.11.0"),
    ],
)
def test_nvmrc_resolves_installed_nodes(tmp_path, nvm, spec, version):
    fake_node(tmp_path / "path", "v27.1.0")
    (tmp_path / ".nvmrc").write_text(f"# project Node\n\n  {spec} # use this\n")
    child = tmp_path / "package"
    child.mkdir()
    (child / ".node-version").write_text("26\n")
    chosen = runtimes.choose(child)
    assert chosen.binary == str(nvm / "versions" / "node" / version / "bin" / "node")
    assert chosen.version == version
    assert chosen.source == ".nvmrc"


def test_nearest_pin_and_git_boundary(tmp_path, nvm):
    (tmp_path / ".nvmrc").write_text("26\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").touch()
    (repo / ".node-version").write_text("24.9\n")
    child = repo / "package"
    child.mkdir()
    assert runtimes.choose(child).version == "v24.9.0"
    assert runtimes.choose(child).source == ".node-version"
    (repo / ".nvmrc").write_text("26\n")
    (child / ".nvmrc").write_text("24\n")
    assert runtimes.choose(child).version == "v24.11.2"


@pytest.mark.parametrize("spec", ["25", "loop", "lts/no-such-name", "../outside"])
def test_a_bad_pin_does_not_fall_back_to_path(tmp_path, nvm, spec):
    fake_node(tmp_path / "path", "v27.1.0")
    pin = tmp_path / ".nvmrc"
    pin.write_text(spec + "\n")
    with pytest.raises(ToolMissing) as caught:
        runtimes.choose(tmp_path)
    assert all(word in str(caught.value) for word in (str(pin), spec, "nvm install"))


def test_path_wins_without_a_pin_and_default_alias_is_the_fallback(tmp_path, nvm):
    chosen = runtimes.choose(tmp_path)
    assert chosen.source == "nvm default"
    assert chosen.version == "v24.11.2"
    fake_node(tmp_path / "path", "v27.1.0")
    chosen = runtimes.choose(tmp_path)
    assert chosen.source == "PATH"
    assert chosen.version == "v27.1.0"


def test_nvm_dir_defaults_to_the_users_home(tmp_path, nvm, monkeypatch):
    nvm.rename(tmp_path / ".nvm")
    monkeypatch.delenv("NVM_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert runtimes.choose(tmp_path).version == "v24.11.2"


def test_the_chosen_node_must_meet_the_minimum(tmp_path, nvm):
    fake_node(tmp_path / "path", "v27.1.0")
    (tmp_path / ".nvmrc").write_text("22.17\n")
    with pytest.raises(ToolMissing, match=r"v22\.17\.0.*22\.18"):
        runner.run([], root=tmp_path)


@pytest.mark.parametrize("name", ["node", "jest", "vitest"])
def test_test_tools_and_their_children_use_the_pinned_node(tmp_path, nvm, name):
    (tmp_path / ".nvmrc").write_text("24.11.0\n")
    fake_node(tmp_path / "path", "v27.1.0", "exit 42\n")
    bin_dir = nvm / "versions" / "node" / "v24.11.0" / "bin"
    fake_node(bin_dir, "v24.11.0", 'echo "runner=$0"; echo "child=$(node --version)"\n')
    if name != "node":
        (tmp_path / "package.json").write_text(f'{{"dependencies": {{"{name}": "*"}}}}')
        binary = tmp_path / "node_modules" / ".bin" / name
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/usr/bin/env node\n")
        binary.chmod(0o755)
    answer = runner.run([], root=tmp_path)
    assert f"runner={bin_dir / 'node'}" in answer.output
    assert "child=v24.11.0" in answer.output


def test_extension_grants_the_nvm_installations(nvm, monkeypatch):
    [entrypoint] = Path(__file__).resolve().parents[1].glob("extension.py")
    monkeypatch.setattr(vis, "register_extension", lambda extension: None)
    grants = runpy.run_path(str(entrypoint))["GRANTS"]
    assert nvm / "versions" / "node" in {Path(grant.path) for grant in grants}
