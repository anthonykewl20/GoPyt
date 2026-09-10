# Active storage-key fencing design

Proposed next issue #11 increment; not an implemented control. The retained
[baseline](../validation/storage-writer-fence/before314.json) reproduces a stale
writer whose ring contains both keys but still selects the old active key. After
maintenance rekey and successful new-key-only reading, that writer publishes an
old-key ciphertext. The new-key-only reader then fails. Generation authority
correctly records a later snapshot; freshness alone does not choose a permitted
publishing key. No key material is retained in the probe output.

## Intended contract

Extend the trusted authority with the identity of the permitted publishing key.
The identity must bind actual key material and store context, rather than a mutable
keyring label. Retained reader keys allow authenticated decryption but do not grant
permission to publish using those keys. Normal mutations compare their selected
active key against the authority while holding the same store/authority locks.
A stale active-key writer fails before advancing authority or publishing data.
This also covers a writer that loaded its ring before waiting on the store lock.

Provide an explicit operator transition requiring the expected current generation.
It authenticates the existing snapshot using the supplied bounded reader ring,
republishes under the selected active key and changes the allowed writer identity
in the same authority-first publication protocol. A crash must recover the exact
admitted snapshot under the new writer policy or fail closed; it must not silently
re-enable the former key. An old-key-only reader can no longer read the new state,
while a stale ring retaining the new reader key still cannot publish with the old
active key. Fresh-process verification remains required before retiring backup keys.

Version the authority schema explicitly. Define how existing version-1 authorities
enter the fenced profile through operator action and how new enrollment chooses
its writer policy. Older runtimes must reject unknown authority versions rather
than ignore fencing metadata. Existing unanchored maintenance remains a quiesced
workflow; it cannot claim online fencing without trusted policy state. Preserve
ordinary keyring reader fallback, store identity, snapshot authenticity and CLI
compatibility. Do not make ordinary language writes into key-authorization actions.

Authorized backup restoration normally writes under the current allowed key;
retaining an old key solely to decrypt the backup must not authorize old-key
publication. Rotation intent and failure ambiguity must remain visible to operators.
Lost keys and lost authority cannot be repaired by inventing secret material or
silently enrolling an older state.

## Functional acceptance before implementation

- Reproduce the retained failure using separate old/new/retired ring configurations.
- After explicit transition, current active-key writes succeed; stale active-key
  writes reject without changing generation, ciphertext or logical state.
- Exercise a real writer already waiting on a lock with old loaded settings.
- Verify fresh-process reads after old-key retirement and deliberate backup restore
  using a reader ring containing the old backup key plus the current writer key.
- Crash before/after policy advancement and snapshot publication; recover exactly
  the admitted ciphertext/policy or fail closed on missing evidence.
- Reject stale operator generations, malformed/unknown policy metadata, unavailable
  authority and incompatible key configurations. Preserve legacy supported behavior
  with an explicit migration/compatibility policy and tests.

These are functional requirements. Any timing or scale claims need a separate
frozen workload and independent state oracle. This design does not complete the
remaining plaintext-migration or production custody/backup procedures.
