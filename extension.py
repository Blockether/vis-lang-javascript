"""Vis entrypoint. The tools themselves live in vis_lang_javascript."""

import os
import shutil
from pathlib import Path

import blockether.vis.extension as vis
from vis_lang_interface import presentation, prompt

from vis_lang_javascript.runtimes import nvm_directory
from vis_lang_javascript.tools import JavaScriptTools


def _bind(
    name,
    label,
    build,
    *,
    tag: vis.SymbolTag = "observation",
    show_start=True,
    describe=None,
):
    """Attach one Activity presentation to a method of JavaScriptTools."""
    setattr(
        JavaScriptTools,
        name,
        vis.method(
            tag=tag,
            activity=presentation.activity(
                label, build, show_start=show_start, describe=describe
            ),
        )(getattr(JavaScriptTools, name)),
    )


_bind(
    "run_tests",
    "Run JavaScript tests",
    lambda result: presentation.test_presentation("Run JavaScript tests", result),
    tag="verification",
)
_bind(
    "repl_start",
    "Start JavaScript REPL",
    lambda result: presentation.session_presentation("Start JavaScript REPL", result),
    tag="mutation",
)
_bind(
    "repl_status",
    "Check JavaScript REPL",
    lambda result: presentation.session_presentation("Check JavaScript REPL", result),
    show_start=False,
)
_bind(
    "repl_stop",
    "Stop JavaScript REPL",
    lambda result: presentation.session_presentation("Stop JavaScript REPL", result),
    tag="mutation",
    show_start=False,
)
_bind(
    "repl_eval",
    "Evaluate in JavaScript REPL",
    lambda result: presentation.repl_presentation(
        "Evaluate in JavaScript REPL", result
    ),
    tag="mutation",
    describe=presentation.code_argument("javascript"),
)

PROMPT = prompt.routing(
    "JavaScript and TypeScript",
    "js",
    ("run_tests", "repl_start", "repl_status", "repl_eval", "repl_stop"),
    notes=(
        "`js.repl_eval` runs JavaScript or TypeScript in one Node or Bun process for each"
        " project directory: types are stripped, declarations stay and top-level await works."
        " Load modules with `require()` or `await import()`; a static `import` works on Bun only.",
        '`runtime="auto"` picks Bun when the project has bun.lock, bun.lockb or'
        ' `"packageManager": "bun@..."`, else Node; pass "node" or "bun" to choose.',
        "For Node, the nearest .nvmrc, or .node-version if there is no .nvmrc, selects an"
        " installed nvm version. With no version file, use Node on PATH or the nvm default."
        " A live REPL keeps its Node version until you stop it.",
        "`js.run_tests` runs Vitest or Jest when package.json declares it, else bun test on"
        " Bun or node:test on Node.",
        '`js.repl_start(nest="auto")` loads a built NestJS app without listening or init:'
        " `svc(name)`, `get(Class)`, `services()` and `methods(Class)` reach its providers."
        ' `env_file=".env"` gives the REPL the variables of a dotenv file.',
    ),
    tools=JavaScriptTools,
)


def _home(program):
    """The installation that `program` on PATH belongs to, or None."""
    found = shutil.which(program)
    return Path(os.path.realpath(found)).parents[1] if found else None


GRANTS = [
    vis.Grant(home, reason=f"Run {label} for REPLs and tests.")
    for home, label in [
        (_home("node"), "Node.js"),
        (_home("bun"), "Bun"),
        (nvm_directory() / "versions" / "node", "Node.js from nvm"),
    ]
    if home is not None and home.is_dir()
]

vis.register_extension(
    vis.Extension(
        name="vis-lang-javascript",
        description="JavaScript and TypeScript tools on Node.js and Bun: test runs and a managed REPL.",
        version="0.2.2",
        alias="js",
        symbols=[
            vis.Symbol(JavaScriptTools(workspace_root=vis.workspace_root), name="js")
        ],
        prompt=PROMPT,
        grants=GRANTS,
    )
)
