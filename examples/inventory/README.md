# Inventory reservation backend

This validation application holds 10 inventory units and retains at most 32 reservation IDs in one atomic aggregate. It exercises typed HTTP/JSON, explicit time/storage effects and a runtime invariant on state transitions.

Run `gopyt check`, then `gopyt run inventory.serve` from this directory. Use the repository security profile for authenticated operation. `GET /inventory` reaps expired reservations and returns the current state. `POST /inventory` accepts a JSON command:

```json
{"action":"reserve","id":"order-001","quantity":2,"ttl_ms":60000}
```

Actions `confirm` and `cancel` require quantity and ttl_ms to be zero. IDs are 1..64 characters, quantities 1..10, and TTL 1..60000 milliseconds. Reusing an ID with changed quantity or TTL is a conflict. Identical retries retain the same outcome. Confirmation is terminal; cancelled and expired IDs remain tombstones. Full ledgers reject new IDs rather than silently recycling them.

Whole-state compare/exchange prevents lost updates. Unchanged reads and retries avoid rewriting an identical snapshot. Contention can exhaust eight retries and return `conflict`; retry with the same request identity. `storage_error` and `conflict` response states are placeholders, not authoritative inventory snapshots.

Tests compare 400 seeded transitions to an independent Python model, then exercise concurrent duplicates, oversell prevention, real expiry and abrupt process restarts through HTTP. Separate security tests require credentials and verify encrypted restart behavior. This is a bounded backend example, not a payment system, distributed database, production capacity claim or complete authorization policy.
