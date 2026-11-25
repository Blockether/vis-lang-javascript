"""The managed JavaScript REPL this extension owns, on Node.js or Bun.

`js.repl_start`, `js.repl_status`, `js.repl_stop` and `js.repl_eval` reach this
module. The REPL is one `node` or `bun` process for each project directory. It runs
`driver.mjs`, a small line-framed eval server: one JSON request goes in per
line, and one JSON answer comes out per line. Declarations live between
evaluations, and TypeScript loses its types before it runs.

The runtime holds its own channels open and runs under a shell id, so it
outlives a sandbox restart: the next Python process attaches to it, with its
state. Only `stop` and the end of the session end it.
"""

from __future__ import annotations

import atexit
import hashlib
import itertools
import json
import os
import secrets
import shlex
import threading
import time
from pathlib import Path

from vis_lang_interface import RuntimeGone, ToolMissing, runtime
from vis_lang_interface.process import attach_shell

from vis_lang_javascript import runtimes

HERE = Path(__file__).parent
SOURCES = {
    name: (HERE / name).read_text(encoding="utf-8")
    for name in ("driver.mjs", "core.mjs", "cancel.cjs", "nest.cjs")
}
"""The runtime's own files, placed together in its rendezvous."""

LOADER = (
    "const { pathToFileURL } = await import('node:url');"
    " await import(pathToFileURL(process.env.VIS_LANG_RENDEZVOUS + '/driver.mjs').href);"
)
"""Runs the driver from the runtime's rendezvous and identifies a kept REPL."""


def command(runtime):
    """The argv that runs the driver on `runtime`, a `runtimes.Runtime`."""
    return (runtime.binary or runtime.name, *runtime.eval_flags, "-e", LOADER)


STARTUP_TIMEOUT_S = 20.0
SETUP_TIMEOUT_S = 120.0
"""Seconds a profile, such as the Nest application context, gets to start."""
DEFAULT_EVAL_TIMEOUT_S = 30.0
INTERRUPT_GRACE_S = 5.0
"""Longer than the driver's own grace (CANCEL_GRACE_MS) before it abandons a promise."""
ANSWER_CHECK_S = 1.0
STDERR_TAIL_LINES = 40

INTERRUPTED = {
    "": "The code was interrupted. The REPL answers again, and the changes the"
    " code made before it stopped stay. A promise that it started can still run.",
    "io": "The code was interrupted at its next I/O call, which failed. The REPL"
    " answers again, and the changes the code made before it stopped stay. A"
    " request it had already sent may have run.",
    "abandoned": "The code was interrupted. The REPL answers again, and the changes"
    " the code made before it stopped stay. The promise it waited on was abandoned:"
    " it made no I/O call since the interrupt and can still run, but its next"
    " fetch, HTTP, pg or ioredis call fails.",
}
"""How an interrupted eval ended, by the driver's `interrupted`: by the signal or
by itself, at a refused I/O call, or abandoned after the grace."""

BUSY = "The REPL is busy: code from an earlier eval still runs in it."

_REPLS: dict[str, "_Repl"] = {}
_REPLS_LOCK = threading.RLock()


class ReplError(RuntimeError):
    """The REPL cannot evaluate: it is down, busy or timed out."""


class _Repl:
    """One node or bun process and the framed conversation with it."""

    def __init__(self, live, cwd, env_fingerprint, place, runtime):
        self.live = live
        self.cwd = cwd
        self.env_fingerprint = dict(env_fingerprint or {})
        self.place = place
        self.runtime = runtime
        self.version = runtime.version.removeprefix("v")
        self.source = runtime.source
        self.binary = runtime.binary
        self.stuck = False
        self.interrupted = ""
        self.profile = {}
        self.setup = {}
        # A kept runtime can still hold answers to an earlier process's calls,
        # so every id carries a prefix of this object's own.
        self.prefix = secrets.token_hex(4)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    @property
    def pid(self):
        return self.live.pid

    def is_alive(self):
        return self.live.is_running

    def request(self, payload, timeout_s):
        try:
            with self.lock:
                wanted = f"{self.prefix}-{next(self.ids)}"
                return self.live.request(
                    dict(payload, id=wanted), timeout_s, wants=wanted
                )
        except RuntimeGone as ex:
            self.kill()
            raise ReplError(
                f"{self.runtime.label} REPL closed the connection; start it again with "
                f"js.repl_start(). ({ex})"
            ) from ex

    def answers(self, timeout_s):
        """Whether the driver answers a ping as idle within `timeout_s`."""
        if not self.lock.acquire(timeout=timeout_s):
            return False
        try:
            wanted = f"{self.prefix}-{next(self.ids)}"
            pong = self.live.request(
                {"op": "ping", "id": wanted}, timeout_s, wants=wanted
            )
        except (RuntimeGone, TimeoutError):
            return False
        finally:
            self.lock.release()
        self.version = str(pong.get("version") or self.version)
        self.binary = self.binary or str(pong.get("binary") or "")
        self.interrupted = str(pong.get("interrupted") or "")
        is_idle = pong.get("pong") is True
        if is_idle:
            self.stuck = False
        return is_idle

    def settles(self, timeout_s):
        """Whether the driver answers as idle before `timeout_s` passes.

        The driver can read a ping before the interrupt's signal, and then it
        is still busy, so the ping repeats until the deadline.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            if self.answers(left):
                return True
            time.sleep(min(0.05, max(left, 0)))

    def interrupt(self):
        """Write one byte to the driver's interrupt FIFO."""
        try:
            fd = os.open(
                os.path.join(self.place, "interrupt"), os.O_WRONLY | os.O_NONBLOCK
            )
        except OSError:
            return False
        try:
            os.write(fd, b"!")
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def timed_out(self, timeout_s):
        """Interrupt the code of a timed-out eval, and say whether it stopped."""
        said = f"{self.runtime.label} eval timed out after {int(timeout_s * 1000)}ms"
        if self.interrupt() and self.settles(INTERRUPT_GRACE_S):
            return f"{said}. {INTERRUPTED.get(self.interrupted, INTERRUPTED[''])}"
        self.stuck = True
        return (
            f"{said}, and the code did not stop on the interrupt. The REPL is busy"
            " until the code ends. Call js.repl_stop() to end it, and lose its state."
        )

    def kill(self):
        self.live.stop()

    def detach(self):
        """Leave the runtime and its state running for the next process."""
        self.live.detach()


def shell_id(cwd):
    """The shell id that keeps the REPL for `cwd` across a sandbox restart."""
    return "vis-lang-javascript-" + hashlib.sha256(cwd.encode()).hexdigest()[:12]


def abbreviate_home(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if home and path.startswith(home) else path


def _project_dir(options):
    raw = (options or {}).get("cwd")
    return os.path.realpath(os.path.expanduser(str(raw or "") or os.getcwd()))


def _displayed(cmd):
    return [*cmd[:-2], f"<vis {Path(cmd[0]).name} driver>"]


def _kept_command(line):
    """Read the exact runtime argv and rendezvous from a kept shell line."""
    try:
        words = shlex.split(str(line or ""))
    except ValueError:
        return None
    if len(words) < 7 or words[0] != "exec":
        return None
    argv = words[1:-4]
    kind = runtimes.RUNTIMES.get(Path(argv[0]).name)
    if kind is None or tuple(argv[1:]) != command(kind)[1:]:
        return None
    requests = words[-4]
    if not requests.startswith("3<>"):
        return None
    return kind, argv, os.path.dirname(requests[3:])


def _saved(place, name):
    """The JSON object that a start saved as `name` in `place`, else {}."""
    try:
        with open(os.path.join(place, name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _kept(cwd):
    """The REPL that an earlier Python process left running for `cwd`, or None.

    It never starts a runtime: without a live one under the shell id, it is None.
    """
    sid = shell_id(cwd)
    try:
        shell = attach_shell(sid)
    except Exception:
        return None
    line = str(shell.command or "")
    if str(shell.status) != "running":
        return None
    command_info = _kept_command(line)
    if command_info is None:
        raise ReplError("Cannot recognize the live REPL command. It was not stopped.")
    kind, argv, place = command_info
    if not place or not os.path.isfile(os.path.join(place, "driver.mjs")):
        raise ReplError("Cannot find the files for the live REPL. It was not stopped.")
    # Attach directly. runtime.start can replace a live shell if it cannot attach.
    try:
        meeting = runtime.Rendezvous(path=place).open()
        live = runtime.Runtime(shell, meeting, argv, cwd, kept=True)
    except OSError as ex:
        raise ReplError("Cannot attach to the live REPL. It was not stopped.") from ex
    kept = _Repl(live, cwd, _saved(place, "env.json"), place, kind)
    saved = _saved(place, "node.json")
    kept.source = saved.get("source", "PATH" if argv[0] == "node" else "")
    if os.path.isabs(argv[0]):
        kept.binary = argv[0]
    kept.version = saved.get("version", "")
    kept.profile = _saved(place, "profile.json")
    kept.setup = _saved(place, "setup.json")
    # An earlier process may have left code running in it.
    kept.stuck = True
    return kept


def _live(cwd):
    with _REPLS_LOCK:
        repl = _REPLS.get(cwd)
        if repl is not None and not repl.is_alive():
            _REPLS.pop(cwd, None)
            repl = None
        if repl is None:
            repl = _kept(cwd)
            if repl is not None:
                _REPLS[cwd] = repl
        return repl


def _status(cwd, repl):
    answer = {"result": "status", "cwd": cwd, "status": "up" if repl else "down"}
    if repl:
        answer["pid"] = repl.pid
        answer["cmd"] = _displayed(repl.live.command)
        answer["runtime"] = repl.runtime.name
        if repl.version:
            answer["version"] = (
                "v" + repl.version.removeprefix("v")
                if repl.runtime.name == "node"
                else repl.version
            )
        if repl.source:
            answer["source"] = repl.source
        if repl.env_fingerprint:
            answer["env"] = dict(repl.env_fingerprint)
        if repl.profile:
            answer["profile"] = dict(repl.profile)
        if repl.setup:
            answer["setup"] = dict(repl.setup)
    return answer


def _node_note(repl):
    """Say when the project now selects another Node, without replacing the live one."""
    if repl.runtime.name != "node":
        return []
    try:
        wanted = runtimes.choose(repl.cwd, "node")
    except ToolMissing as ex:
        return [f"Keeping the live REPL. {ex}"]
    actual = repl.binary
    if (actual and os.path.realpath(actual) != os.path.realpath(wanted.binary)) or (
        repl.version
        and repl.version.removeprefix("v") != wanted.version.removeprefix("v")
    ):
        return [
            f"Keeping the live REPL. A new REPL would use Node {wanted.version}"
            f" from {wanted.source} ({wanted.binary})."
        ]
    return []


def status(options=None):
    cwd = _project_dir(options)
    repl = _live(cwd)
    # The ping also tells a kept REPL its version.
    is_busy = repl is not None and not repl.answers(ANSWER_CHECK_S)
    answer = _status(cwd, repl)
    if repl is not None:
        answer["notes"] = _node_note(repl)
    if is_busy:
        answer["busy"] = True
        answer["message"] = BUSY
    return answer


def start(options=None):
    """Start the REPL for `cwd` on `runtime` ("auto", "node" or "bun"), or keep
    the live one.

    A live REPL is never replaced, because its state is the work: with "auto"
    it is kept on whichever runtime it runs, and another runtime asked for by
    name is refused. A start succeeds only after the driver answers its ping on
    a runtime that is new enough.
    """
    options = options or {}
    cwd = _project_dir(options)
    wanted_runtime = options.get("runtime") or "auto"
    with _REPLS_LOCK:
        live = _live(cwd)
        if live:
            answer = _status(cwd, live)
            if wanted_runtime not in ("auto", live.runtime.name):
                answer["result"] = "failed"
                answer["message"] = (
                    f"The REPL for {abbreviate_home(cwd)} already runs on"
                    f" {live.runtime.label}. Call js.repl_stop(), then start it on"
                    f" {runtimes.label(wanted_runtime)}."
                )
                return answer
            wanted = dict(options.get("env_fingerprint") or {})
            differing = sorted(
                name
                for name in {*live.env_fingerprint, *wanted}
                if live.env_fingerprint.get(name) != wanted.get(name)
            )
            profile = dict(options.get("profile") or {})
            if profile != live.profile:
                answer["result"] = "failed"
                answer["message"] = (
                    f"The REPL for {abbreviate_home(cwd)} already runs"
                    f" {_profile_label(live.profile)}. Call js.repl_stop(), then start it"
                    f" {_profile_label(profile)}."
                )
            elif differing:
                answer["result"] = "failed"
                answer["message"] = (
                    f"The {live.runtime.label} REPL for {abbreviate_home(cwd)} already runs with a"
                    f" different env ({', '.join(differing)}). Call js.repl_stop(),"
                    " then start it with this env."
                )
            else:
                if live.runtime.name == "node" and not live.version:
                    live.answers(ANSWER_CHECK_S)
                    answer = _status(cwd, live)
                answer["result"] = "already-running"
                answer["notes"] = _node_note(live)
            return answer
        chosen = runtimes.choose(cwd, wanted_runtime)
        meeting = runtime.Rendezvous(chosen.name).open()
        try:
            for name, text in SOURCES.items():
                meeting.place(name, text)
            meeting.place(
                "env.json", json.dumps(dict(options.get("env_fingerprint") or {}))
            )
            meeting.place(
                "profile.json", json.dumps(dict(options.get("profile") or {}))
            )
            if chosen.binary:
                meeting.place(
                    "node.json",
                    json.dumps({"source": chosen.source, "version": chosen.version}),
                )
            os.mkfifo(os.path.join(meeting.path, "interrupt"))
            live = runtime.start(
                list(command(chosen)),
                cwd=cwd,
                env=chosen.child_env(options.get("env")),
                meeting=meeting,
                shell_id=shell_id(cwd),
            )
        except BaseException:
            meeting.close()
            raise
        repl = _Repl(live, cwd, options.get("env_fingerprint"), meeting.path, chosen)
        repl.profile = dict(options.get("profile") or {})
        try:
            pong = repl.live.request(
                {"op": "ping", "id": "start"}, STARTUP_TIMEOUT_S, wants="start"
            )
            repl.version = str(pong.get("version") or "")
            if pong.get("runtime") != chosen.name:
                # For example the `node` that the oven/bun image links to bun.
                raise ReplError(
                    f"`{chosen.binary or chosen.name}` runs {pong.get('runtime') or 'an unknown runtime'}"
                )
            if runtimes.version_parts(repl.version) < chosen.minimum:
                raise ReplError(
                    f"{chosen.label} {repl.version} is too old: the REPL needs"
                    f" {chosen.label} {'.'.join(map(str, chosen.minimum))} or newer"
                )
            if repl.profile:
                setup = repl.live.request(
                    {"op": "setup", "id": "setup"}, SETUP_TIMEOUT_S, wants="setup"
                )
                if not setup.get("ok"):
                    raise ReplError(f"the Nest app did not start: {setup.get('exc')}")
                repl.setup = {k: v for k, v in setup.items() if k not in ("id", "ok")}
                # A later Python process reads it back in _kept.
                with open(
                    os.path.join(repl.place, "setup.json"), "w", encoding="utf-8"
                ) as handle:
                    json.dump(repl.setup, handle)
        except BaseException as ex:
            tail = repl.live.log_tail(STDERR_TAIL_LINES)
            repl.kill()
            if not isinstance(ex, Exception):
                raise
            message = f"{chosen.label} REPL failed its startup handshake: {ex}"
            return {
                "result": "failed",
                "status": "failed",
                "cwd": cwd,
                "cmd": _displayed(list(command(chosen))),
                "message": message + ("\n" + "\n".join(tail) if tail else ""),
            }
        _REPLS[cwd] = repl
        answer = _status(cwd, repl)
        answer["result"] = "started"
        return answer


def _profile_label(profile):
    if profile.get("kind") == "nest":
        return f"with the Nest app of {abbreviate_home(profile.get('entry', ''))}"
    return "without a profile"


def stop(options=None):
    """Stop the REPL for `cwd`. Safe when none is running."""
    cwd = _project_dir(options)
    with _REPLS_LOCK:
        repl = _live(cwd)
        _REPLS.pop(cwd, None)
    if repl:
        repl.kill()
    return {
        "result": "stopped" if repl else "not-managed",
        "cwd": cwd,
        "status": "down",
    }


def evaluate(options):
    """Evaluate `code` in the REPL for `cwd`. Answers the driver's
    `{ok, out, value, type, exc, syntax}`."""
    cwd = _project_dir(options)
    repl = _live(cwd)
    if repl is None:
        shown = abbreviate_home(cwd)
        raise ReplError(
            f'No REPL is up for {shown}; call js.repl_start(cwd="{shown}") first'
        )
    timeout_ms = options.get("timeout_ms")
    timeout_s = float(timeout_ms) / 1000.0 if timeout_ms else DEFAULT_EVAL_TIMEOUT_S
    if repl.stuck and not repl.answers(ANSWER_CHECK_S):
        raise ReplError(BUSY + " The code was not sent. Call js.repl_stop() to end it.")
    started = time.monotonic()
    try:
        answer = repl.request({"code": str(options.get("code") or "")}, timeout_s)
    except TimeoutError:
        raise ReplError(repl.timed_out(timeout_s)) from None
    answer["duration_ms"] = round((time.monotonic() - started) * 1000)
    return answer


def detach_all():
    """Leave every REPL running, kept for the next Python process."""
    with _REPLS_LOCK:
        live = list(_REPLS.values())
        _REPLS.clear()
    for repl in live:
        repl.detach()


atexit.register(detach_all)
