# GoPyT v0 canonical formatter

Status: normative. `gopyt fmt` is the only source layout. `gopyt check` reports
`GOPYT_E004 unformatted` when formatting would change any source file.

## File and ordering

- UTF-8 without BOM, LF newlines, no trailing whitespace, exactly one final LF.
- Indentation is four ASCII spaces. Tabs are illegal.
- `module` is first. `use` declarations follow, sorted by module path using UTF-8
  byte order. Items retain source order. There is one blank line between these
  groups and between top-level items; no other blank lines.
- One `use` per module. Its names are de-duplicated and sorted: PascalCase names
  first, then snake_case names, UTF-8 byte order within each group.

## Delimiters

Inline lists use comma plus one space and never a trailing comma:

```
use core.status { IoError, NotFound }
fn write_pair(left: str, right: str) -> str
effects { network, database.write }
tasks { collect, refund }
map[K, V]
write_pair(left, right)
```

This applies to `use`, parameters, arguments, effects, agent task names, type
parameters, type arguments, and `egress` origin strings. Empty lists have no
interior space: `fn ping()`.

Newline-delimited record fields, enum variants/payload fields, record literal
fields, match pattern bindings, statements, match arms, and parallel arms never
use commas. Each occupies one line. This distinction is syntactic, not stylistic.

## Declarations and blocks

- Type, enum, trait, agent, HTTP, effects, tasks, match, if, for, and parallel
  opening braces remain on the header line.
- Function, task, workflow, provide, and test body braces are on their own lines.
- Fields and variants are declaration order. Effects are canonical S9 order,
  never alphabetical. Duplicate effects are checker errors, not silently removed.
- Agent task names and egress origins are sorted by UTF-8 byte order. Duplicate
  entries are checker errors.
- Contracts are emitted in source order after the effect clause, one per line.
- Bodies require explicit `return`, including `return unit`.

## Expressions

- One ASCII space surrounds binary operators, `=`, and `->`.
- Prefix `not` has one following space; unary `-` has none.
- No space precedes `(` in calls or `[` in type arguments.
- Parentheses are removed only when precedence in `grammar.ebnf` makes removal
  semantics-preserving. The formatter never reassociates expressions.
- String escapes are preserved if legal; the formatter does not change decoded
  string values.

## Comments

Only `//` comments exist. The formatter preserves their text and attachment,
removes trailing whitespace, and never turns prose into a contract or `open`.

## Idempotence

Formatting canonical input produces byte-identical output. Formatting twice must
equal formatting once. The formatter never renames identifiers, repairs types,
adds imports, changes algorithms, fills holes, or rewrites the lockfile.
