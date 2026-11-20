# vis-lang-javascript

JavaScript and TypeScript on Node.js: test runs and a managed REPL. No parser of our own, no tree-sitter.

- `src/vis_lang_javascript/` is ordinary Python. Only `extension.py` mentions Vis.
- Return the contract types from `vis-lang-interface`. Do not make a second result shape.
- Run the project's own tools: its Jest or Vitest from `node_modules/.bin`, else `node --test`.
- `driver.mjs` is the eval server. It runs in the REPL's `node` and needs Node 22.18 or newer.
- `repl.py` owns one `node` process for each project directory, kept under a shell id across a sandbox restart. Never replace a live REPL, because its state is the user's work.
- Give every exported method an explicit Activity presentation with a capitalized English label.
- Format and lint with ruff. Run the tests with `uv run pytest tests -q`; they need `node` on PATH.
