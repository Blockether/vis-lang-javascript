"""The managed Node REPL keeps its state and never leaves a child behind."""

import pytest

from vis_lang_javascript import repl
from vis_lang_javascript.repl import ReplError
from vis_lang_javascript.tools import JavaScriptTools


@pytest.fixture
def project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
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


def test_output_and_errors_come_back(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    printed = tools.repl_eval("console.log('hello'); console.error('warn')", cwd=cwd)
    assert printed.output == "hello\nwarn\n"
    failed = tools.repl_eval("throw new TypeError('boom')", cwd=cwd)
    assert failed.error.startswith("TypeError: boom")
    assert "driver.mjs" not in failed.error


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
    assert tools.repl_start(cwd=cwd).detail == "already running"
    assert tools.repl_eval("kept", cwd=cwd).value == "7"


def test_a_new_python_process_attaches_to_the_kept_repl(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    tools.repl_eval("const kept = 9", cwd=cwd)
    # A sandbox restart ends the Python process; the runtime stays.
    repl.detach_all()
    assert tools.repl_status(cwd=cwd).detail == "running"
    assert tools.repl_eval("kept", cwd=cwd).value == "9"


def test_stop_ends_the_repl(project):
    tools, cwd = project
    tools.repl_start(cwd=cwd)
    assert tools.repl_stop(cwd=cwd).detail == "stopped"
    assert tools.repl_status(cwd=cwd).is_running is False
    with pytest.raises(ReplError, match="not up"):
        tools.repl_eval("1", cwd=cwd)
