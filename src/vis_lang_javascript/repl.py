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

from vis_lang_interface import RuntimeGone, runtime
from vis_lang_interface.process import shell_call

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
"""Runs the driver from the runtime's rendezvous. The command line stays the same
for every start in one directory, so a later process finds the runtime by it."""


def command(runtime):
    """The argv that runs the driver on `runtime`, a `runtimes.Runtime`."""
    return (runtime.name, *runtime.eval_flags, "-e", LOADER)


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
        self.version = ""
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
    return [*cmd[:-2], f"<vis {cmd[0]} driver>"]


def _kept_directory(command):
    """The rendezvous directory in the shell line of a kept REPL, or None."""
    try:
        words = shlex.split(str(command or ""))
    except ValueError:
        return None
    for word in words:
        if word.startswith("3<>"):
            return os.path.dirname(word[3:])
    return None


def _kept(cwd):
    """The REPL that an earlier Python process left running for `cwd`, or None.

    It never starts a runtime: without a live one under the shell id, it is None.
    """
    sid = shell_id(cwd)
    try:
        shell = shell_call()({"op": "logs", "id": sid, "offset": -1})
    except Exception:
        return None
    line = str(shell.get("command") or "")
    if str(shell.get("status")) != "running":
        return None
    kind = next(
        (
            known
            for known in runtimes.RUNTIMES.values()
            if shlex.join(command(known)) in line
        ),
        None,
    )
    if kind is None:
        return None
    place = _kept_directory(line)
    if not place or not os.path.isfile(os.path.join(place, "driver.mjs")):
        return None
    try:
        live = runtime.start(list(command(kind)), cwd=cwd, shell_id=sid)
    except Exception:
        return None
    if live.pid != shell.get("pid"):
        live.stop()
        return None
    try:
        with open(os.path.join(place, "env.json"), encoding="utf-8") as handle:
            fingerprint = json.load(handle)
    except (OSError, ValueError):
        fingerprint = {}
    kept = _Repl(live, cwd, fingerprint, place, kind)
    try:
        with open(os.path.join(place, "profile.json"), encoding="utf-8") as handle:
            kept.profile = json.load(handle)
    except (OSError, ValueError):
        pass
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
        answer["cmd"] = _displayed(list(command(repl.runtime)))
        answer["runtime"] = repl.runtime.name
        if repl.version:
            answer["version"] = repl.version
        if repl.env_fingerprint:
            answer["env"] = dict(repl.env_fingerprint)
        if repl.profile:
            answer["profile"] = dict(repl.profile)
        if repl.setup:
            answer["setup"] = dict(repl.setup)
    return answer


def status(options=None):
    cwd = _project_dir(options)
    repl = _live(cwd)
    # The ping also tells a kept REPL its version.
    is_busy = repl is not None and not repl.answers(ANSWER_CHECK_S)
    answer = _status(cwd, repl)
    if is_busy:
        answer["busy"] = True
        answer["message"] = BUSY
    return answer


def _version(text):
    try:
        return tuple(int(part) for part in str(text).split(".")[:3])
    except ValueError:
        return (0, 0)


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
                answer["result"] = "already-running"
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
            os.mkfifo(os.path.join(meeting.path, "interrupt"))
            live = runtime.start(
                list(command(chosen)),
                cwd=cwd,
                env={k: v for k, v in (options.get("env") or {}).items()},
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
                    f"the `{chosen.name}` on PATH runs {pong.get('runtime') or 'an unknown runtime'}"
                )
            if _version(repl.version) < chosen.minimum:
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
