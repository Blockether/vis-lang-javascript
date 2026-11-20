# JavaScript toolchain reference

## Runtime

`js.run_tests` and `js.repl_start` take `runtime`: `"auto"` (the default), `"node"` or `"bun"`.
`"auto"` uses Bun when the project or its nearest parent up to the repository root has
`bun.lock`, `bun.lockb` or `"packageManager": "bun@…"` in `package.json`. Another lockfile or package
manager, or none, means Node. The tool raises `ToolMissing` when the runtime is not on `PATH`.

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

`js.repl_start` starts one `node` or `bun` process for each project directory. It needs Node 22.18
or Bun 1.4.5 or newer on `PATH`. `require()` and `import()` resolve from that directory. The process keeps running across
a sandbox restart. A live REPL is never replaced, because its state is your work. With
`runtime="auto"` a live REPL is kept on its runtime; a start on another named runtime is refused.

The REPL gets the environment variables that a `before` op hook on `js.repl_start` adds with
`vis.with_env`. A live REPL with other variables is refused.

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

`timeout_ms` sets how long `js.repl_eval` waits for the answer (30000 by default).

- Synchronous code, such as a busy loop, is interrupted. The REPL state stays.
- A promise that the code waits on is abandoned so that the REPL answers again. That promise can
  still run.

Evaluating without a live REPL raises `ReplError`, and so does code that does not stop.
