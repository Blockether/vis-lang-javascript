# vis-lang-javascript

JavaScript and TypeScript tools for [Vis](https://github.com/Blockether/vis) on Node.js: test runs
and a REPL that keeps its state between calls. Node 22.18 or newer must be on `PATH`.

## Tools

```python
js.run_tests(["src"], keyword="parser")        # Vitest or Jest from package.json, else node:test
js.run_tests(["src/a.spec.ts"], max_failures=1)
js.repl_start(cwd="~/app")                     # one node process for the directory
js.repl_eval("const n: number = 2", cwd="~/app")
js.repl_eval("await import('./lib.ts')", cwd="~/app")
js.repl_stop(cwd="~/app")
```

The REPL strips TypeScript types before the code runs, and transforms enums and namespaces.
Declarations stay between calls and top-level await works. Load modules with `require()` or
`await import()`. A timeout stops a busy loop, or abandons a promise that never settles.

Test counts come from the runner's own report: JSON for Jest and Vitest, JUnit for node:test.
