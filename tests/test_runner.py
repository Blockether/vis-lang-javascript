"""Test runs count what the runner reported, with each failure located."""

import json

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


def test_node_test_failures_carry_their_suite_file_and_line(tmp_path):
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


def test_node_test_keyword_selects_tests(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "p", "type": "module"}')
    (tmp_path / "a.test.mjs").write_text(NODE_TESTS)
    result = JavaScriptTools().run_tests(cwd=str(tmp_path), keyword="adds")
    assert result.is_passed is True
    assert result.passed == 1


def test_the_declared_runner_wins(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "30", "vitest": "3"}})
    )
    assert runner.runner(tmp_path) == "vitest"
    (tmp_path / "package.json").write_text('{"devDependencies": {"jest": "30"}}')
    assert runner.runner(tmp_path) == "jest"
    (tmp_path / "package.json").write_text("{}")
    assert runner.runner(tmp_path) == "node"


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
