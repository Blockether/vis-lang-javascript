# vis-lang-javascript

JavaScript and TypeScript tools for [Vis](https://github.com/Blockether/vis) on Node.js: Jest,
Vitest and node:test runs and a managed project REPL. Tests and the REPL use your project's Node.

## Install

```bash
vis-agent extension install blockether/vis-lang-javascript --global --trust
```

Use Vis 0.5.2 or later. Put Node 22.18 or newer on `PATH`. Install Jest or Vitest in each project
that declares it in `package.json`. Projects without either run on node:test.

## Use

| Tool | Purpose |
| --- | --- |
| `js.run_tests` | Run Vitest, Jest or node:test. Set `keyword` to filter tests by name. |
| `js.repl_start` | Start a Node REPL for the project directory. |
| `js.repl_status` | Check if the project's REPL is active. |
| `js.repl_eval` | Evaluate JavaScript or TypeScript in the active REPL. Declarations stay between calls. |
| `js.repl_stop` | Stop the project's REPL. |

```python
js.run_tests(["src"], keyword="parser")
```

See the [JavaScript toolchain reference](docs/toolchain.md) for runner selection, TypeScript
handling and REPL behavior. Results follow the [language contract](https://github.com/Blockether/vis-lang-interface).

## Development

```bash
uv run pytest tests -q
```

The tests need `node` on `PATH`. Format and lint with `uv run ruff format` and `uv run ruff check`.
