# GoPyT v0 lockfile and packages

Must follow: charter D8/D9/D10, `docs/spec.md` S18/S21/S22.

v0 has **no package registry**. Dependencies are path packages plus the compiler stdlib. One manifest, one lock, exact versions. No `^`, `~`, `*`, or “latest”.

## `gopyt.toml`

```
name = "billing"
version = "0.1.0"

[deps]
auth = { path = "../auth" }
```

Keys, in this order only:

| key | rule |
|-----|------|
| `name` | package `snake`, equals root module prefix convention (not enforced as folder name) |
| `version` | three decimal components `0..2147483647`, no leading zero except `0`, e.g. `0.1.0`; no `v`, prerelease, metadata, or range |
| `[deps]` | omitted if empty. Each key is the **import prefix** (`snake` or `module_path` first segment) |
| `path` | relative directory: either a descendant without `..`, or one sibling exactly `../<snake>`; no absolute path, URL, git source, symlink, or further `..` |

Illegal: `[dev-deps]`, `[features]`, version ranges, git/http sources, extra keys.
Comments, escapes in values, quoted dependency keys, duplicate keys/tables, and
noncanonical whitespace are also illegal; the manifest has exactly the forms
shown in this document. This is a deliberately closed TOML subset, not arbitrary
TOML accepted and later normalized.

The dep’s own `gopyt.toml` `name` must equal the table key, and its `version` is copied into the lock (the parent toml does not repeat version — path is identity).

Stdlib (`core.*`, `net.http`, `data.json`, `store.db`) is **not** listed in `[deps]`.

## `gopyt.lock`

Compiler-owned. Committed. TOML. Canonical order.

```
toolchain = "gopyt-0.1.000"

[[pkg]]
name = "billing"
version = "0.1.0"
path = "."
digest = "sha256:…"

[[pkg]]
name = "auth"
version = "0.2.0"
path = "../auth"
digest = "sha256:…"
```

| field | rule |
|-------|------|
| `toolchain` | exact compiler id; mismatch = `GOPYT_E040 toolchain` |
| `[[pkg]]` | root first, then deps sorted by `name` |
| `digest` | sha256 of the package’s **canonical bytes** (below) |

No `[metadata]`, no timestamps (they break RP-002).

## Digest

For each package, create a preimage from `gopyt.toml`, then every regular file in
`spec/`, `impl/`, and `test/`. Paths are relative to the package root, use `/`,
and are sorted by raw UTF-8 bytes. Symlinks and non-regular entries in those trees
are `GOPYT_E046`.

**2026-09-05 security amendment:** CR and LF are forbidden in every path
component beneath `spec/`, `impl/`, and `test/`, including non-`.gopyt` files and
directories (`GOPYT_E046`). Paths delimit digest records with LF. Allowing LF in
a filename would let one path impersonate multiple entries and give different
trees identical preimages. This restriction preserves the digest format and all
ordinary existing package digests; it does not change SHA-256.

For every entry, append exactly:

```
<path>\n
sha256:<lowercase hex sha256 of exact file bytes>\n
```

Then SHA-256 the complete preimage. The lock value is `sha256:` plus its lowercase
hex digest. There is no BOM, length prefix, platform path separator, or extra
blank line. Canonical source bytes are enforced by `gopyt check`; the manifest
canonical form is the key order shown above, dependency keys sorted by UTF-8 byte
order, one LF per line, one blank line before `[deps]`, and one final LF.

Changing a comment in `impl/` changes the digest. That is intended.

## Who writes the lock

`gopyt check`:

- computes the would-be lock
- if `gopyt.lock` missing or different → **does not write** (same policy as impl repairs)
- diagnostic `GOPYT_E041 lock_stale` with the **full canonical lock text** as the one repair
- agent/CI writes that text, then check passes

`fmt` / `run` / `test` do not invent a second lock tool. `test` and `run` require a matching lock (they run `check` first, S18).

## Graph rules

- Cycles: error `GOPYT_E042 dep_cycle`
- Missing path: `GOPYT_E043 dep_missing`
- Name mismatch: `GOPYT_E044 dep_name`
- Two packages anywhere in the transitive graph with the same `name` but different
  canonical paths: `GOPYT_E045 dep_dup`
- Import `use auth.foo` requires `[deps] auth = { path = ... }` unless `auth` is this package’s own module prefix or stdlib `core`/`net`/`data`/`store`

Every module in a dependency package has that dependency's `name` as its first
module-path segment. A dependency named `auth` may expose `auth` and `auth.token`,
not `token`. Root-package modules may use any non-stdlib prefix except a declared
dependency name. Imports are direct: a transitive package must also appear in the
root `[deps]` before root code can import it.

Dependencies are resolved transitively. Canonical paths are lexical normalized
paths after applying the restricted forms above; symlink resolution is forbidden.
The lock contains root first, then every transitive dependency sorted by package
name. v0 stdlib prefixes are `core`, `net`, `data`, `store` only (S21).

## Reproducibility

Same `spec/` + `impl/` + `test/` + `gopyt.toml` + `gopyt.lock` + toolchain → same digest and same `.gobyte`. If an agent regenerates impl holes differently, the lock digest changes and CI shows a lock/spec review on **spec** plus a digest change — not a style debate.

The [exact toolchain amendment](toolchain-compatibility-amendment-2026-09-09.md)
extends the compiler ID shown above with a runtime-source SHA-256 fingerprint.
Legacy identifiers require an explicitly reviewed E040 repair and recompilation.
