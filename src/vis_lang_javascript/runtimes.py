"""Which JavaScript runtime a project uses: Node.js or Bun."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from vis_lang_interface import ToolMissing


@dataclass(frozen=True)
class Runtime:
    """One JavaScript runtime: what differs between Node and Bun outside the driver."""

    name: str
    """The program on PATH, and the name of its built-in test runner."""
    label: str
    eval_flags: tuple[str, ...]
    """The flags before `-e` that run the REPL's ES module loader."""
    minimum: tuple[int, ...]


NODE = Runtime(
    "node",
    "Node",
    ("--disable-warning=ExperimentalWarning", "--input-type=module"),
    # Node 22.18 strips TypeScript types by default, also in the files it imports.
    (22, 18),
)
BUN = Runtime(
    "bun",
    "Bun",
    # Bun reads .env by itself; the REPL gets only the variables of env_file.
    ("--no-env-file",),
    # The release the Bun driver is checked on.
    (1, 4, 5),
)
RUNTIMES = {runtime.name: runtime for runtime in (NODE, BUN)}
CHOICES = ("auto", *RUNTIMES)

BUN_LOCKS = ("bun.lock", "bun.lockb")
OTHER_LOCKS = (
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


def detected(directory):
    """The runtime that `directory` or its nearest parent declares, else node.

    A Bun lockfile or `"packageManager": "bun@..."` means bun; another
    lockfile or package manager means node. The walk ends at a `.git`.
    """
    for place in (Path(directory), *Path(directory).parents):
        if any((place / name).exists() for name in BUN_LOCKS):
            return "bun"
        try:
            manifest = json.loads((place / "package.json").read_text("utf-8"))
        except (OSError, ValueError):
            manifest = {}
        manager = manifest.get("packageManager") if isinstance(manifest, dict) else None
        if isinstance(manager, str) and manager:
            return "bun" if manager.startswith("bun@") else "node"
        if any((place / name).exists() for name in OTHER_LOCKS):
            return "node"
        if (place / ".git").exists():
            break
    return "node"


def label(name):
    """The display name of the runtime `name`, or `name` itself."""
    known = RUNTIMES.get(name)
    return known.label if known else name


def choose(directory, runtime="auto"):
    """The `Runtime` named by `runtime`, or the detected one for "auto";
    raises ToolMissing when it is not on PATH."""
    if runtime not in CHOICES:
        raise ValueError(
            f"runtime must be one of {', '.join(CHOICES)}, not {runtime!r}"
        )
    chosen = RUNTIMES[detected(directory) if runtime == "auto" else runtime]
    if shutil.which(chosen.name) is None:
        why = (
            f"the project uses {chosen.label}"
            if runtime == "auto"
            else f"runtime={chosen.name!r} was asked for"
        )
        raise ToolMissing(f"{chosen.name} is not on PATH, and {why}.")
    return chosen
