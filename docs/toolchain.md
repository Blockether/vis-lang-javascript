# JavaScript toolchain reference

## Runtime

`js.run_tests` and `js.repl_start` take `runtime`: `"auto"` (the default), `"node"` or `"bun"`.
`"auto"` uses Bun when the project or its nearest parent up to the repository root has
`bun.lock`, `bun.lockb` or `"packageManager": "bun@…"` in `package.json`. Another lockfile or package
manager, or none, means Node. Bun must be on `PATH`.

For Node, the tools look for the nearest `.nvmrc` in the project directory or a parent. The
search stops at the repository root. If no `.nvmrc` exists, the tools look for `.node-version`
in the same directories. Blank lines and comments after `#` are ignored. The file can give a
full or partial version (`v24.11.0`, `24.11`, or `24`) or an nvm alias (`node`, `stable`,
`lts/*`, or a name in nvm's `alias` directory). A partial version selects the newest match.

The tools read the installed versions from `$NVM_DIR/versions/node`, or `~/.nvm/versions/node`
if `NVM_DIR` is not set. If a file requests a version that is not installed, the tools raise
`ToolMissing`. Run `nvm install` in the directory with `.nvmrc`, or give it the version from
`.node-version`. With no version file, the tools use Node on `PATH` or, if it is not there, the
nvm `default` alias. Node must be 22.18 or newer. The selected Node bin directory is first on
`PATH` for Node REPLs, Node tests, Jest, and Vitest.

## Test runs

`js.run_tests` uses the runner that the project declares in `package.json`:

1. Vitest, from the project's `node_modules/.bin`.
2. Jest, from the project's `node_modules/.bin`.
3. When `package.json` declares neither: bun test on Bun, node:test on Node.

A declared runner wins over `runtime`. Counts and failure locations come from the report that the
runner writes: JSON for Jest and Vitest, JUnit for bun test and node:test. bun test leaves a file
that fails to load out of its report, so that file is counted from its output as one failure. A directory given to node:test runs its `*.test.*` and `*.spec.*`
files.

- `keyword` runs only the tests whose name matches.
- `max_failures` stops Jest, Vitest and bun test after that many failures (`--bail`). node:test has no
  failure limit, so it runs every test.
- `timeout_s` abandons the run after that many seconds (900 by default).

The tool raises `ToolMissing` when a declared runner is not installed, and `ToolTimeout` when the
run outlives `timeout_s`.

## REPL

`js.repl_start` starts one `node` or `bun` process for each project directory. `require()` and
`import()` resolve from that directory. The process keeps running across a sandbox restart. A live
REPL is never replaced, because its state is your work. With `runtime="auto"` a live REPL keeps its
runtime. A start on another named runtime is refused. The status shows the live Node version and
its source. If the project now selects a different Node, the status tells you and keeps the live
REPL. Stop it with `js.repl_stop` before you start a REPL on the new version.

### Environment

The REPL process gets the variables of Vis, not those of a `.env` file: Node does not read `.env`
by itself, and Bun starts with `--no-env-file`. `env_file` names a dotenv file, relative to `cwd`, whose variables the REPL gets. Lines
are `NAME=value`, optionally after `export`, and variables are not expanded. `NODE_OPTIONS` and
`BUN_OPTIONS` from the file are skipped, because they would load other code into the REPL.

The variables that a `before` op hook on `js.repl_start` adds with `vis.with_env` win over the
file. A live REPL with other variables is refused.

### Evaluation

- TypeScript types are stripped before the code runs. Enums and namespaces are transformed.
- Declarations stay between calls, and top-level `await` works.
- Load modules with `require()` or `await import()`. On Node, a static `import` statement is
  refused.
- The value of the last expression comes back as `util.inspect` shows it, with the output of the
  code.
- Code that does not parse runs nothing.

### Bun

On Bun, `Bun.Transpiler` in `replMode` compiles the code, and `node:vm` runs it.

- A static `import` works, and `const` and `let` can be declared again.
- A declaration answers its value: `const n = 1` shows `1`.
- Error frames in the code show `REPL` without a line, because the lines are those of the
  compiled code.

### Timeouts

`timeout_ms` sets how long `js.repl_eval` waits for the answer (30000 by default). After the
timeout the code is interrupted, and the REPL state stays:

- Synchronous code, such as a busy loop, stops at once.
- Code that waits on I/O fails at its next `fetch`, `node:http`, `node:https`, `pg` or `ioredis`
  call. I/O that other code started goes on.
- A promise that makes no I/O call is abandoned after 2 seconds, so that the REPL answers again.
  It can still run, but its next I/O call fails.

A long loop without I/O can call `checkpoint()` to stop there. Evaluating without a live REPL
raises `ReplError`, and so does code that does not stop.

## NestJS

`js.repl_start(nest="auto")` loads a built NestJS app into the REPL. Build the app first, for
example with `nest build`. The REPL runs the built JavaScript, because Nest's dependency injection
needs the metadata that the TypeScript compiler emits.

- `"auto"` reads `entryFile` and `sourceRoot` from `nest-cli.json` and takes the newer of
  `dist/<entry>.js` and `dist/<sourceRoot>/<entry>.js`. Pass a path, relative to `cwd`, to choose.
- `"auto"` reads only the top-level keys of `nest-cli.json`. For an app under `projects`, pass its
  built entry, for example `nest="dist/worker/main.js"`.
- The entry runs up to its `NestFactory` call. The REPL makes an application context from the
  same root module. The app does not listen.
- `init` is `False` by default, so no lifecycle hook runs: no queue, schedule or connection that
  `onModuleInit` starts. Run `await app.init()` later, or start with `init=True`.
- `detail` says when a source in `sourceRoot` is newer than the build.

The REPL binds these names:

| Name | Value |
| --- | --- |
| `get`, `$`, `resolve`, `select`, `debug`, `methods`, `help` | Nest's own REPL functions. |
| Each provider, controller and module class | The class, by its name. |
| `svc(name)` | The provider or controller instance with that class name. A request-scoped or transient provider is refused: use `await resolve(X)`. |
| `services(regex)` | The names of the providers and controllers. |
| `load(path)` | `require` of a path in the build output, or of a package. |
| `app` | The application context. |
| `checkpoint()` | Stops the eval here when it was interrupted. |

A name that is already a global of the runtime is not bound over, and `detail` lists it.

A `.vis/js-repl.cjs` file in `cwd` or a parent up to the repository root adds project code. It can
export `before({ require, dist, cwd })`, which runs before the app loads, and
`bindings({ app, require, dist, cwd })`, which returns more names to bind.

A live REPL that runs another app, or no app, is refused.
