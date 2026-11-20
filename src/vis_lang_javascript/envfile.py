"""Variables from a dotenv file, for the REPL's process.

A line is `NAME=value`, optionally after `export `. A value in single quotes is
taken as written; in double quotes, `\\n` becomes a newline. A bare value ends at
` #`. Variables are not expanded.
"""

from __future__ import annotations

import re
from pathlib import Path

SKIPPED = ("NODE_OPTIONS", "BUN_OPTIONS")
"""Names a file does not set: the REPL starts its runtime with its own options,
and NODE_OPTIONS on Node or BUN_OPTIONS on Bun would load the code that the file
names into it."""

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _value(raw):
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end > 0:
            text = raw[1:end]
            return text.replace("\\n", "\n") if raw[0] == '"' else text
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def read(path):
    """The variables of the file at `path`, and the names that were skipped."""
    found, skipped = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        name, raw = match.groups()
        if name in SKIPPED:
            skipped.append(name)
            continue
        found[name] = _value(raw.strip())
    return found, skipped
