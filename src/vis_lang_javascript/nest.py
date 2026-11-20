"""The NestJS profile of the REPL: which built entry file loads the app.

The REPL runs the built JavaScript, not the TypeScript sources, because Nest's
dependency injection reads the decorator metadata that the TypeScript compiler
emits. nest.cjs loads the entry up to its NestFactory call and makes an
application context from the same root module, without listening.
"""

from __future__ import annotations

import json
from pathlib import Path


def _candidates(cwd):
    """Entry files that `nest build` can write, newest layout first."""
    entry_file, source_root = "main", "src"
    try:
        config = json.loads((cwd / "nest-cli.json").read_text(encoding="utf-8"))
        entry_file = str(config.get("entryFile") or entry_file)
        source_root = str(config.get("sourceRoot") or source_root)
    except (OSError, ValueError):
        pass
    for dist in ("dist", f"dist/{source_root}"):
        yield cwd / dist / f"{entry_file}.js"


def entry(cwd, wanted):
    """The built entry file for `wanted`: a path relative to `cwd`, or "auto" for
    the one that nest-cli.json names, the newest when there are several."""
    cwd = Path(cwd)
    if wanted != "auto":
        found = (cwd / wanted).resolve()
        if not found.is_file():
            raise FileNotFoundError(
                f"{found} does not exist. Build the app first, for example with `nest build`."
            )
        return str(found)
    built = [path for path in _candidates(cwd) if path.is_file()]
    if not built:
        tried = ", ".join(str(path.relative_to(cwd)) for path in _candidates(cwd))
        raise FileNotFoundError(
            f"No built Nest entry in {cwd} (tried {tried}). Build the app first,"
            " for example with `nest build`, or name the entry file."
        )
    return str(max(built, key=lambda path: path.stat().st_mtime).resolve())


def stale(entry_file, cwd):
    """Whether a TypeScript source under `cwd`/src is newer than `entry_file`."""
    built = Path(entry_file).stat().st_mtime
    sources = Path(cwd) / "src"
    return any(path.stat().st_mtime > built for path in sources.rglob("*.ts"))
