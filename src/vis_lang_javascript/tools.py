"""The JavaScript and TypeScript tools this extension exports.

Ordinary Python: every method takes plain arguments and returns a contract
result from `vis_lang_interface`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from vis_lang_interface import (
    ReplResult,
    ReplSession,
    TestResult,
    call_env,
    env_fingerprint,
    project_root,
    string_list,
)

from vis_lang_javascript import repl, runner, runtimes

MARKERS = ("package.json", ".git")


def _session(answer):
    """A `repl` answer as a `ReplSession`, its detail in plain words."""
    is_running = answer.get("status") == "up"
    happened = answer.get("result", "")
    if happened == "status":
        happened = "running" if is_running else "not running"
    detail = [happened.replace("-", " ")]
    if is_running and answer.get("runtime"):
        label = runtimes.label(answer["runtime"])
        detail.append(" ".join(filter(None, [label, answer.get("version")])))
    if answer.get("busy"):
        detail += ["busy", str(answer.get("message") or "")]
    return ReplSession(
        "javascript",
        repl.abbreviate_home(answer["cwd"]),
        answer["cwd"],
        tuple(answer.get("cmd") or ()),
        is_running,
        " · ".join(part for part in detail if part),
    )


class JavaScriptTools:
    """Node.js and Bun tools: test runs and a managed REPL for JavaScript and
    TypeScript."""

    def __init__(self, workspace_root=Path.cwd):
        self._workspace_root = workspace_root

    def _absolute(self, path):
        named = Path(path or ".").expanduser()
        return named if named.is_absolute() else Path(self._workspace_root()) / named

    def _root(self, cwd, paths=()):
        start = cwd or (paths[0] if paths else ".")
        return str(project_root(self._absolute(start), MARKERS))

    def run_tests(
        self,
        paths: Annotated[
            Sequence[str], "Test files or directories; the runner discovers when empty."
        ] = (),
        *,
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
        keyword: Annotated[str, "Run only the tests whose name matches."] = "",
        max_failures: Annotated[
            int,
            "Stop after this many failures (Jest and Vitest --bail); 0 runs every test.",
        ] = 0,
        timeout_s: Annotated[int, "Seconds before the run is abandoned."] = 900,
        runtime: Annotated[
            str, "auto (Bun when the project uses it, else Node), node or bun."
        ] = "auto",
    ) -> TestResult:
        """Run the project's own test runner: Vitest or Jest when package.json
        declares it, else bun test on Bun or node:test on Node.

        A declared runner wins over runtime. Jest and Vitest come from the
        project's node_modules/.bin. Counts and failures come from the report
        the runner writes: JSON for Jest and Vitest, JUnit for bun test and
        node:test. A directory given to node:test runs its *.test.* and *.spec.*
        files. node:test has no failure limit, so it runs every test. Raises
        ToolMissing when a declared runner or the runtime is not installed and
        ToolTimeout when the run outlives timeout_s.
        """
        paths = string_list(paths)
        return runner.run(
            paths,
            root=self._root(cwd, paths),
            keyword=keyword,
            max_failures=max_failures,
            timeout_s=timeout_s,
            runtime=runtime,
        )

    def repl_start(
        self,
        cwd: Annotated[str, "Project directory to run the REPL in."] = "",
        *,
        runtime: Annotated[
            str, "auto (Bun when the project uses it, else Node), node or bun."
        ] = "auto",
    ) -> ReplSession:
        """Start a persistent REPL for this directory, or keep the live one.

        "auto" runs Bun when the project uses it (bun.lock, bun.lockb or
        "packageManager": "bun@..."), else Node. It needs Node 22.18 or Bun
        1.4.5 or newer on PATH. require and import() resolve from the directory.
        A live REPL is never replaced, because its state is the work: "auto"
        keeps it, and a live REPL on another named runtime is refused. It gets
        the variables that a `before` op hook on `js.repl_start` adds with
        `vis.with_env`; a live REPL with other variables is refused. Raises
        ToolMissing when the runtime is not on PATH and RuntimeError when it
        fails its startup handshake.
        """
        options = {"cwd": str(self._absolute(cwd)), "runtime": runtime}
        env = call_env()
        if env:
            options["env"] = env
            options["env_fingerprint"] = env_fingerprint(env)
        answer = repl.start(options)
        if answer.get("result") == "failed":
            raise RuntimeError(answer.get("message") or "The REPL did not start")
        return _session(answer)

    def repl_status(
        self,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
    ) -> ReplSession:
        """Whether this directory has a live REPL, its runtime, and whether it is busy."""
        return _session(repl.status({"cwd": str(self._absolute(cwd))}))

    def repl_stop(
        self,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
    ) -> ReplSession:
        """Stop this directory's REPL. Safe when none is running."""
        return _session(repl.stop({"cwd": str(self._absolute(cwd))}))

    def repl_eval(
        self,
        code: Annotated[str, "JavaScript or TypeScript source to evaluate."],
        *,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
        timeout_ms: Annotated[int, "Milliseconds to wait for the answer."] = 30000,
    ) -> ReplResult:
        """Evaluate code in the live REPL, keeping declarations between calls.

        TypeScript types are stripped before the code runs; enums and namespaces
        are transformed. Top-level await works. Use require() or await import()
        for modules; on Node a static import statement is refused. On Bun a
        static import works, a const can be declared again, and a declaration
        answers its value. The value of the last
        expression comes back as util.inspect shows it, with what the code
        printed. Code that does not parse runs nothing. A timeout interrupts
        synchronous code, or abandons a promise that the code waits on; that
        promise can still run. Start the REPL first: evaluating without one
        raises ReplError, and so does code that does not stop.
        """
        directory = str(self._absolute(cwd))
        answer = repl.evaluate(
            {"code": code, "cwd": directory, "timeout_ms": timeout_ms}
        )
        error = answer.get("exc") or ""
        if answer.get("syntax"):
            error = f"{answer['syntax']}\nThe code was not evaluated."
        return ReplResult(
            "javascript",
            repl.abbreviate_home(str(Path(directory).expanduser().resolve())),
            answer.get("value") or "",
            answer.get("out") or "",
            error,
            int(answer.get("duration_ms") or 0),
            True,
            code=code.rstrip(),
        )
