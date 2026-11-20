"""The NestJS profile of the REPL, on the app in tests/fixtures/nest-app."""

import os
import subprocess
from pathlib import Path

import pytest
from conftest import is_installed

from vis_lang_javascript import nest, repl
from vis_lang_javascript.repl import ReplError
from vis_lang_javascript.tools import JavaScriptTools

APP = Path(__file__).parent / "fixtures" / "nest-app"


@pytest.fixture(scope="session")
def built():
    """The fixture app, compiled to dist."""
    if not is_installed("node"):
        pytest.skip("node is not on PATH")
    if not (APP / "node_modules").is_dir():
        pytest.skip(f"run `npm ci` in {APP}")
    tsc = APP / "node_modules" / "typescript" / "bin" / "tsc"
    subprocess.run(["node", str(tsc), "-p", "tsconfig.json"], cwd=APP, check=True)
    return str(APP)


@pytest.fixture
def app(built, runtime):
    tools = JavaScriptTools()
    yield tools, built, runtime
    tools.repl_stop(cwd=built)


def test_the_app_is_bound_without_running_its_hooks(app):
    tools, cwd, runtime = app
    detail = tools.repl_start(cwd=cwd, runtime=runtime, nest="auto").detail
    assert "Nest AppModule, " in detail and "initialized" not in detail
    assert tools.repl_eval("svc('GreeterService').greet('REPL')", cwd=cwd).value == (
        "'Hello, REPL!'"
    )
    assert tools.repl_eval("get(GreeterService).initialized", cwd=cwd).value == "false"
    assert tools.repl_eval("await app.init()", cwd=cwd).value == (
        "NestApplicationContext <AppModule>"
    )
    assert tools.repl_eval("get(GreeterService).initialized", cwd=cwd).value == "true"


def test_nest_functions_and_our_own_are_bound(app):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime, nest="dist/main.js")
    names = tools.repl_eval("services(/Service$/)", cwd=cwd).value
    assert "'GreeterService'" in names and "'PollerService'" in names
    methods = tools.repl_eval("methods(GreeterService)", cwd=cwd)
    assert "greet" in methods.output
    loaded = tools.repl_eval(
        "load('./greeter.service').GreeterService === GreeterService", cwd=cwd
    )
    assert loaded.value == "true"


@pytest.mark.parametrize(
    "name, scope, field",
    [("RequestContext", "request-scoped", "id"), ("Stamp", "transient", "at")],
)
def test_svc_refuses_a_scoped_provider(app, name, scope, field):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")
    failed = tools.repl_eval(f"svc('{name}')", cwd=cwd)
    assert f"{scope}; use await resolve({name})" in failed.error
    found = tools.repl_eval(f"typeof (await resolve({name})).{field}", cwd=cwd)
    assert found.value == "'number'"


def test_a_new_python_process_keeps_the_app_detail(app):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")
    repl.detach_all()
    assert "Nest AppModule, " in tools.repl_status(cwd=cwd).detail


def test_init_runs_the_lifecycle_hooks(app):
    tools, cwd, runtime = app
    detail = tools.repl_start(cwd=cwd, runtime=runtime, nest="auto", init=True).detail
    assert "Nest AppModule initialized" in detail
    assert tools.repl_eval("get(GreeterService).initialized", cwd=cwd).value == "true"


def test_an_interrupted_poll_fails_at_its_next_fetch(app):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")
    tools.repl_eval(
        "const server = require('node:http').createServer((q, s) => s.end('ok'))\n"
        "await new Promise((r) => server.listen(0, '127.0.0.1', r))\n"
        "const url = `http://127.0.0.1:${server.address().port}`",
        cwd=cwd,
    )
    with pytest.raises(ReplError, match="interrupted at its next I/O call"):
        tools.repl_eval("await svc('PollerService').poll(url)", cwd=cwd, timeout_ms=500)
    # The next eval is not cancelled.
    assert tools.repl_eval("(await fetch(url)).status", cwd=cwd).value == "200"


def test_a_promise_without_io_is_abandoned(app):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")
    with pytest.raises(ReplError, match="was abandoned"):
        tools.repl_eval("await new Promise(() => {})", cwd=cwd, timeout_ms=500)
    assert tools.repl_eval("1 + 1", cwd=cwd).value == "2"


def test_a_live_repl_without_the_app_is_refused(app):
    tools, cwd, runtime = app
    tools.repl_start(cwd=cwd, runtime=runtime)
    with pytest.raises(RuntimeError, match="without a profile"):
        tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")


def test_project_hooks_add_bindings(app, tmp_path):
    tools, cwd, runtime = app
    hooks = Path(cwd) / ".vis" / "js-repl.cjs"
    hooks.parent.mkdir(exist_ok=True)
    hooks.write_text(
        "exports.bindings = ({ app, require }) => {\n"
        "  const { GreeterService } = require('./greeter.service');\n"
        "  return { greet: (name) => app.get(GreeterService).greet(name) };\n"
        "};\n"
    )
    try:
        tools.repl_start(cwd=cwd, runtime=runtime, nest="auto")
        assert tools.repl_eval("greet('hook')", cwd=cwd).value == "'Hello, hook!'"
    finally:
        hooks.unlink()
        hooks.parent.rmdir()


def test_an_unbuilt_app_names_the_build(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "nest-cli.json").write_text('{"entryFile": "main"}')
    with pytest.raises(FileNotFoundError, match="nest build"):
        JavaScriptTools().repl_start(cwd=str(tmp_path), nest="auto")


def test_stale_reads_the_source_root(tmp_path):
    (tmp_path / "nest-cli.json").write_text('{"sourceRoot": "app"}')
    built = tmp_path / "dist" / "main.js"
    built.parent.mkdir()
    built.write_text("")
    (tmp_path / "app").mkdir()
    source = tmp_path / "app" / "main.ts"
    source.write_text("")
    os.utime(built, (0, 0))
    assert nest.stale(built, tmp_path)
