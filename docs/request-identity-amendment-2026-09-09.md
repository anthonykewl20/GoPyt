# Verified request identity and tenant sessions: 2026-09-09

This normative host-runtime amendment implements the request identity boundary
in issue #10 and completes the authenticated-tenant integration of issue #4's
[delegated authority](resource-authority-amendment-2026-09-09.md). It adds no
GoPyT syntax, callable signatures, bytecode fields, or CLI flags.

## Supported identity mode and trust boundary

The initial mode is operator-provisioned API subjects with opaque bearer
sessions. A trusted Python host uses `SessionBroker` to provision a subject and
tenant, issue a bounded session credential, and attach the broker to a VM.
The HTTP server verifies possession of that credential on every request. It
never accepts a tenant, subject, role or authentication assertion from a URL,
request body, cookie, query token, `X-User`, `X-Tenant-Id` or forwarded header as
verified identity. Body keys remain untrusted resource requests checked against
the authenticated subject's actual authority.

The broker is a trusted provisioning interface, not a public signup/password
endpoint. The operator establishes a subject's identity and permission before
provisioning it. In a human-login deployment, the operator's gateway/identity
adapter must verify the upstream authentication result and required MFA **before**
calling `issue`. GoPyT neither validates an OAuth/JWT assertion nor implements
password enrollment/recovery, MFA ceremonies, or browser login/CSRF flows.
Such integrations must not pass an unverified client subject into `issue`.

Bearer credentials must be protected in transit and storage; possession permits
use. The Authorization header and TLS gateway boundary follow the transport
principle in [RFC 6750, sections 2.1 and 5](https://www.rfc-editor.org/rfc/rfc6750.html).
This API is not a claim of implementing the whole OAuth protocol or sender-bound
tokens. The supported gateway contract is precise: terminate authenticated TLS,
restrict backend reachability to the protected loopback listener, and forward
exactly one Authorization bearer header containing the broker-issued credential.
No forwarding identity header substitutes for that credential. A gateway can
exchange an upstream verified identity for a broker session through trusted host
code; accepting upstream tokens directly is unsupported.

`GOPYT_SECURITY_PROFILE=strict` still requires the existing encryption key/context
and loopback listener. A configured broker supplies authentication in this mode,
so a shared `GOPYT_HTTP_TOKEN_FILE` is not required. Configuring both is rejected
with `ListenError`, rather than silently selecting one. TLS certificate lifecycle,
gateway administration, upstream issuer/audience/MFA verification, rate policies,
and protection from hostile local processes remain operator responsibilities.
Their deployed qualification stays in issues #6/#11/#23/#24/#26.

## Provisioning and embedding

```python
from pathlib import Path
from gopyt.cli import build
from gopyt.identity import SessionBroker
from gopyt.resource_authority import ResourceAuthority
from gopyt.vm import VM

root = str(Path('examples/retail_replay').resolve())
_, artifact, functions = build(root)
service = ResourceAuthority.issue(
    database_read=['tenant/'], database_write=['tenant/'],
    listen=['127.0.0.1:8080'])
broker = SessionBroker(service, module='retail')
broker.provision('alice', 'alpha',
    routes=[('POST', '/read'), ('POST', '/commit')],
    database_read=['tenant/alpha/'], database_write=['tenant/alpha/'])
credential = broker.issue('alice', 'alpha', ttl_ms=900_000)
# Deliver credential only through the operator's protected credential channel.
# Never put it in a URL, application source, logs, or a public configuration file.
vm = VM(artifact, root, authority=service, identities=broker)
vm.call(functions['retail.serve'], [])
```

This uses the actual retail replay application. The trusted host retains `broker`
for policy updates and revocation while the server runs. Production integration
must configure the strict profile, storage key/context and TLS gateway first.
The ordinary CLI and VMs without a broker retain their existing authentication
mode; applications cannot enable a broker by submitting source or headers.

A broker names an exact GoPyT module audience and binds to exactly one VM. The
server rejects a different module audience or a broker outside its serving
authority. A new VM requires a new broker and sessions; accidental cross-service
reuse is not an implicit authentication mechanism. A distributed session store
or cross-process session replication is not supported by this in-process mode.

## Policy interface and least privilege

`provision(subject, tenant, routes=..., **rights)` creates or replaces the complete
policy for that `(subject, tenant)` pair. Subjects are 1..128 printable non-space
ASCII characters; tenants use GoPyT's 2..64-byte `snake` grammar. They are operator
identities, not values inferred from request parameters. Multiple subjects may
belong to a tenant with different grants. A subject in multiple tenants receives
separate policies and credentials.

Rights use the delegated-authority vocabulary. Every DB grant must be beneath
`tenant/<verified_tenant>/`; every file grant must be beneath
`tenants/<verified_tenant>/`. Further per-subject or per-object narrowing is
allowed. A wildcard or another tenant's prefix is rejected. Network and named
secret grants still require explicit attenuation from the service authority;
request sessions cannot acquire listeners. Omitted rights deny access.

Routes are a bounded list of exact `(HTTP_METHOD, declared_route_template)` pairs.
Authorization checks the matched artifact route, not a client-provided role or
handler name. A read-only subject is not allowed to reach a write endpoint simply
because it knows its path. Resource authority is checked again inside that
endpoint: route authorization alone does not grant any database/file/network
access. Unknown route grants match nothing; they cannot authorize an undeclared
route. Grants and route lists are copied into immutable policy data.

At most 1024 subject/tenant policies and 4096 live session entries are retained.
There are at most 256 route entries per policy; underlying resource grant/depth
limits also apply. Exceeding capacity raises `AuthorityError`, never evicts an
active identity or falls back to an unrestricted policy. Expired/revoked sessions
are purged during issuance and policy changes, and rejected during lookup.
These count limits are not an aggregate process-memory qualification claim.

## Credential lifecycle

- `issue(subject, tenant, ttl_ms=900000)` requires a current provisioned policy.
  Lifetimes are integers in 1..3,600,000 ms. A 256-bit random URL-safe bearer value
  is returned once; only its SHA-256 digest is retained for lookup. Credentials
  are not stored in the application DB, configuration, or logs by this module.
- Each returned context has an immutable subject, tenant and opaque session ID.
  `vm.request_identity` exposes it to trusted host instrumentation during the
  request and parallel descendants. It is not a forgeable GoPyT record or an
  implicit extra handler argument.
- `revoke_session(credential)` revokes that session's authority. `revoke(subject,
  tenant)` removes the policy and revokes all its descendant sessions. Replacing
  a policy also revokes its old sessions, even if the replacement widens rights.
  Clients must obtain a fresh credential after reauthorization. Re-provisioning
  an account never revives its old credentials.
- Session expiry uses integer monotonic deadlines, checked on every native
  resource admission, including descendants retained after request entry.
  Wall-clock rollback cannot extend a session. A child cannot outlive a parent
  by omitting its own deadline. The general host authority `delegate(ttl_ms=...)`
  supports 1..86,400,000 ms; the broker uses the stricter one-hour ceiling.
- Issuing a replacement session and then revoking the old one provides explicit
  rotation with a bounded overlap. There is no automatic refresh or indefinite
  session renewal. A new broker/process rejects all old credentials, even if
  identical subject policies are provisioned again; operators must reissue.

Revocation/expiry deny **new** resource admissions. Already-admitted operations
may finish, including durable commits. They do not erase returned data, retract
acknowledgements, or cancel pure computation. A request that authenticated before
revocation but reaches a DB native afterward gets the declared `DbError`; it
cannot retain a privileged cache path. Reconciliation and cancellation semantics
remain those of the resource and transaction amendments.

## HTTP execution and failures

Every request, including each request on a reused connection, must supply exactly
one Authorization header with the issued token. Missing, malformed, duplicate,
unknown, revoked or expired credentials receive an empty 401 and close the
connection before handler execution. The response includes a Bearer challenge.
A verified session lacking the matched route receives an empty 403. Payload and
routing errors retain existing behavior. Native resource denials retain their
declared result values (for example, the retail handler returns its normal
`db_error` response at HTTP 200).

The request's identity and delegated authority surround argument conversion and
handler execution, restore on exit/unwind, and propagate into parallel tasks.
HTTP workers retain the service authority between requests, never a prior user's
scope. Sessions may be replaced/revoked concurrently with requests. Admission
locks order policy changes and resource decisions; no authority lock is held
while performing native I/O. Authentication/route denials add payload-free HTTP
status observations; no credential, subject, or tenant is added to default logs.

## Review and qualification evidence

`gopyt/test_identity.py` covers forged/malformed credentials and identity headers,
per-tenant permissions, mixed-tenant atomic batches, read-only route escalation,
credential expiry/revocation/re-provisioning, fixed session capacity, immutable
policy input, audience mismatch and broker reuse. It checks identity inheritance
through compiled parallel arms and scope restoration. Real HTTP tests exercise
concurrent policy changes and different users, reused connections, and controlled
expiry/revocation after authentication but before a compiled DB operation.

The HTTP cases repeat with strict loopback authentication and actual encrypted
storage. The encrypted fixture verifies that private tenant data is absent from
the persisted ciphertext and only the authorized user can read it over HTTP.
These are generated fixtures, not customer data or a throughput/availability
benchmark. The dataset, performance/SLO and disaster-recovery gates remain open.
Retained source identities, test results and unsuccessful fixture/environment
attempts live in `validation/request-identity/`.

The review explicitly added a module audience and single-VM broker binding after
identifying the accidental cross-application reuse risk. It checked every point
where the request crosses into native resource I/O and both thread creation
paths. This is an implementation review with finite adversarial regressions,
not an independent security audit, an MFA/IdP deployment certification, or a
claim that all possible identity/deployment defects have been eliminated.
