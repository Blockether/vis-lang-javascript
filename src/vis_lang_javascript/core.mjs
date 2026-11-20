// The evaluator of the managed JavaScript REPL (driver.mjs), on Node.js or Bun.
// Declarations live between evaluations: on Node in one node:repl context, on
// Bun because Bun.Transpiler's replMode hoists them to globals. Each eval runs
// in one AsyncLocalStorage store (cancel.cjs), which owns what it prints and
// whether it may still start I/O.
import path from 'node:path';
import repl from 'node:repl';
import vm from 'node:vm';
import util from 'node:util';
import domain from 'node:domain';
import module from 'node:module';
import { Console } from 'node:console';
import { PassThrough } from 'node:stream';

const require = module.createRequire(import.meta.url);
const cancel = require('./cancel.cjs');

/** Milliseconds an interrupted eval gets to stop by itself before it is abandoned. */
export const CANCEL_GRACE_MS = 2000;

const ABANDONED =
  'Interrupted: the evaluation was abandoned. A promise it started can still run,' +
  ' and its next fetch, HTTP, pg or ioredis call fails.';

let engine = null;
let current = null;
let lastInterrupt = '';
let rawErr = null;

const FRAME = /^\s+at /;
// An error as text: the frames of the code, without the REPL's. The engine
// names its own frames (`internal`) and the frames that only follow the code
// (`outer`); `tidy` and `frame` rewrite the text and each kept frame.
export const errorText = (err) => {
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

// What an eval prints is part of its answer. Other output, for example from a
// timer that outlives the eval, goes to `outside`, or to the stream itself.
function capture(raw, outside) {
  return (chunk, encoding, done) => {
    const store = cancel.als.getStore();
    if (!store?.out || store.done) return (outside ?? raw)(chunk, encoding, done);
    store.out.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'));
    if (typeof encoding === 'function') encoding();
    else if (typeof done === 'function') done();
    return true;
  };
}

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
// `signals` says whether a SIGINT may break synchronous code: only in a process
// that the REPL owns.

// Node: one node:repl context keeps the declarations.
function nodeEngine(signals) {
  const server = repl.start({
    input: new PassThrough(),
    output: new PassThrough(),
    terminal: false,
    useGlobal: true,
    breakEvalOnSigint: signals,
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
      internal: /^\s+at (node:|.*(\(node:|driver\.mjs|core\.mjs))/,
      // The REPL's own frames, such as `new Promise (<anonymous>)`, follow the
      // last frame of the code; anonymous frames between code frames stay.
      outer: /^\s+at (node:|.*(\(node:|driver\.mjs|core\.mjs|\(<anonymous>\)$))/,
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
function bunEngine(signals) {
  // Bun's own console writes to the descriptors directly, past the capture.
  globalThis.console = new Console({ stdout: process.stdout, stderr: process.stderr, colorMode: false });
  globalThis.require = module.createRequire(path.join(process.cwd(), '<repl>'));
  const transpiler = new Bun.Transpiler({ loader: 'ts', replMode: true });
  const script = {
    filename: 'REPL',
    breakOnSigint: signals,
    importModuleDynamically: (specifier) => import(Bun.resolveSync(specifier, process.cwd())),
  };
  return {
    runtime: 'bun',
    version: process.versions.bun,
    frames: {
      // `REPL:l:c` names a line of the transpiled code, so only `REPL` stays, and
      // the bare `at REPL:l:c` frame is the wrapper that replMode adds.
      internal: /^\s+at (.*driver\.mjs|.*core\.mjs|.*\(node:|.*\(unknown\)$|REPL:\d+:\d+$)/,
      outer: /^\s+at (.*driver\.mjs|.*core\.mjs|.*\(node:|.*\(unknown\)$|.*\(native:|REPL:\d+:\d+$)/,
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

/** How an eval that was interrupted ended: at a refused I/O call, by itself, or abandoned. */
const ending = (store, abandoned) => (abandoned ? 'abandoned' : store.refused ? 'io' : 'stopped');

function evaluate(code) {
  return new Promise((resolve) => {
    const store = { out: [], done: false, cancelled: '', refused: '' };
    const finish = (res, abandoned = false) => {
      if (store.done) return;
      store.done = true;
      if (current?.store === store) current = null;
      const interrupted = store.cancelled ? ending(store, abandoned) : '';
      if (interrupted) lastInterrupt = interrupted;
      resolve({ ok: !res.exc && !res.syntax, out: store.out.join(''), ...res, ...(interrupted ? { interrupted } : {}) });
    };
    current = {
      store,
      abandon: () => finish({ exc: ABANDONED }, true),
      fail: (err) => finish({ exc: errorText(err) }),
    };
    lastInterrupt = '';
    cancel.hookLoaded();
    cancel.als.run(store, () => engine.run(code, finish));
  });
}

/** Cancel the running eval: its next I/O call fails, and after the grace it is
 * abandoned. Answers whether an eval was running. */
function interrupt(reason) {
  const running = current;
  if (!running) return false;
  if (!running.store.cancelled) {
    running.store.cancelled = reason;
    setTimeout(() => running.abandon(), CANCEL_GRACE_MS).unref?.();
  }
  return true;
}

/**
 * The evaluator of this process, made once. `signals` lets a SIGINT break
 * synchronous code; `outside` takes the output of no eval, else each stream
 * keeps its own.
 */
export function createRepl({ signals, outside = null }) {
  if (engine) throw new Error('the REPL evaluator is made once per process');
  rawErr = process.stderr.write.bind(process.stderr);
  const rawOut = process.stdout.write.bind(process.stdout);
  process.stdout.write = capture(rawOut, outside);
  process.stderr.write = capture(rawErr, outside);
  cancel.hookGlobals();
  engine = typeof Bun === 'undefined' ? nodeEngine(signals) : bunEngine(signals);
  return {
    runtime: engine.runtime,
    version: engine.version,
    evaluate,
    interrupt,
    get busy() { return current !== null; },
    get lastInterrupt() { return lastInterrupt; },
  };
}
