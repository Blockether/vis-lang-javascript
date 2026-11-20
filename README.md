# vis-lang-javascript

JavaScript and TypeScript tools for [Vis](https://github.com/Blockether/vis) on Node.js and Bun:
Jest, Vitest, bun test and node:test runs and a managed project REPL. Tests and the REPL use your
project's runtime: Bun when the project has a Bun lockfile or `"packageManager": "bun@…"`, else Node.

## Install

```bash
vis-agent extension install blockether/vis-lang-javascript --global --trust
```

Use Vis 0.5.2 or later. Put Node 22.18 or newer, Bun 1.4.5 or newer, or both on `PATH`. Install
Jest or Vitest in each project that declares it in `package.json`. Projects without either run on
bun test or node:test.

## Use

| Tool | Purpose |
| --- | --- |
| `js.run_tests` | Run Vitest, Jest, bun test or node:test. Set `keyword` to filter tests by name. |
| `js.repl_start` | Start a Node or Bun REPL for the project directory. Set `runtime` to choose, `nest` to load a built NestJS app and `env_file` to pass a dotenv file. |
| `js.repl_status` | Check if the project's REPL is active. |
| `js.repl_eval` | Evaluate JavaScript or TypeScript in the active REPL. Declarations stay between calls. |
| `js.repl_stop` | Stop the project's REPL. |

```python
js.run_tests(["src"], keyword="parser")
js.repl_start(cwd="apps/api", nest="auto", env_file="../../.env")
js.repl_eval("await svc('UsersService').findOne(1)", cwd="apps/api")
```

See the [JavaScript toolchain reference](docs/toolchain.md) for runner selection, TypeScript
handling and REPL behavior. Results follow the [language contract](https://github.com/Blockether/vis-lang-interface).

## Development

```bash
npm ci --prefix tests/fixtures/nest-app
uv run pytest tests -q
```

A test that needs `node` or `bun` on `PATH` is skipped without it. The NestJS tests are skipped
without the fixture's `node_modules`. Format and lint with `uv run ruff format` and `uv run ruff check`.
