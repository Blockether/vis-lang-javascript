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

from vis_lang_javascript import envfile, repl, runner, runtimes
from vis_lang_javascript import nest as nest_app

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
        if answer.get("source"):
            detail.append(answer["source"])
    setup = answer.get("setup") or {}
    if is_running and setup.get("kind") == "nest":
        detail.append(
            f"Nest {setup.get('module')}{' initialized' if setup.get('init') else ''},"
            f" {setup.get('bound')} names bound"
        )
        if setup.get("skipped"):
            detail.append("not bound over globals: " + ", ".join(setup["skipped"]))
    detail += answer.get("notes") or []
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
        nest: Annotated[
            str,
            "Built NestJS entry file to load the app from, relative to cwd, or"
            " auto for the one nest-cli.json names; empty for a plain REPL.",
        ] = "",
        init: Annotated[
            bool,
            "Run the Nest app's init: lifecycle hooks, and with them its queues,"
            " schedules and connections.",
        ] = False,
        env_file: Annotated[
            str, "Dotenv file, relative to cwd, whose variables the REPL gets."
        ] = "",
    ) -> ReplSession:
        """Start a persistent REPL for this directory, or keep the live one.

        "auto" runs Bun when the project uses it (bun.lock, bun.lockb or
        "packageManager": "bun@..."), else Node. It needs Node 22.18 or newer
        from nvm or PATH, or Bun 1.4.5 or newer on PATH. A .nvmrc or .node-version
        file selects an installed Node from nvm. require and import() resolve
        from the directory.
        A live REPL is never replaced, because its state is the work: "auto"
        keeps it, and a live REPL on another named runtime is refused. It gets
        the variables that a `before` op hook on `js.repl_start` adds with
        `vis.with_env`, over those of env_file; NODE_OPTIONS and BUN_OPTIONS
        from the file are skipped. A live REPL with other variables is refused.

        With nest, the REPL loads the built app (build it first) up to its
        NestFactory call and makes an application context from that root
        module. It does not listen, and without init no lifecycle hook runs.
        Nest's REPL functions are bound (get, $, resolve, select, debug,
        methods, help), every provider, controller and module class by name, and
        svc(name), services(regex), load(path in dist), app and checkpoint().
        svc refuses a request-scoped or transient provider: use await resolve(X).
        A `.vis/js-repl.cjs` in cwd or a parent can export before(ctx) and
        bindings(ctx). An interrupted eval fails at its next fetch, HTTP, pg or
        ioredis call. Raises ToolMissing when the runtime is not installed or
        is too old, and RuntimeError when it fails its startup handshake.
        """
        directory = self._absolute(cwd)
        options = {"cwd": str(directory), "runtime": runtime}
        notes = []
        env = {}
        if env_file:
            env, skipped = envfile.read(directory / env_file)
            notes += [f"{name} from {env_file} skipped" for name in skipped]
        env.update(call_env())
        if nest:
            entry = nest_app.entry(directory, nest)
            options["profile"] = {"kind": "nest", "entry": entry, "init": bool(init)}
            if nest_app.stale(entry, directory):
                notes.append(
                    "the build is older than its sources: rebuild for the newest code"
                )
        if env:
            options["env"] = env
            options["env_fingerprint"] = env_fingerprint(env)
        answer = repl.start(options)
        if answer.get("result") == "failed":
            raise RuntimeError(answer.get("message") or "The REPL did not start")
        return _session(dict(answer, notes=[*(answer.get("notes") or []), *notes]))

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
