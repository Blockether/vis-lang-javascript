"""The managed REPL keeps its state and never leaves a child behind, on Node and Bun."""

import os
import shlex
import shutil

import pytest
from test_runtimes import fake_node

from vis_lang_javascript import repl, runtimes
from vis_lang_javascript.repl import ReplError
from vis_lang_javascript.tools import JavaScriptTools


@pytest.fixture
def project(tmp_path, runtime):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
    if runtime == "bun":
        # "auto" picks Bun for a project with a Bun lockfile.
        (tmp_path / "bun.lock").write_text("{}")
    tools = JavaScriptTools()
    yield tools, str(tmp_path)
    tools.repl_stop(cwd=str(tmp_path))


def test_status_is_down_before_a_start(project):
    tools, cwd = project
    assert tools.repl_status(cwd=cwd).detail == "not running"


def test_declarations_survive_between_evaluations(project):
    tools, cwd = project
    assert tools.repl_start(cwd=cwd).is_running is True
    tools.repl_eval("const kept = 41", cwd=cwd)
    answer = tools.repl_eval("kept + 1", cwd=cwd)
    assert (answer.value, answer.error) == ("42", "")


def test_typescript_runs_after_its_types_are_stripped(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const n: number = await Promise.resolve(3)", cwd=cwd)
    assert tools.repl_eval("enum E { A, B }\nE.B + n", cwd=cwd).value == "4"


def test_project_modules_load_from_the_directory(project, tmp_path):
    tools, cwd = project
    (tmp_path / "lib.ts").write_text(
        "export const twice = (n: number): number => n * 2;\n"
    )
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const { twice } = await import('./lib.ts')", cwd=cwd)
    assert tools.repl_eval("twice(21)", cwd=cwd).value == "42"


def test_the_project_runtime_starts(project, runtime):
    tools, cwd = project
    detail = tools.repl_start(cwd=cwd).detail
    assert detail.startswith(f"started · {runtime.capitalize()} ")
    assert tools.repl_status(cwd=cwd).detail.startswith(
        f"running · {runtime.capitalize()} "
    )


def test_output_and_errors_come_back(project, runtime):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    printed = tools.repl_eval("console.log('hello'); console.error('warn')", cwd=cwd)
    assert printed.output == "hello\nwarn\n"
    failed = tools.repl_eval("throw new TypeError('boom')", cwd=cwd)
    # Bun's positions are in the transpiled code, so they are left out.
    where = "REPL:1:7" if runtime == "node" else "REPL"
    assert failed.error == f"TypeError: boom\n    at {where}"


def test_an_error_keeps_the_frames_of_the_code_and_drops_the_driver(
    project, tmp_path, runtime
):
    tools, cwd = project
    (tmp_path / "lib.mjs").write_text(
        "export const fail = () => [1].map(() => { throw new Error('deep') });\n"
    )
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const { fail } = await import('./lib.mjs')", cwd=cwd)
    frames = tools.repl_eval("fail()", cwd=cwd).error.splitlines()
    assert frames[0] == "Error: deep"
    assert "lib.mjs:1:" in frames[1]
    if runtime == "node":
        assert "Array.map (<anonymous>)" in frames[2]
        assert frames[-1] == "    at REPL:1:1"
    else:
        assert frames[2] == "    at map (native:1:11)"
        assert frames[-1] == "    at REPL"


def test_code_that_does_not_parse_runs_none_of_its_statements(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    refused = tools.repl_eval("globalThis.ran = true\nx = = 2", cwd=cwd)
    assert refused.error.endswith("The code was not evaluated.")
    assert tools.repl_eval("typeof ran", cwd=cwd).value == "'undefined'"


def test_a_timeout_interrupts_a_busy_loop_and_keeps_the_state(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const kept = 7", cwd=cwd)
    with pytest.raises(ReplError, match="interrupted. The REPL answers again"):
        tools.repl_eval("for (;;) {}", cwd=cwd, timeout_ms=500)
    assert tools.repl_eval("kept", cwd=cwd).value == "7"


def test_a_timeout_abandons_a_promise_that_never_settles(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    with pytest.raises(ReplError, match="interrupted. The REPL answers again"):
        tools.repl_eval("await new Promise(() => {})", cwd=cwd, timeout_ms=500)
    assert tools.repl_eval("1 + 1", cwd=cwd).value == "2"


def test_a_second_start_keeps_the_live_repl(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const kept = 7", cwd=cwd)
    assert tools.repl_start(cwd=cwd).detail.startswith("already running · ")
    assert tools.repl_eval("kept", cwd=cwd).value == "7"


def test_a_new_python_process_attaches_to_the_kept_repl(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const kept = 9", cwd=cwd)
    # A sandbox restart ends the Python process; the runtime stays.
    repl.detach_all()
    assert tools.repl_status(cwd=cwd).detail.startswith("running · ")
    assert tools.repl_eval("kept", cwd=cwd).value == "9"


def test_stop_ends_the_repl(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    assert tools.repl_stop(cwd=cwd).detail == "stopped"
    assert tools.repl_status(cwd=cwd).is_running is False
    with pytest.raises(ReplError, match="No REPL is up"):
        tools.repl_eval("1", cwd=cwd)


def test_a_live_repl_on_another_named_runtime_is_refused(tmp_path, node, bun):
    tools, cwd = JavaScriptTools(), str(tmp_path)
    try:
        tools.repl_start(cwd=cwd, runtime="bun")
        tools.repl_eval("const kept = 1", cwd=cwd)
        with pytest.raises(RuntimeError, match="already runs on Bun"):
            tools.repl_start(cwd=cwd, runtime="node")
        assert tools.repl_start(cwd=cwd).detail.startswith("already running · Bun")
        assert tools.repl_eval("kept", cwd=cwd).value == "1"
    finally:
        tools.repl_stop(cwd=cwd)


def test_bun_runs_a_static_import_and_a_declaration_again(project, runtime, tmp_path):
    if runtime != "bun":
        pytest.skip("Node refuses a static import")
    tools, cwd = project
    (tmp_path / "lib.ts").write_text("export const twice = (n: number) => n * 2;\n")
    tools.repl_start(cwd=cwd)
    tools.repl_eval("import { twice } from './lib.ts'", cwd=cwd)
    tools.repl_eval("const n = 1", cwd=cwd)
    tools.repl_eval("const n = 20", cwd=cwd)
    assert (
        tools.repl_eval("twice(n) + require('node:path').sep.length", cwd=cwd).value
        == "41"
    )


def test_an_env_file_reaches_the_repl(project, tmp_path):
    tools, cwd = project
    (tmp_path / ".env").write_text("UNNAMED=1\n")
    (tmp_path / "vars.env").write_text(
        "GREETING=hello\nNODE_OPTIONS=--require ./x.cjs\nBUN_OPTIONS=--preload ./x.cjs\n"
    )
    detail = tools.repl_start(cwd=cwd, env_file="vars.env").detail
    assert "NODE_OPTIONS from vars.env skipped" in detail
    assert "BUN_OPTIONS from vars.env skipped" in detail
    answer = tools.repl_eval(
        "[process.env.GREETING, process.env.NODE_OPTIONS, process.env.BUN_OPTIONS,"
        " process.env.UNNAMED]",
        cwd=cwd,
    )
    assert answer.value == "[ 'hello', undefined, undefined, undefined ]"


@pytest.mark.parametrize("legacy", [False, True])
def test_pinned_and_legacy_node_repls_keep_their_state_after_reattach(
    tmp_path, node, monkeypatch, legacy
):
    # A wrapper acts like one nvm install, and forwards driver calls to real Node.
    installed = shutil.which(node)
    current = runtimes.choose(tmp_path, "node").version
    home = tmp_path / "nvm install"
    bins = home / "versions" / "node"
    selected = fake_node(
        bins / current / "bin", current, f'exec {shlex.quote(installed)} "$@"\n'
    )
    other = "v99.1.0"
    fake_node(bins / other / "bin", other)
    monkeypatch.setenv("NVM_DIR", str(home))
    cwd = str(tmp_path)
    pin = tmp_path / ".nvmrc"
    tools = JavaScriptTools()
    try:
        if legacy:
            # v0.2.1 started bare `node` and left no source metadata.
            with monkeypatch.context() as old:
                old.setattr(runtimes, "choose", lambda *_args: runtimes.NODE)
                assert tools.repl_start(cwd=cwd).is_running
        else:
            pin.write_text(current + "\n")
            started = tools.repl_start(cwd=cwd)
            assert f"Node {current} · .nvmrc" in started.detail
            assert started.command[0] == str(selected)
            value = tools.repl_eval(
                f"process.env.PATH.split({os.pathsep!r})[0]", cwd=cwd
            ).value
            assert str(selected.parent) in value
        tools.repl_eval("const stored = 17", cwd=cwd)
        repl.detach_all()
        status = tools.repl_status(cwd=cwd)
        assert f"Node {current}" in status.detail
        if not legacy:
            assert "· .nvmrc" in status.detail
        pin.write_text(other + "\n")
        changed = tools.repl_status(cwd=cwd)
        assert (
            f"Keeping the live REPL. A new REPL would use Node {other}"
            in changed.detail
        )
        assert f"already running · Node {current}" in tools.repl_start(cwd=cwd).detail
        assert tools.repl_eval("stored", cwd=cwd).value == "17"
        pin.write_text("91\n")
        assert "nvm install" in tools.repl_start(cwd=cwd).detail
        assert tools.repl_eval("stored", cwd=cwd).value == "17"
    finally:
        tools.repl_stop(cwd=cwd)
