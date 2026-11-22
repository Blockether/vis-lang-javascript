"""Which JavaScript runtime a project uses: Node.js or Bun."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from vis_lang_interface import ToolMissing, process


@dataclass(frozen=True)
class Runtime:
    """One JavaScript runtime: what differs between Node and Bun outside the driver."""

    name: str
    """The runtime name and the name of its built-in test runner."""
    label: str
    eval_flags: tuple[str, ...]
    """The flags before `-e` that run the REPL's ES module loader."""
    minimum: tuple[int, ...]
    binary: str = ""
    source: str = ""
    version: str = ""

    def child_env(self, env=None):
        """Put this Node first on PATH, also for the tools that it starts."""
        added = dict(env or {})
        if self.name == "node" and self.binary:
            path = added.get("PATH", os.environ.get("PATH", os.defpath))
            directory = str(Path(self.binary).parent)
            added["PATH"] = f"{directory}{os.pathsep}{path}" if path else directory
        return added


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


def _parents(directory):
    directory = Path(directory).absolute()
    for place in (directory, *directory.parents):
        yield place
        if (place / ".git").exists():
            break


def detected(directory):
    """The runtime that `directory` or its nearest parent declares, else node.

    A Bun lockfile or `"packageManager": "bun@..."` means bun; another
    lockfile or package manager means node. The walk ends at a `.git`.
    """
    for place in _parents(directory):
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
    return "node"


def label(name):
    """The display name of the runtime `name`, or `name` itself."""
    known = RUNTIMES.get(name)
    return known.label if known else name


def nvm_directory():
    """The directory that holds nvm's versions and aliases."""
    return Path(os.environ.get("NVM_DIR") or "~/.nvm").expanduser().absolute()


_FULL_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_VERSION_PREFIX = re.compile(r"v?\d+(?:\.\d+){0,2}")
_ALIAS = re.compile(r"(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*|lts/(?:[A-Za-z0-9_-]+|\*))")


def version_parts(text):
    """The numbers in a Node or Bun release version, or an empty tuple."""
    match = _FULL_VERSION.fullmatch(text)
    return tuple(map(int, match.groups())) if match else ()


def _version_file(directory):
    fallback = None
    for place in _parents(directory):
        nvmrc = place / ".nvmrc"
        if nvmrc.is_file():
            return nvmrc
        node_version = place / ".node-version"
        if fallback is None and node_version.is_file():
            fallback = node_version
    return fallback


def _read_spec(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return " ".join(line for raw in lines if (line := raw.partition("#")[0].strip()))


def _nvm_binary(home, spec):
    seen = set()
    while True:
        if _VERSION_PREFIX.fullmatch(spec):
            prefix = tuple(map(int, spec.removeprefix("v").split(".")))
            break
        if spec in ("node", "stable"):
            prefix = ()
            break
        if spec in seen or not _ALIAS.fullmatch(spec):
            return None
        seen.add(spec)
        try:
            spec = _read_spec(home / "alias" / spec)
        except (OSError, UnicodeError):
            return None
    try:
        versions = list((home / "versions" / "node").iterdir())
    except OSError:
        return None
    candidates = []
    for installed in versions:
        version = version_parts(installed.name)
        binary = installed / "bin" / "node"
        if version and version[: len(prefix)] == prefix and shutil.which(str(binary)):
            candidates.append((version, str(binary)))
    return max(candidates)[1] if candidates else None


def _node(directory):
    home = nvm_directory()
    pin = _version_file(directory)
    if pin is not None:
        try:
            spec = _read_spec(pin)
        except (OSError, UnicodeError) as ex:
            raise ToolMissing(f"Cannot read the Node version in {pin}: {ex}") from ex
        binary = _nvm_binary(home, spec)
        if binary is None:
            raise ToolMissing(
                f"{pin} asks for Node {spec or '(empty)'!r}, but it is not installed"
                f" in {home}. Check the version and run nvm install in the project."
            )
        source = pin.name
    else:
        binary = shutil.which("node")
        source = "PATH"
        if binary is None:
            binary = _nvm_binary(home, "default")
            source = "nvm default"
        if binary is None:
            raise ToolMissing(
                "node is not on PATH and nvm has no installed default."
                " Run nvm install or put Node on PATH."
            )
    chosen = replace(NODE, binary=os.path.abspath(binary), source=source)
    try:
        checked = process.run(
            [chosen.binary, "--version"],
            cwd=directory,
            env=chosen.child_env(process.call_env()),
            timeout_s=10,
        )
    except process.ToolTimeout as ex:
        raise ToolMissing(
            f"{chosen.binary} ({source}) did not answer --version."
        ) from ex
    version = checked.out.strip()
    parts = version_parts(version)
    if not checked.is_ok or not version.startswith("v") or not parts:
        raise ToolMissing(f"{chosen.binary} ({source}) did not report a Node version.")
    if parts < NODE.minimum:
        raise ToolMissing(
            f"Node {version} from {source} ({chosen.binary}) is too old."
            " Use Node 22.18 or newer."
        )
    return replace(chosen, version=version)


def runtime_name(directory, runtime):
    """The detected or requested runtime name, without checking its binary."""
    if runtime not in CHOICES:
        raise ValueError(
            f"runtime must be one of {', '.join(CHOICES)}, not {runtime!r}"
        )
    return detected(directory) if runtime == "auto" else runtime


def choose(directory, runtime="auto"):
    """The detected or requested runtime, with the project's Node if needed."""
    chosen = RUNTIMES[runtime_name(directory, runtime)]
    if chosen.name == "node":
        return _node(directory)
    if shutil.which(chosen.name) is None:
        why = (
            f"the project uses {chosen.label}"
            if runtime == "auto"
            else f"runtime={chosen.name!r} was asked for"
        )
        raise ToolMissing(f"{chosen.name} is not on PATH, and {why}.")
    return chosen
