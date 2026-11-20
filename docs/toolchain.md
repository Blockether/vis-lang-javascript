# JavaScript toolchain reference

## Test runs

`js.run_tests` uses the runner that the project declares in `package.json`:

1. Vitest, from the project's `node_modules/.bin`.
2. Jest, from the project's `node_modules/.bin`.
3. node:test, when `package.json` declares neither.

Counts and failure locations come from the report that the runner writes: JSON for Jest and
Vitest, JUnit for node:test. A directory given to node:test runs its `*.test.*` and `*.spec.*`
files.

- `keyword` runs only the tests whose name matches.
- `max_failures` stops Jest and Vitest after that many failures (`--bail`). node:test has no
  failure limit, so it runs every test.
- `timeout_s` abandons the run after that many seconds (900 by default).

The tool raises `ToolMissing` when a declared runner is not installed, and `ToolTimeout` when the
run outlives `timeout_s`.

## REPL

`js.repl_start` starts one `node` process for each project directory. It needs Node 22.18 or newer
on `PATH`. `require()` and `import()` resolve from that directory. The process keeps running across
a sandbox restart. A live REPL is never replaced, because its state is your work.

The REPL gets the environment variables that a `before` op hook on `js.repl_start` adds with
`vis.with_env`. A live REPL with other variables is refused.

### Evaluation

- TypeScript types are stripped before the code runs. Enums and namespaces are transformed.
- Declarations stay between calls, and top-level `await` works.
- Load modules with `require()` or `await import()`. A static `import` statement is refused.
- The value of the last expression comes back as `util.inspect` shows it, with the output of the
  code.
- Code that does not parse runs nothing.

### Timeouts

`timeout_ms` sets how long `js.repl_eval` waits for the answer (30000 by default).

- Synchronous code, such as a busy loop, is interrupted. The REPL state stays.
- A promise that the code waits on is abandoned so that the REPL answers again. That promise can
  still run.

Evaluating without a live REPL raises `ReplError`, and so does code that does not stop.
