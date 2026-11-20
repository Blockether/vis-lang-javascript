// The eval server of a managed JavaScript REPL, on Node.js or Bun. One JSON
// request per line on descriptor 0, one JSON answer per line on descriptor 1.
// Declarations live between evaluations: on Node in one node:repl context, on
// Bun because Bun.Transpiler's replMode hoists them to globals.
import fs from 'node:fs';
import path from 'node:path';
import repl from 'node:repl';
import vm from 'node:vm';
import util from 'node:util';
import domain from 'node:domain';
import readline from 'node:readline';
import { PassThrough } from 'node:stream';
import { Worker } from 'node:worker_threads';
import module from 'node:module';
import { Console } from 'node:console';

const answer = (res) => fs.writeSync(1, JSON.stringify(res) + '\n');
const rawOut = process.stdout.write.bind(process.stdout);
const rawErr = process.stderr.write.bind(process.stderr);
let captured = null;

// What evaluated code prints is part of its answer. Output after the answer,
// for example from a timer, goes to the runtime's log.
function capture(raw) {
  return (chunk, encoding, done) => {
    if (captured === null) return rawErr(chunk, encoding, done);
    captured.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'));
    if (typeof encoding === 'function') encoding();
    else if (typeof done === 'function') done();
    return true;
  };
}
process.stdout.write = capture(rawOut);
process.stderr.write = capture(rawErr);

let current = null;

const FRAME = /^\s+at /;
// An error as text: the frames of the code, without the driver's. The engine
// names its own frames (`internal`) and the frames that only follow the code
// (`outer`); `tidy` and `frame` rewrite the text and each kept frame.
const errorText = (err) => {
  if (!(err instanceof Error)) return 'Uncaught ' + util.inspect(err, { depth: 3 });
  const { internal, outer, tidy, frame } = engine.frames;
  const lines = tidy(util.inspect(err, { depth: 3 })).split('\n');
  const last = lines.findLastIndex((line) => FRAME.test(line) && !outer.test(line));
  return lines
    .filter((line, i) => !FRAME.test(line) || (i <= last && !internal.test(line)))
    .map((line) => (FRAME.test(line) ? frame(line) : line))
    .join('\n');
};
const shown = (value) =>
  value === undefined ? '' : util.inspect(value, { depth: 4, breakLength: 100, maxArrayLength: 100, maxStringLength: 10000 });

const answered = (value) => {
  let text;
  try { text = shown(value); } catch (e) { text = '<value could not be shown: ' + e + '>'; }
  return { value: text, type: value?.constructor?.name ?? typeof value };
};

// TypeScript loses its types before it runs; plain JavaScript passes unchanged.
function compiled(code) {
  try {
    return { code: module.stripTypeScriptTypes(code) };
  } catch (err) {
    if (err?.code === 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX') {
      try {
        return { code: module.stripTypeScriptTypes(code, { mode: 'transform' }) };
      } catch (again) {
        err = again;
      }
    }
    return { syntax: String(err?.message ?? err).trim() };
  }
}

// An engine is what differs between the runtimes: `run(code, finish)` evaluates
// and calls `finish` once with the answer, and `frames` shapes an error's stack.

// Node: one node:repl context keeps the declarations.
function nodeEngine() {
  const server = repl.start({
    input: new PassThrough(),
    output: new PassThrough(),
    terminal: false,
    useGlobal: true,
    breakEvalOnSigint: true,
    prompt: '',
  });
  // Before Node 25, the REPL binds its eval to a domain of its own, and an error
  // of the code reaches that domain instead of the eval's callback.
  server._domain?.on('error', (err) => {
    if (current) current.fail(err);
    else rawErr('uncaught: ' + errorText(err) + '\n');
  });
  return {
    runtime: 'node',
    version: process.versions.node,
    frames: {
      internal: /^\s+at (node:|.*(\(node:|driver\.mjs))/,
      // The driver's own frames, such as `new Promise (<anonymous>)`, follow the
      // last frame of the code; anonymous frames between code frames stay.
      outer: /^\s+at (node:|.*(\(node:|driver\.mjs|\(<anonymous>\)$))/,
      tidy: (text) => text,
      frame: (line) => line,
    },
    run(code, finish) {
      const ready = compiled(code);
      if (ready.syntax) return finish({ syntax: ready.syntax });
      const guard = domain.create();
      guard.on('error', (err) => finish({ exc: errorText(err) }));
      guard.run(() =>
        server.eval(ready.code, server.context, 'REPL', (err, value) => {
          if (err instanceof repl.Recoverable) return finish({ syntax: 'The code is incomplete: ' + String(err.err?.message ?? err.message) });
          if (err) return finish({ exc: errorText(err) });
          finish(answered(value));
        }),
      );
    },
  };
}

// Bun: replMode turns declarations into globals, wraps the code in a function,
// async for top-level await, and returns the last value as `{ value }`. A static
// import becomes import(), which resolves from the project directory.
function bunEngine() {
  // Bun's own console writes to the descriptors directly, past the capture.
  globalThis.console = new Console({ stdout: process.stdout, stderr: process.stderr, colorMode: false });
  globalThis.require = module.createRequire(path.join(process.cwd(), '<repl>'));
  const transpiler = new Bun.Transpiler({ loader: 'ts', replMode: true });
  const script = {
    filename: 'REPL',
    breakOnSigint: true,
    importModuleDynamically: (specifier) => import(Bun.resolveSync(specifier, process.cwd())),
  };
  return {
    runtime: 'bun',
    version: process.versions.bun,
    frames: {
      // `REPL:l:c` names a line of the transpiled code, so only `REPL` stays, and
      // the bare `at REPL:l:c` frame is the wrapper that replMode adds.
      internal: /^\s+at (.*driver\.mjs|.*\(unknown\)$|REPL:\d+:\d+$)/,
      outer: /^\s+at (.*driver\.mjs|.*\(unknown\)$|.*\(native:|REPL:\d+:\d+$)/,
      // Bun puts the source line of the throw and a caret before the error.
      tidy: (text) => text.replace(/^[^\n]*:\d+\n[^\n]*\n[ \t]*\^\n\n/, ''),
      frame: (line) => line.replace(/\(REPL:\d+:\d+\)$/, '(REPL)').replace(/^(\s+at )<anonymous> \(REPL\)$/, '$1REPL'),
    },
    run(code, finish) {
      let ready;
      try {
        ready = transpiler.transformSync(code);
      } catch (err) {
        return finish({ syntax: String(err?.message ?? err).trim() });
      }
      let result;
      try {
        result = vm.runInThisContext(ready, script);
      } catch (err) {
        return finish({ exc: errorText(err) });
      }
      Promise.resolve(result).then(
        (wrapped) => finish(answered(wrapped?.value)),
        (err) => finish({ exc: errorText(err) }),
      );
    },
  };
}

const engine = typeof Bun === 'undefined' ? nodeEngine() : bunEngine();

function evaluate(code) {
  return new Promise((resolve) => {
    const out = (captured = []);
    let settled = false;
    const finish = (res) => {
      if (settled) return;
      settled = true;
      captured = null;
      current = null;
      resolve({ ok: !res.exc && !res.syntax, out: out.join(''), ...res });
    };
    current = {
      abandon: () => finish({ exc: 'Interrupted: the evaluation was abandoned. A promise it started can still run.' }),
      fail: (err) => finish({ exc: errorText(err) }),
    };
    // Node's SIGINT watchdog breaks the synchronous part of the code only when
    // the process has no SIGINT listener, so the listener waits outside it.
    process.removeListener('SIGINT', onInterrupt);
    try {
      engine.run(code, finish);
    } finally {
      process.on('SIGINT', onInterrupt);
    }
  });
}

// A SIGINT stops running synchronous code (breakEvalOnSigint, breakOnSigint). For code that
// waits on a promise, the eval is abandoned so the REPL answers again.
function onInterrupt() {
  current?.abandon();
}
process.on('SIGINT', onInterrupt);
process.on('uncaughtException', (err) => rawErr('uncaught: ' + errorText(err) + '\n'));
process.on('unhandledRejection', (err) => rawErr('unhandled rejection: ' + errorText(err) + '\n'));

// One byte on the interrupt FIFO asks to stop the running eval. A worker reads
// it, because the main thread can be held by the code itself.
const stop = process.env.VIS_LANG_RENDEZVOUS && path.join(process.env.VIS_LANG_RENDEZVOUS, 'interrupt');
if (stop && fs.existsSync(stop)) {
  // The FIFO is open before the startup ping is answered, so an interrupt
  // that comes right after the start finds a reader.
  const fd = fs.openSync(stop, 'r+');
  const watcher = new Worker(
    `const fs = process.getBuiltinModule('node:fs'); const { workerData } = process.getBuiltinModule('node:worker_threads');
     const b = Buffer.alloc(64);
     for (;;) { if (fs.readSync(workerData, b) > 0) process.kill(process.pid, 'SIGINT'); }`,
    { eval: true, workerData: fd },
  );
  watcher.unref();
}

let queue = Promise.resolve();
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on('line', (line) => {
  let req;
  try { req = JSON.parse(line); } catch (err) { return answer({ ok: false, exc: 'bad request: ' + err.message }); }
  if (req.op === 'ping') return answer({ id: req.id, ok: true, pong: current === null, runtime: engine.runtime, version: engine.version });
  queue = queue.then(async () => answer({ id: req.id, ...(await evaluate(String(req.code ?? ''))) }));
});
lines.on('close', () => process.exit(0));
