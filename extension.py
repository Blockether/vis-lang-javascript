"""Vis entrypoint. The tools themselves live in vis_lang_javascript."""

import os
import shutil
from pathlib import Path

import blockether.vis.extension as vis
from vis_lang_interface import presentation, prompt

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
    "Start Node REPL",
    lambda result: presentation.session_presentation("Start Node REPL", result),
    tag="mutation",
)
_bind(
    "repl_status",
    "Check Node REPL",
    lambda result: presentation.session_presentation("Check Node REPL", result),
    show_start=False,
)
_bind(
    "repl_stop",
    "Stop Node REPL",
    lambda result: presentation.session_presentation("Stop Node REPL", result),
    tag="mutation",
    show_start=False,
)
_bind(
    "repl_eval",
    "Evaluate in Node REPL",
    lambda result: presentation.repl_presentation("Evaluate in Node REPL", result),
    tag="mutation",
    describe=presentation.code_argument("javascript"),
)

PROMPT = prompt.routing(
    "JavaScript and TypeScript",
    "js",
    ("run_tests", "repl_start", "repl_status", "repl_eval", "repl_stop"),
    notes=(
        "`js.repl_eval` runs JavaScript or TypeScript in one Node process for each project"
        " directory: types are stripped, declarations stay and top-level await works. Load"
        " modules with `require()` or `await import()`, not a static `import`.",
        "`js.run_tests` runs Vitest or Jest when package.json declares it, else node:test.",
    ),
    tools=JavaScriptTools,
)


def _node_home():
    """The installation that the `node` on PATH belongs to, or None."""
    found = shutil.which("node")
    return Path(os.path.realpath(found)).parents[1] if found else None


GRANTS = [
    vis.Grant(home, reason="Run Node.js for REPLs and tests.")
    for home in [_node_home()]
    if home is not None
]

vis.register_extension(
    vis.Extension(
        name="vis-lang-javascript",
        description="JavaScript and TypeScript tools on Node.js: test runs and a managed REPL.",
        version="0.1.0",
        alias="js",
        symbols=[
            vis.Symbol(JavaScriptTools(workspace_root=vis.workspace_root), name="js")
        ],
        prompt=PROMPT,
        grants=GRANTS,
    )
)
