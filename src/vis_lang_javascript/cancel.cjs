// Cooperative interruption of an eval. Each eval runs in one AsyncLocalStorage
// store. Once the eval is cancelled, its next I/O call fails: fetch, node:http
// and node:https, a pg query or an ioredis command. A call fails through its
// own promise, callback or 'error' event, never by a synchronous throw:
// libraries also start I/O from timers and socket events, where a throw would
// stop the process. Only calls made inside the eval's store are refused, so I/O
// that a library batches for other work goes on.
'use strict';
const { AsyncLocalStorage } = require('node:async_hooks');
const http = require('node:http');
const https = require('node:https');

/** Store: { out: string[], done: boolean, cancelled: string, refused: string } */
const als = new AsyncLocalStorage();

/** The error for a `what` call inside a cancelled eval, or undefined. */
function refusal(what) {
  const store = als.getStore();
  if (!store?.cancelled) return undefined;
  store.refused ||= what;
  return Object.assign(new Error(`Interrupted: the eval was cancelled, so its ${what} call was refused.`), {
    code: 'VIS_REPL_CANCELLED',
  });
}

/** Throws when the running eval is cancelled; a long loop without I/O can call it. */
function checkpoint() {
  const error = refusal('checkpoint()');
  if (error) throw error;
}

const HOOKED = Symbol.for('vis.repl.cancel');

function wrap(owner, name, makeWrapper) {
  const original = owner?.[name];
  if (typeof original !== 'function' || original[HOOKED]) return;
  const wrapped = makeWrapper(original);
  wrapped[HOOKED] = true;
  owner[name] = wrapped;
}

/** fetch, node:http and node:https, hooked once per process. */
function hookGlobals() {
  wrap(globalThis, 'fetch', (original) =>
    function fetch(...args) {
      const error = refusal('fetch');
      return error ? Promise.reject(error) : original.apply(this, args);
    },
  );
  for (const [lib, what] of [[http, 'HTTP request'], [https, 'HTTPS request']]) {
    for (const name of ['request', 'get']) {
      wrap(lib, name, (original) =>
        function request(...args) {
          const req = original.apply(this, args);
          const error = refusal(what);
          if (error) req.destroy(error);
          return req;
        },
      );
    }
  }
}

function hookPg(pg) {
  wrap(pg?.Client?.prototype, 'query', (original) =>
    function query(...args) {
      // A submittable (cursor, stream) reports through its own events; it runs.
      const error = typeof args[0]?.submit === 'function' ? undefined : refusal('pg query');
      if (!error) return original.apply(this, args);
      const callback = [...args].reverse().find((arg) => typeof arg === 'function') ?? args[0]?.callback;
      if (typeof callback !== 'function') return Promise.reject(error);
      process.nextTick(callback, error);
      return undefined;
    },
  );
}

function hookRedis(ioredis) {
  for (const Client of [ioredis?.default ?? ioredis, ioredis?.Cluster]) {
    // A command sent on its own is refused the way ioredis refuses one on a
    // closed connection. With a stream it is part of a pipeline flush, which
    // must write every queued command.
    wrap(Client?.prototype, 'sendCommand', (original) =>
      function sendCommand(command, stream, ...rest) {
        const error = stream ? undefined : refusal('ioredis command');
        if (!error) return original.call(this, command, stream, ...rest);
        command.reject(error);
        return command.promise;
      },
    );
  }
}

function hookAutoPipelining(autoPipelining) {
  // An auto-pipelined command is refused before it joins the batch that other
  // callers share.
  wrap(autoPipelining, 'executeWithAutoPipelining', (original) =>
    function executeWithAutoPipelining(client, functionName, commandName, args, callback) {
      const error = refusal('ioredis command');
      if (!error) return original.call(this, client, functionName, commandName, args, callback);
      const failed = Promise.reject(error);
      if (typeof callback === 'function') failed.catch(callback);
      return failed;
    },
  );
}

const LIBRARIES = [
  [/[\\/]node_modules[\\/]pg[\\/]lib[\\/]index\.js$/, hookPg],
  [/[\\/]node_modules[\\/]ioredis[\\/]built[\\/]index\.js$/, hookRedis],
  [/[\\/]node_modules[\\/]ioredis[\\/]built[\\/]autoPipelining\.js$/, hookAutoPipelining],
];
const seen = new Set();

/** Hooks every copy of pg and ioredis that is loaded now; pnpm can hold several. */
function hookLoaded() {
  for (const [filename, loaded] of Object.entries(require.cache)) {
    if (seen.has(filename) || !loaded?.loaded) continue;
    const library = LIBRARIES.find(([pattern]) => pattern.test(filename));
    if (!library) continue;
    seen.add(filename);
    try {
      library[1](loaded.exports);
    } catch (error) {
      process.stderr.write(`[vis repl] ${filename} not hooked: ${error?.message ?? error}\n`);
    }
  }
}

module.exports = { als, refusal, checkpoint, hookGlobals, hookLoaded };
