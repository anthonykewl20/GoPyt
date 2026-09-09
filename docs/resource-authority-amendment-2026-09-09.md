# Delegated resource authority: 2026-09-09

This normative host-runtime amendment adds `gopyt.resource_authority` and an
optional `authority` argument to the Python embedding `VM` constructor. It does
not add GoPyT syntax, callable signatures, language values, bytecode fields,
compiler flags, or a second effect system. It advances milestone issue #4.

## Ownership and use

The trusted embedding host issues a root `ResourceAuthority` and supplies it to
`VM(artifact, package_root, authority=root)`. The host may delegate a subset of
that root and use `with vm.authority_scope(child):` around one call tree.
Delegation and revocation are host APIs. Handles are not GoPyT records, strings,
serialized credentials, artifact constants, or application-controlled policy.
A dictionary, `None`, unrelated root, or sibling cannot replace the authority of
an already restricted scope. The VM accepts only actual host handles.

```python
from gopyt.cli import build
from gopyt.resource_authority import ResourceAuthority
from gopyt.vm import VM

_, artifact, functions = build('/srv/orders')
service = ResourceAuthority.issue(
    database_read=['tenant/'], database_write=['tenant/'],
    file_read=['exports/'], network=['https://payments.example:443'])
vm = VM(artifact, '/srv/orders', authority=service)
customer = service.delegate(database_read=['tenant/customer/'])
with vm.authority_scope(customer):
    result = vm.call(functions['orders.lookup'], ['tenant/customer/order'])
customer.revoke()
```

The host authenticates a caller and selects its handle before entering this
scope. A namespace string alone is not authenticated user identity. The example
assumes an application exposing `orders.lookup`; it illustrates embedding, not
a deployed identity service. Issue #10 still covers identity and authorization.

Omitting `authority` preserves the existing v0 embedding and CLI behavior. The
CLI does not select a delegated authority from application files or ambient
application strings. Deployments needing these restrictions must use the host
embedding boundary and retain its root handles for administrative revocation.
The existing operator DB policy remains an additional restriction.

## Closed grant vocabulary

Unspecified rights deny access. Each right accepts a list/tuple of at most 256
UTF-8 grants, each at most 1024 bytes. A standalone `*` grants every resource in
that category; there are no partial wildcards, regexes, DNS aliases, IP ranges,
or URL-path grants. Other categories and malformed entries are errors.

| Right | Grant and matching rule | Native admission |
| --- | --- | --- |
| `database_read` | Namespace prefix ending in `/`, or `*`; exact string prefix | `get`, `get_many`, both compare-exchange forms |
| `database_write` | Same namespace rules | `put`, both compare-exchange forms |
| `file_read` | Exact relative file path, directory prefix ending in `/`, or `*` | `core.file.read` |
| `file_write` | Same path rules | `core.file.write` |
| `network` | Canonical `scheme://host:port` origin, or `*` | `net.http.request`, `core.model.complete` |
| `listen` | Exact configured `host:port`, or `*` | `net.http.serve` |
| `secrets` | Exact source-style secret name, or `*` | `core.secret.get` |

File grants use the existing package-relative path grammar; they never authorize
traversal, protected source/artifact writes, symlinks or hardlinks refused by the
underlying native. A directory prefix includes its slash, so `data/a/` does not
grant `data/ab/`. An exact file grant does not grant descendants. Database keys
retain their existing opaque-string semantics; namespace grants include their
slash so `tenant/a/` cannot admit `tenant/ab/key`.

Origins use the compiler's existing normalizer and must already be canonical
(e.g. `https://payments.example:443`). An operator grant is intersected with the
calling module's declared `egress`. Neither declaration alone admits a request.
The HTTP listener's existing address and security-profile checks still apply.

## Delegation and revocation decisions

1. A child can only retain or narrow grants from its immediate parent. Omitted
   categories are denied, not implicitly inherited. Sibling roots with identical
   text do not constitute a delegation relationship. Maximum depth is 64.
2. Parent and descendants share one admission/revocation lock. Each admission
   checks every ancestor's revocation state and every requested resource under
   that lock. An atomic DB batch is admitted once for all keys and both rights;
   there is no partially authorized sub-batch.
3. Revoking a handle blocks new admissions through it and all descendants.
   Revoking a child does not revoke siblings or the parent. Revocation is
   idempotent. Dropping a Python reference is not revocation.
4. Admission releases the authority lock before native I/O. Already-admitted
   reads, writes, connections and durable commits may finish after revocation.
   Revocation is not rollback, forced descriptor closure, or cancellation of an
   in-flight operation. This deliberately avoids reporting a committed write as
   undone. Application reconciliation follows the existing transaction rules.
5. A later operation checks authority again even if storage has a cached value.
   Revocation cannot retract data or secrets already returned to an application.
   Secret reveal of a previously obtained opaque value retains its existing
   semantics; this amendment mediates acquisition, not information erasure.

Scope is thread-local and inherited by parallel children and HTTP worker threads
from the serving call. Transitive module calls carry the same handle. Host scope
exit restores the previous handle, including exceptional unwinds. Scopes in
simultaneous host threads cannot replace each other's authority. An HTTP server
keeps its serving authority throughout its lifetime; per-user HTTP identities
and per-request tenant handles remain separate application/identity work.

## Native boundary decisions

Denial occurs before DB/cache access or file I/O, and before outbound network
admission. It returns `DbError`, `IoError`, `HttpError`, `ModelError` or
`ListenError` as appropriate; secret acquisition returns `NotFound`. Denial
records a payload-free resource/egress event where applicable. Authorization
checks precede the native's value preconditions when admission is refused.
Compiler effects and VM caller-effect checks remain in force.

The optional local model provider imports host Python; evolution can launch host
processes and write source outside resource-native mediation. Restricted VMs
therefore return `ModelError` or `EvolveError` before entering those integrations.
They cannot be re-enabled by granting `*`. Supporting them under delegated
resource authority requires a separately mediated implementation; passing a
handle to arbitrary companion code would not enforce this boundary.

## Review and evidence

The implementation review checked the entire current resource-native inventory:
all five DB natives, both file natives, HTTP request/listen, remote and local
model completion, secret acquisition, and evolution. Pure conversion/provide
natives perform no resource I/O. New resource natives must extend this mapping.
The existing descriptor-relative file and storage confinement remains responsible
for filesystem races; grant matching is not a replacement for secure opening.

`gopyt/test_resource_authority.py` covers attenuation/widening, ancestor and
sibling revocation, malformed grants, maximum delegation depth, compiled
transitive reads, warm-cache denial, mixed-grant atomic writes, simultaneous
host scopes, parallel inheritance, exact file rights, traversal, symlinks,
replacement after admission, and a paused real durable commit across revocation.
Real loopback tests exercise HTTP/model requests and a GoPyT HTTP worker launched
under an attenuated scope. Tests retain compile-fixture failures separately from
runtime qualification results in `validation/resource-authority/`.

These are finite implementation/regression results, not independent security
certification. Trusted Python, custom native replacements, interpreter memory,
and the embedding host remain inside the trust boundary. Hostile Python in the
same process can bypass Python objects; OS containment is issue #6. Authenticated
cross-tenant deployment and independent security review remain issues #10/#26.
No throughput, bounded native-memory, production SLO or universal safety claim
follows from these tests. The full milestone remains open.
