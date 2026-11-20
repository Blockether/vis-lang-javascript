// The NestJS profile of the REPL. It finds a built Nest application's root
// module, makes its application context, and binds the REPL functions of Nest
// itself (get, $, resolve, select, debug, methods, help), every provider,
// controller and module class by name, and svc, services, load, app and
// checkpoint. A project adds its own through `.vis/js-repl.cjs`.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const cancel = require('./cancel.cjs');

const FACTORY = ['create', 'createApplicationContext'];
const ENTRY_WAIT_MS = 30000;
const HOOK_FILE = path.join('.vis', 'js-repl.cjs');

/** The `.vis/js-repl.cjs` of `cwd` or of a parent up to the repository root, loaded, else {}. */
function projectHooks(cwd) {
  for (let dir = path.resolve(cwd); ; dir = path.dirname(dir)) {
    const file = path.join(dir, HOOK_FILE);
    if (fs.existsSync(file)) return { path: file, ...require(file) };
    if (fs.existsSync(path.join(dir, '.git')) || path.dirname(dir) === dir) return {};
  }
}

/** The output directory of `entry`: its nearest `dist` parent, else its own directory. */
function distOf(entry) {
  for (let dir = path.dirname(entry); path.dirname(dir) !== dir; dir = path.dirname(dir)) {
    if (path.basename(dir) === 'dist') return dir;
  }
  return path.dirname(entry);
}

// The options of NestFactory.create, not its HTTP adapter.
const isOptions = (value) =>
  value !== null && typeof value === 'object' && typeof value.getHttpServer !== 'function' && typeof value.listen !== 'function';

/**
 * The module and options that `entry` passes to NestFactory. The entry runs up
 * to that call; its bootstrap then waits forever, so it never listens.
 */
async function rootModule(entry) {
  const { NestFactory } = createRequire(entry)('@nestjs/core');
  const saved = FACTORY.map((name) => [name, Object.getOwnPropertyDescriptor(NestFactory, name)]);
  const restore = () => {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(NestFactory, name, descriptor);
      else delete NestFactory[name];
    }
  };
  let asked;
  const called = new Promise((resolve) => (asked = resolve));
  for (const name of FACTORY) {
    NestFactory[name] = (module, ...rest) => {
      restore();
      asked({ module, options: rest.find(isOptions) ?? {} });
      return new Promise(() => {});
    };
  }
  let timer;
  try {
    const ran = import(pathToFileURL(entry).href).then(
      () => new Promise((resolve) => (timer = setTimeout(resolve, ENTRY_WAIT_MS, null))),
    );
    const found = await Promise.race([called, ran]);
    if (!found) {
      throw new Error(`${entry} did not call NestFactory.create or createApplicationContext within ${ENTRY_WAIT_MS / 1000} s`);
    }
    return { ...found, NestFactory };
  } finally {
    clearTimeout(timer);
    restore();
  }
}

/** Provider and controller wrappers by class name, across every module. */
function wrappers(app) {
  const index = new Map();
  for (const moduleRef of app.container.getModules().values()) {
    for (const wrapper of [...moduleRef.providers.values(), ...moduleRef.controllers.values()]) {
      // A class or string token names the provider; a symbol or a factory's own
      // name does not.
      const { token } = wrapper;
      const name = typeof token === 'function' ? token.name : typeof token === 'string' ? token : '';
      if (name && wrapper.instance && !index.has(name)) index.set(name, wrapper);
    }
  }
  return index;
}

/** Nest's own REPL scope for `app`, or {} for a Nest without one. */
function nestScope(app, entry) {
  try {
    const core = createRequire(createRequire(entry).resolve('@nestjs/core'));
    const { ReplContext } = core('./repl/repl-context.js');
    return new ReplContext(app).globalScope;
  } catch {
    return {};
  }
}

const ours = new Set();

/**
 * Binds the REPL names for `app` as globals. A name that is already a global of
 * the runtime stays as it is. Answers what was bound and what was skipped.
 */
function expose(app, { entry, dist, hooks }) {
  const entryRequire = createRequire(entry);
  const own = {
    app,
    svc(name) {
      const wrapper = wrappers(app).get(name);
      if (!wrapper) throw new Error(`svc: no provider or controller named ${name}; services() lists them`);
      // A request-scoped or transient wrapper holds only a prototype, not an instance.
      if (wrapper.isTransient || !wrapper.isDependencyTreeStatic()) {
        const token = typeof wrapper.token === 'string' ? JSON.stringify(name) : name;
        const scope = wrapper.isTransient ? 'transient' : 'request-scoped';
        throw new Error(`svc: ${name} is ${scope}; use await resolve(${token})`);
      }
      return wrapper.instance;
    },
    services: (pattern) => [...wrappers(app).keys()].filter((name) => !pattern || pattern.test(name)).sort(),
    load: (specifier) => entryRequire(/^\.\.?([\\/]|$)/.test(specifier) ? path.resolve(dist, specifier) : specifier),
    checkpoint: cancel.checkpoint,
  };
  const extra = hooks.bindings?.({ app, require: entryRequire, dist, cwd: process.cwd() }) ?? {};
  const bound = new Set();
  const skipped = [];
  for (const source of [nestScope(app, entry), own, extra]) {
    for (const name of Object.keys(source)) {
      if (name in globalThis && !ours.has(name)) {
        skipped.push(name);
        continue;
      }
      const descriptor = { ...Object.getOwnPropertyDescriptor(source, name), configurable: true, enumerable: false };
      if ('value' in descriptor) descriptor.writable = true;
      Object.defineProperty(globalThis, name, descriptor);
      ours.add(name);
      bound.add(name);
    }
  }
  return { bound: bound.size, skipped: [...new Set(skipped)].sort(), hooks: hooks.path ?? '' };
}

/**
 * The application context of `module`. NestFactory.createApplicationContext
 * ends with init(); without `init` that call returns at once, and the app's
 * lifecycle hooks run only on a later `await app.init()`.
 */
async function context(entry, NestFactory, module, options, init) {
  const { NestApplicationContext } = createRequire(entry)('@nestjs/core');
  const proto = NestApplicationContext.prototype;
  const own = Object.getOwnPropertyDescriptor(proto, 'init');
  if (!init) {
    proto.init = async function init() {
      return this;
    };
  }
  try {
    return await NestFactory.createApplicationContext(module, { ...options, abortOnError: false });
  } finally {
    Object.defineProperty(proto, 'init', own);
  }
}

/** Start the Nest profile of the managed REPL from its profile.json. */
async function start({ entry, init, dist = distOf(entry) }) {
  process.env.NO_COLOR ??= '1';
  const hooks = projectHooks(process.cwd());
  await hooks.before?.({ require: createRequire(entry), dist, cwd: process.cwd() });
  const { module, options, NestFactory } = await rootModule(entry);
  const app = await context(entry, NestFactory, module, options, init);
  // The whole dependency graph is too large to show as an eval's value.
  const name = module?.name ?? module?.module?.name ?? String(module);
  Object.defineProperty(Object.getPrototypeOf(app), Symbol.for('nodejs.util.inspect.custom'), {
    value: () => `NestApplicationContext <${name}>`,
    configurable: true,
  });
  return { kind: 'nest', module: name, init: Boolean(init), ...expose(app, { entry, dist, hooks }) };
}

module.exports = { start, expose, projectHooks, distOf };
