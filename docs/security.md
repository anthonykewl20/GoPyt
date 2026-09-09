# GoPyT v0 security

Peon local inference is narrowly extended by [the local runtime amendment](peon-runtime-amendment-2026-09-06.md).

Amended by [the implementation closure amendment](runtime-amendment-2026-09-05.md).

Status: normative. Security is **in the language and VM**, not a library an agent can forget.

This is not a claim that every business rule is safe. It is a claim that whole classes of agent-caused attacks **do not compile or do not run**.

## What the compiler/VM always enforces

| Attack class | Mechanism |
|--------------|-----------|
| Memory smash / buffer overflow in GoPyT | GC values, no pointers, no `unsafe`, overflow **traps** (S20) |
| `eval` / `exec` / shell / dynamic code | no such stdlib call, opcode, or app FFI; an undeclared call is E074 |
| SQL injection | no SQL; only `store.db` KV |
| Path traversal | normalized relative paths, no dot/backslash/empty segments or symlinks; package root only |
| Prototype pollution / `any` | no `any`; JSON extra fields → `ConvertError` |
| Hidden I/O | `fn` cannot touch the world; missing effect → `GOPYT_E050` |
| App-level FFI escape | `ffi` **only** in toolchain stdlib (`store.db`). App `effects { ffi }` → `GOPYT_E069`. Stdlib `ffi` does **not** propagate to callers. |
| Unlisted outbound hosts | `egress { }` allowlist; parsed request origin must match exactly |
| Secret leak via JSON/log | type `Secret`; cannot `Json`, cannot `core.log.write`, cannot `concat` to `str` |
| Supply chain | path deps + lock digest; no registry, no version ranges |
| Mass assignment | decode requires **exact** field set; extra or missing JSON key → `ConvertError` |

## `egress` (required for outbound HTTP)

If a module calls `net.http.request` or `core.model.complete`, spec **must** contain:

```
egress {
    "https://api.stripe.com",
    "https://models.example"
}
```

- Each string is an origin only: `https://host` with optional explicit port, or
  `http://127.0.0.1` / `http://localhost` with optional port. Userinfo, path,
  query, fragment, trailing slash, wildcard, and non-ASCII host are illegal.
- No `*` wildcards.
- Runtime parses and lowercases the ASCII DNS host, fills the default port
  (`443`/`80`), and requires exact `(scheme, host, port)` equality with an entry.
  String-prefix matching is forbidden.
- Literal URLs that do not match fail **check** (`GOPYT_E111 egress`).
- Inbound `serve` does not use `egress`.
- Omit `egress` when the module makes no outbound network/model call. An empty or
  unused egress block is E004; duplicate origins are E004.

## Secrets

```
module core.secret

use core.status { NotFound }
use core.str { len }

type Secret {
}

task get(name: str) -> Secret | NotFound
    effects { secret }
    requires core.str.len(name) > 0

task reveal(value: Secret) -> str
    effects { secret }
```

- `Secret` is opaque. User code cannot write `Secret { }`.
- `Secret` has no fields, equality, ordering, JSON implementation, string/bytes
  conversion, or pattern payload. It may be stored, passed, and returned.
- `get` reads env `GOPYT_SECRET_<NAME>` with `name` uppercased (`api_token` → `GOPYT_SECRET_API_TOKEN`). Not files in `spec/`/`impl/`.
- `reveal` explicitly declassifies to ordinary `str` and requires `secret`.
  Afterward the type system cannot prevent logging or JSON encoding; every call
  is security-sensitive and remains visible in source/effect audits.
- `data.json.encode` / `core.log.write` / `core.str.concat` do not accept `Secret` (`GOPYT_E112 secret_leak`).
- String literals that look like keys are **not** scanned. The type system is the scan.

## JSON

Canonical encoding (stdlib.md) plus:

- Unknown key → `ConvertError`
- Missing non-optional field → `ConvertError`
- `null` only for `T?`
- No `Secret` fields in JSON-facing types (check: `provide Json` cannot mention `Secret`)

## HTTP

- Client: URL scheme `https` or localhost `http` only, **and** `egress`.
- No redirects (implementer.md).
- Decode fail → 400 empty body (implementer.md).
- No HTML templates, no `eval` of query strings.

## Honesty bound

The language cannot prove “this payment is authorized” or prevent misuse after
explicit secret declassification. It can prove: no shell, no SQL string, no
`any`, no unlisted outbound origin, no app FFI, and no direct opaque `Secret` in
logs/JSON. A running VM cannot `eval` or hot-patch loaded bytecode (`GOPYT_E116`).
Self-evolution (`docs/evolve.md`) only installs a **new checked digest** for the
next process start, and cannot drop `egress` or add app `ffi`.

### Peon host development integration

[Delegation v2](peon-delegation-v2.md) adds host developer tooling around the existing
GoPyT compiler and VM. It adds no language syntax, native signatures, bytecode
version or GoPyT CLI commands. Model output remains subject to explicit grants
and real checks; local execution is not an adversarial OS sandbox.

## Delegated host resource authority

The [resource authority amendment](resource-authority-amendment-2026-09-09.md)
adds optional host-issued authority to the embedding VM. Grants attenuate and
revoke across task descendants; they intersect existing source egress and DB
operator policy. The trusted Python embedding boundary and unsupported companion
integrations are explicit in the amendment.

## Verified request sessions

The [request identity amendment](request-identity-amendment-2026-09-09.md) defines
operator provisioning, tenant-bound bearer sessions, exact route permission,
monotonic expiry and revocation. It specifies the strict TLS-gateway boundary,
identity-provider/MFA responsibilities, broker audience and process lifetime.
Unverified forwarding/tenant headers never substitute for a valid credential.
