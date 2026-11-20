"""Test runs: node:test, Jest and Vitest, counted from the report each one writes.

node:test writes JUnit XML. Jest and Vitest write the same JSON report shape.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from vis_lang_interface import TestFailure, TestResult, ToolMissing, process

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
OUTPUT_TAIL = 20000
NODE_TEST_FILES = "**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts}"


def runner(root):
    """The runner the project declares: vitest, then jest, else node:test."""
    try:
        manifest = json.loads((Path(root) / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        manifest = {}
    declared = {
        **(manifest.get("devDependencies") or {}),
        **(manifest.get("dependencies") or {}),
    }
    for name in ("vitest", "jest"):
        if name in declared:
            return name
    return "node"


def binary(root, name):
    """The project's own `node_modules/.bin/<name>`, the nearest one upwards."""
    for directory in (Path(root), *Path(root).parents):
        found = directory / "node_modules" / ".bin" / name
        if found.exists():
            return str(found)
    raise ToolMissing(
        f"{name} is declared in package.json but not installed; install the"
        " project's dependencies, for example with npm install or pnpm install."
    )


def _node_paths(root, paths):
    """node:test reads a directory as a file, so a directory becomes a glob."""
    named = []
    for path in paths:
        if (Path(root) / path).is_dir():
            named.append(f"{str(path).rstrip('/')}/{NODE_TEST_FILES}")
        else:
            named.append(str(path))
    return named


def command(name, root, paths, report, *, keyword="", max_failures=0):
    """The argv of one run that writes its report to `report`."""
    if name == "node":
        argv = [
            "node",
            "--test",
            "--test-reporter=spec",
            "--test-reporter-destination=stdout",
            "--test-reporter=junit",
            f"--test-reporter-destination={report}",
        ]
        if keyword:
            argv.append(f"--test-name-pattern={keyword}")
        return argv + _node_paths(root, paths)
    if name == "jest":
        argv = [binary(root, "jest"), "--ci", "--json", f"--outputFile={report}"]
        argv.append("--testLocationInResults")
        if max_failures:
            argv.append(f"--bail={max_failures}")
    else:
        argv = [binary(root, "vitest"), "run", "--reporter=default"]
        argv += ["--reporter=json", f"--outputFile.json={report}"]
        if max_failures:
            argv.append(f"--bail={max_failures}")
    if keyword:
        argv += ["-t", keyword]
    return argv + [str(path) for path in paths]


def _relative(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(path or "")


def _line_in(text, path):
    """The first line of `path` that a stack trace names, or 0."""
    name = re.escape(Path(path).name) if path else None
    if not name:
        return 0
    found = re.search(rf"{name}:(\d+):\d+", text or "")
    return int(found.group(1)) if found else 0


STACK_FRAME = re.compile(r"(?:file://)?(/[^\s:()'\"]+):(\d+):\d+")


def _stack_location(text):
    """The file and line of the first stack frame outside node_modules."""
    for found in STACK_FRAME.finditer(text or ""):
        if "/node_modules/" not in found.group(1):
            return found.group(1), int(found.group(2))
    return "", 0


def junit_result(xml, root, output="", duration_ms=0):
    """A node:test JUnit report as a TestResult."""
    total = passed = failed = skipped = 0
    failures = []

    def walk(node, titles):
        nonlocal total, passed, failed, skipped
        for child in node:
            if child.tag == "testsuite":
                walk(child, [*titles, child.get("name", "")])
            elif child.tag == "testcase":
                total += 1
                problem = child.find("failure")
                if problem is None:
                    problem = child.find("error")
                if child.find("skipped") is not None:
                    skipped += 1
                elif problem is not None:
                    failed += 1
                    body = ANSI.sub("", problem.text or "").strip()
                    reason = problem.get("message") or body.split("\n")[0]
                    # Node before 24 names no file on a test case.
                    path = child.get("file", "")
                    line = int(child.get("line") or 0) or _line_in(body, path)
                    if not path:
                        path, line = _stack_location(body)
                    failures.append(
                        TestFailure(
                            " › ".join([*titles, child.get("name", "")]),
                            _relative(path, root),
                            line,
                            reason + ("\n" + body if body and body != reason else ""),
                        )
                    )
                else:
                    passed += 1

    walk(ElementTree.fromstring(xml), [])
    return TestResult.of(
        "javascript",
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        duration_ms=duration_ms,
        failures=failures,
        output=output,
    )


def json_result(report, root, output="", duration_ms=0):
    """A Jest or Vitest JSON report as a TestResult. A file that fails before
    its tests run, for example on an import error, counts as one failure."""
    failures = []
    broken = 0
    for suite in report.get("testResults") or ():
        path = suite.get("name", "")
        cases = suite.get("assertionResults") or ()
        for case in cases:
            if case.get("status") != "failed":
                continue
            text = ANSI.sub("", "\n".join(case.get("failureMessages") or ())).strip()
            line = (case.get("location") or {}).get("line") or _line_in(text, path)
            failures.append(
                TestFailure(
                    " › ".join(
                        [*(case.get("ancestorTitles") or ()), case.get("title", "")]
                    ),
                    _relative(path, root),
                    int(line or 0),
                    text,
                )
            )
        message = ANSI.sub("", suite.get("message") or "").strip()
        if (
            suite.get("status") == "failed"
            and message
            and not any(case.get("status") == "failed" for case in cases)
        ):
            broken += 1
            failures.append(
                TestFailure(
                    Path(path).name,
                    _relative(path, root),
                    _line_in(message, path),
                    message,
                )
            )
    skipped = int(report.get("numPendingTests") or 0) + int(
        report.get("numTodoTests") or 0
    )
    return TestResult.of(
        "javascript",
        total=int(report.get("numTotalTests") or 0) + broken,
        passed=int(report.get("numPassedTests") or 0),
        failed=int(report.get("numFailedTests") or 0) + broken,
        skipped=skipped,
        duration_ms=duration_ms,
        failures=failures,
        output=output,
    )


def run(paths, *, root, keyword="", max_failures=0, timeout_s=900):
    """Run the project's test runner in `root` and count its report."""
    name = runner(root)
    work = process.run_directory("javascript-tests")
    try:
        report = work / ("report.xml" if name == "node" else "report.json")
        argv = command(
            name, root, paths, report, keyword=keyword, max_failures=max_failures
        )
        started = time.monotonic()
        ran = process.run(argv, cwd=root, timeout_s=timeout_s, read_write=[work])
        duration_ms = round((time.monotonic() - started) * 1000)
        output = ANSI.sub("", (ran.out or "") + (ran.err or ""))[-OUTPUT_TAIL:]
        if not report.is_file():
            return TestResult.of(
                "javascript",
                total=0,
                passed=0,
                failed=1,
                skipped=0,
                duration_ms=duration_ms,
                failures=[
                    TestFailure(
                        f"{name} run",
                        "",
                        0,
                        f"{name} exited with status {ran.exit_code} and wrote no"
                        " report.\n" + output[-4000:],
                    )
                ],
                output=output,
            )
        text = report.read_text("utf-8")
        if name == "node":
            return junit_result(text, root, output, duration_ms)
        return json_result(json.loads(text), root, output, duration_ms)
    finally:
        shutil.rmtree(work, ignore_errors=True)
