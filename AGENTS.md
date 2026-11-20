# vis-lang-javascript

JavaScript and TypeScript on Node.js and Bun: test runs and a managed REPL. No parser of our own, no tree-sitter.

- `src/vis_lang_javascript/` is ordinary Python. Only `extension.py` mentions Vis.
- Return the contract types from `vis-lang-interface`. Do not make a second result shape.
- Run the project's own tools: its Jest or Vitest from `node_modules/.bin`, else `bun test` on Bun or `node --test` on Node. A declared runner wins over the runtime.
- `runtimes.py` picks the runtime: Bun for `bun.lock`, `bun.lockb` or `"packageManager": "bun@…"`, else Node.
- `driver.mjs` is the eval server for both runtimes, with one protocol and one answer shape. It needs Node 22.18 or Bun 1.4.5 or newer. On Bun, code goes through `Bun.Transpiler` (`replMode`) and `vm.runInThisContext`.
- `repl.py` owns one `node` or `bun` process for each project directory, kept under a shell id across a sandbox restart. Never replace a live REPL, because its state is the user's work.
- Give every exported method an explicit Activity presentation with a capitalized English label.
- Format and lint with ruff. Run the tests with `uv run pytest tests -q`; a test that needs `node` or `bun` is skipped without it.
