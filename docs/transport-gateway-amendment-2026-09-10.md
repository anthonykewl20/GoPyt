# Transport, gateway and egress boundary amendment

This amendment adds inbound and outbound network boundaries without changing
GoPyT source syntax or the closed standard-library signatures. It constrains
what one serving process believes and which addresses it dials. It is not
network isolation, and it does not authenticate the gateway itself.

## Trusted gateway boundary

Strict mode terminates TLS at a gateway and listens on a numerical loopback
address, so the service never sees the client's connection. Anything the gateway
asserts about that client arrives as an ordinary header that a client could also
send. Three environment variables define when such a header may be believed:

- `GOPYT_HTTP_GATEWAY_ADDR`: the numerical address the trusted gateway connects
  from. Setting it makes the service fail closed for every other peer: a request
  from any other address is refused **403** with an empty body before it is
  routed, because the deployment has stated that all traffic arrives through the
  gateway.
- `GOPYT_HTTP_FORWARDED_HEADER`: the header carrying the verified client
  identity, default `X-GoPyT-Client`. It is read only when the connection came
  from the configured gateway address. A duplicate header is **400**; a value
  that is empty, longer than 256 bytes, or not printable non-space ASCII is
  **400**.
- `GOPYT_HTTP_FORWARDED_REQUIRED`: `1` makes a missing forwarded identity
  **401**.

Setting a forwarded header or requirement without a gateway address is a refused
configuration, not a default: a header nobody is trusted to have written would
be the client's own claim. With no gateway configured the header is never read,
so a client cannot name itself, and a request from any peer is served as
unidentified.

The gateway boundary constrains this process. Keeping other peers away from the
loopback listener, authenticating the gateway, and terminating TLS correctly
remain deployment responsibilities.

## Explicit inbound header bounds

Header parsing no longer inherits whatever the host HTTP library defaults to.
The request line is bounded at 8,192 bytes, each header line at 8,192 bytes, the
header block at 32,768 bytes and 64 headers. Parsing stops at the bound while
the block arrives rather than buffering a large block and rejecting it
afterwards. An over-bound request line is **414** and an over-bound header line,
count or block is **431**, both with an empty body like every other refusal on
this path.

## Identity-aware admission

`GOPYT_HTTP_RATE`, written `tokens/refill_ms`, applies the existing bounded
token-bucket limiter at HTTP admission. The bucket key is the strongest verified
identity available, in order: the authenticated session subject, then the
identity a trusted gateway asserted, then the peer address. A key the client
itself could choose is never used, so two clients asserting different identities
to a service with no configured gateway share one bucket. Refusal is **429**
with an empty body and a closed connection, and it is recorded as a denial.

## Outbound resolved-address policy

An egress allowlist names origins, not addresses, so a permitted name can still
resolve to a loopback, private or link-local address — through the deployment's
own DNS or an attacker's answer. Every resolved candidate is now checked before
a socket is created:

- an origin written as a literal address connects only to that exact address, so
  no resolver answer can move it;
- `localhost`, `127.0.0.1` and `::1` may reach loopback, because an operator who
  allowlisted loopback chose loopback;
- every other host must reach a globally routable address. Loopback, private,
  link-local (including the `169.254.0.0/16` metadata range), unique-local,
  multicast and reserved addresses are refused, in IPv4-mapped IPv6 form as
  well.

A refused address returns the typed `HttpError`/`ModelError` reason `address`
and is recorded as an egress denial, distinct from a transport failure. Redirect
refusal, the explicit no-proxy opener, userinfo rejection and certificate
verification are unchanged. This is an egress boundary for addresses this
runtime dials; it does not constrain anything else on the host.

## Status codes

`docs/implementer.md` section 12 gains 403, 414, 429 and 431 to the empty-body
refusals it already fixes. Handler traps, routing and success responses are
unchanged.
