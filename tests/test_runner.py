"""Test runs count what the runner reported, with each failure located."""

import json

import pytest

from vis_lang_javascript import runner
from vis_lang_javascript.tools import JavaScriptTools

NODE_TESTS = """import { describe, it, test } from 'node:test';
import assert from 'node:assert';
describe('math', () => {
  it('adds', () => assert.equal(1 + 1, 2));
  it('fails', () => {
    assert.equal(1 + 1, 3);
  });
  it.skip('later', () => {});
});
test('top', () => {});
"""


BUN_TESTS = """import { describe, it, test, expect } from 'bun:test';
describe('math', () => {
  it('adds', () => expect(1 + 1).toBe(2));
  it('fails', () => {
    expect(1 + 1).toBe(3);
  });
  it.skip('later', () => {});
  it.todo('soon');
});
test('top', () => {});
"""


def test_node_test_failures_carry_their_suite_file_and_line(tmp_path, node):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "math.test.ts").write_text(NODE_TESTS)
    result = JavaScriptTools().run_tests(["test"], cwd=str(tmp_path))
    assert (result.total, result.passed, result.failed, result.skipped) == (4, 2, 1, 1)
    assert result.is_passed is False
    (failure,) = result.failures
    assert (failure.test, failure.path, failure.line) == (
        "math › fails",
        "test/math.test.ts",
        6,
    )
    assert failure.message.startswith("2 == 3")


def test_node_test_keyword_selects_tests(tmp_path, node):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
    (tmp_path / "a.test.mjs").write_text(NODE_TESTS)
    result = JavaScriptTools().run_tests(cwd=str(tmp_path), keyword="adds")
    assert result.is_passed is True
    assert result.passed == 1


def test_the_declared_runner_wins(tmp_path, node):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "30", "vitest": "3"}})
    )
    assert runner.runner(tmp_path) == "vitest"
    (tmp_path / "package.json").write_text('{"devDependencies": {"jest": "30"}}')
    assert runner.runner(tmp_path) == "jest"
    (tmp_path / "package.json").write_text("{}")
    assert runner.runner(tmp_path) == "node"


def test_a_declared_runner_wins_over_bun(tmp_path, bun):
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "3"}}')
    assert runner.runner(tmp_path) == "vitest"
    (tmp_path / "package.json").write_text("{}")
    assert runner.runner(tmp_path) == "bun"
    assert runner.runner(tmp_path, "node") == "node"


def _bun_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "math.test.ts").write_text(BUN_TESTS)


def test_bun_test_failures_carry_their_suite_file_and_line(tmp_path, bun):
    _bun_project(tmp_path)
    result = JavaScriptTools().run_tests(["test"], cwd=str(tmp_path))
    assert (result.total, result.passed, result.failed, result.skipped) == (5, 2, 1, 2)
    (failure,) = result.failures
    assert (failure.test, failure.path, failure.line) == (
        "math › fails",
        "test/math.test.ts",
        5,
    )
    assert failure.message.startswith("expect(received).toBe(expected)")


def test_bun_test_keyword_selects_tests(tmp_path, bun):
    _bun_project(tmp_path)
    result = JavaScriptTools().run_tests(cwd=str(tmp_path), keyword="adds")
    assert (result.is_passed, result.passed) == (True, 1)


def test_bun_test_counts_a_file_that_failed_to_load(tmp_path, bun):
    _bun_project(tmp_path)
    (tmp_path / "broken.test.ts").write_text("import './gone';\n")
    result = JavaScriptTools().run_tests(cwd=str(tmp_path))
    assert (result.total, result.failed) == (6, 2)
    broken = next(f for f in result.failures if f.path == "broken.test.ts")
    assert "Cannot find module './gone'" in broken.message
    alone = JavaScriptTools().run_tests(["broken.test.ts"], cwd=str(tmp_path))
    assert (alone.total, alone.failed, alone.failures[0].test) == (
        1,
        1,
        "broken.test.ts",
    )


@pytest.mark.parametrize("prefix", ["", "::group::"])
def test_a_file_that_bun_could_not_load_is_read_from_its_output(tmp_path, prefix):
    output = (
        f"bun test v1.4.5\n\n{prefix}test/broken.test.ts:\n\n"
        "# Unhandled error between tests\n-------------------------------\n"
        "error: Cannot find module './gone'\n-------------------------------\n"
    )
    (failure,) = runner._unloaded(output, tmp_path)
    assert (failure.test, failure.path, failure.line, failure.message) == (
        "broken.test.ts",
        "test/broken.test.ts",
        0,
        "error: Cannot find module './gone'",
    )


def test_runtime_node_runs_node_test_in_a_bun_project(tmp_path, node):
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "a.test.mjs").write_text(NODE_TESTS)
    result = JavaScriptTools().run_tests(cwd=str(tmp_path), runtime="node")
    assert (result.total, result.failed) == (4, 1)


def test_an_unknown_runtime_is_refused(tmp_path):
    with pytest.raises(ValueError, match="runtime must be one of"):
        JavaScriptTools().run_tests(cwd=str(tmp_path), runtime="deno")


@pytest.mark.parametrize("max_failures", [1, -1, True, 1.5])
def test_node_refuses_an_unsupported_failure_limit(tmp_path, monkeypatch, max_failures):
    def started(*args, **kwargs):
        pytest.fail("The runner must refuse the option before starting a child.")

    monkeypatch.setattr(runner.runtimes, "choose", started)
    with pytest.raises(ValueError, match="max_failures"):
        JavaScriptTools().run_tests(
            cwd=str(tmp_path), runtime="node", max_failures=max_failures
        )


@pytest.mark.parametrize("name", ["jest", "vitest"])
@pytest.mark.parametrize("runtime", ["bun", "deno"])
def test_declared_runner_refuses_runtime_it_cannot_use(tmp_path, name, runtime):
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {name: "1"}}))
    with pytest.raises(ValueError, match="runtime"):
        JavaScriptTools().run_tests(cwd=str(tmp_path), runtime=runtime)


def test_a_json_report_counts_cases_and_a_file_that_failed_to_load(tmp_path):
    report = {
        "numTotalTests": 3,
        "numPassedTests": 1,
        "numFailedTests": 1,
        "numPendingTests": 1,
        "numTodoTests": 0,
        "testResults": [
            {
                "name": str(tmp_path / "src" / "a.spec.ts"),
                "status": "failed",
                "message": "",
                "assertionResults": [
                    {
                        "ancestorTitles": ["A"],
                        "title": "ok",
                        "status": "passed",
                        "failureMessages": [],
                    },
                    {
                        "ancestorTitles": ["A", "b"],
                        "title": "no",
                        "status": "failed",
                        "failureMessages": ["\x1b[31mexpected 1 to be 2\x1b[39m"],
                        "location": {"line": 12, "column": 5},
                    },
                    {
                        "ancestorTitles": [],
                        "title": "later",
                        "status": "pending",
                        "failureMessages": [],
                    },
                ],
            },
            {
                "name": str(tmp_path / "src" / "b.spec.ts"),
                "status": "failed",
                "message": "Cannot find module './gone' from src/b.spec.ts",
                "assertionResults": [],
            },
        ],
    }
    result = runner.json_result(report, tmp_path)
    assert (result.total, result.passed, result.failed, result.skipped) == (4, 1, 2, 1)
    first, second = result.failures
    assert (first.test, first.path, first.line, first.message) == (
        "A › b › no",
        "src/a.spec.ts",
        12,
        "expected 1 to be 2",
    )
    assert (second.test, second.path) == ("b.spec.ts", "src/b.spec.ts")
    assert "Cannot find module" in second.message


def test_a_declared_runner_that_is_not_installed_is_named(tmp_path):
    (tmp_path / "package.json").write_text('{"devDependencies": {"jest": "30"}}')
    try:
        JavaScriptTools().run_tests(cwd=str(tmp_path))
    except Exception as ex:
        assert type(ex).__name__ == "ToolMissing"
        assert "jest is declared" in str(ex)
    else:
        raise AssertionError("expected ToolMissing")
