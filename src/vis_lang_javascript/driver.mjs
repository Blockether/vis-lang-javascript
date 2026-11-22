// The eval server of a managed JavaScript REPL, on Node.js or Bun. One JSON
// request per line on descriptor 0, one JSON answer per line on descriptor 1.
// The evaluator is core.mjs; a profile.json next to this file can ask for the
// NestJS profile (nest.cjs), which the `setup` op answers.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { Worker } from 'node:worker_threads';
import module from 'node:module';
import { createRepl, errorText } from './core.mjs';

const require = module.createRequire(import.meta.url);
const answer = (res) => fs.writeSync(1, JSON.stringify(res) + '\n');
const rawErr = process.stderr.write.bind(process.stderr);
// Descriptor 1 carries the answers, so output outside an eval goes to the log.
const repl = createRepl({ signals: true, outside: rawErr });

const place = process.env.VIS_LANG_RENDEZVOUS;
const profileFile = place && path.join(place, 'profile.json');
const profile = profileFile && fs.existsSync(profileFile) ? JSON.parse(fs.readFileSync(profileFile, 'utf8')) : {};
const setup = profile.kind === 'nest' ? require('./nest.cjs').start(profile) : Promise.resolve({ kind: 'plain' });
setup.catch(() => {});

// Node's SIGINT watchdog breaks the synchronous part of the code only when the
// process has no SIGINT listener, so the listener waits outside it.
function evaluate(code) {
  process.removeListener('SIGINT', onInterrupt);
  try {
    return repl.evaluate(code);
  } finally {
    process.on('SIGINT', onInterrupt);
  }
}

// A SIGINT stops running synchronous code (breakEvalOnSigint, breakOnSigint).
// Code that waits on a promise is cancelled: its next I/O call fails, and after
// a grace it is abandoned, so the REPL answers again.
function onInterrupt() {
  repl.interrupt('interrupted');
}
process.on('SIGINT', onInterrupt);
process.on('uncaughtException', (err) => rawErr('uncaught: ' + errorText(err) + '\n'));
process.on('unhandledRejection', (err) => rawErr('unhandled rejection: ' + errorText(err) + '\n'));

// One byte on the interrupt FIFO asks to stop the running eval. A worker reads
// it, because the main thread can be held by the code itself.
const stop = place && path.join(place, 'interrupt');
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

let queue = setup.catch(() => {});
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on('line', (line) => {
  let req;
  try { req = JSON.parse(line); } catch (err) { return answer({ ok: false, exc: 'bad request: ' + err.message }); }
  if (req.op === 'ping') {
    return answer({ id: req.id, ok: true, pong: !repl.busy, runtime: repl.runtime, version: repl.version, binary: process.execPath, interrupted: repl.lastInterrupt });
  }
  if (req.op === 'setup') {
    return setup.then(
      (res) => answer({ id: req.id, ok: true, ...res }),
      (err) => answer({ id: req.id, ok: false, exc: errorText(err) }),
    );
  }
  queue = queue.then(async () => answer({ id: req.id, ...(await evaluate(String(req.code ?? ''))) }));
});
lines.on('close', () => process.exit(0));
