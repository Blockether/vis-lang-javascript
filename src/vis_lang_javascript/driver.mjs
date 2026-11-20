// The eval server of a managed JavaScript REPL. One JSON request per line on
// descriptor 0, one JSON answer per line on descriptor 1. Code runs in one
// node:repl context, so declarations live between evaluations.
import fs from 'node:fs';
import path from 'node:path';
import repl from 'node:repl';
import util from 'node:util';
import domain from 'node:domain';
import readline from 'node:readline';
import { PassThrough } from 'node:stream';
import { Worker } from 'node:worker_threads';
import { stripTypeScriptTypes } from 'node:module';

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

const server = repl.start({
  input: new PassThrough(),
  output: new PassThrough(),
  terminal: false,
  useGlobal: true,
  breakEvalOnSigint: true,
  prompt: '',
});

const errorText = (err) => {
  if (err instanceof Error) {
    const lines = util.inspect(err, { depth: 3 }).split('\n');
    return lines.filter((line, i) => i === 0 || !/^\s+at (node:|.*(\(node:|driver\.mjs))/.test(line)).join('\n');
  }
  return 'Uncaught ' + util.inspect(err, { depth: 3 });
};
const shown = (value) =>
  value === undefined ? '' : util.inspect(value, { depth: 4, breakLength: 100, maxArrayLength: 100, maxStringLength: 10000 });

// TypeScript loses its types before it runs; plain JavaScript passes unchanged.
function compiled(code) {
  try {
    return { code: stripTypeScriptTypes(code) };
  } catch (err) {
    if (err?.code === 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX') {
      try {
        return { code: stripTypeScriptTypes(code, { mode: 'transform' }) };
      } catch (again) {
        err = again;
      }
    }
    return { syntax: String(err?.message ?? err).trim() };
  }
}

let current = null;

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
    const ready = compiled(code);
    if (ready.syntax) return finish({ syntax: ready.syntax });
    const guard = domain.create();
    guard.on('error', (err) => finish({ exc: errorText(err) }));
    // Node's SIGINT watchdog breaks the synchronous part of the code only when
    // the process has no SIGINT listener, so the listener waits outside it.
    process.removeListener('SIGINT', onInterrupt);
    try {
      guard.run(() =>
        server.eval(ready.code, server.context, 'REPL', (err, value) => {
          if (err instanceof repl.Recoverable) return finish({ syntax: 'The code is incomplete: ' + String(err.err?.message ?? err.message) });
          if (err) return finish({ exc: errorText(err) });
          let text;
          try { text = shown(value); } catch (e) { text = '<value could not be shown: ' + e + '>'; }
          finish({ value: text, type: value?.constructor?.name ?? typeof value });
        }),
      );
    } finally {
      process.on('SIGINT', onInterrupt);
    }
  });
}

// A SIGINT stops running synchronous code (breakEvalOnSigint). For code that
// waits on a promise, the eval is abandoned so the REPL answers again.
function onInterrupt() {
  current?.abandon();
}
process.on('SIGINT', onInterrupt);
process.on('uncaughtException', (err) => rawErr('uncaught: ' + errorText(err) + '\n'));
// Before Node 25, the REPL binds its eval to a domain of its own, and an error
// of the code reaches that domain instead of the eval's callback.
server._domain?.on('error', (err) => {
  if (current) current.fail(err);
  else rawErr('uncaught: ' + errorText(err) + '\n');
});
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
  if (req.op === 'ping') return answer({ id: req.id, ok: true, pong: current === null, node: process.versions.node });
  queue = queue.then(async () => answer({ id: req.id, ...(await evaluate(String(req.code ?? ''))) }));
});
lines.on('close', () => process.exit(0));
