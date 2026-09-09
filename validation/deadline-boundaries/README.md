# Deadline specification coverage review

Reviewed runtime: `17036c26c75017b1ea414793a754cfd4fea5ea98`, fingerprint
`bce10f32d3acdea13b7f3490689285284ca6e8252a3ecedcfa1680d508616dd6`.
The review concerns the shipped Python VM and its closed native dispatch, not an
installed optional provider or arbitrary host-injected native implementation.

Issue #13 A1 is: “Specify deadline propagation, cancellation delivery and commit
ambiguity across nested tasks and blocking native calls.” The
[normative consolidation](../../docs/deadline-boundaries.md) addresses that scope.
It does not reinterpret a synchronous boundary as interruptible or qualify A2–A4.

| Requirement | Reviewed implementation | Policy/evidence |
| --- | --- | --- |
| Nested propagation | `VM.check_cancelled`, `_parallel`, `_parallel_admitted`; worker context transfer and reservation cleanup in `vm.py`/`scheduling.py` | Parallel amendment; `test_parallel_admission` covers inherited absolute deadlines, ancestor cancellation, admission rejection and partial startup. |
| Delivery before/after native work | `VM._call`, `VM.call`, `_exec_frame`; all 73 fixed dispatch entries, money/time installers and `resolve_provide` | Inventory's 12 explicit groups plus three conversion forms. The consolidation specifies CPU, stderr, clocks/entropy, secrets and optional-provider boundaries without claiming interior polling. `test_noncooperative_native_is_joined_before_timeout_returns` retains the generic late-native limitation. |
| Polling and admission boundaries | `files.py`, `storage.py`, `netio.py`, `evolve.py`, `transaction.py`, `observe.py`, `server.py` and their native callers | Parallel amendment and retained `file-deadline`, `storage-deadline`, `outbound-budget`, `evolve-deadline`, `observe-deadline`, `http-request-budget` and `http-drain` evidence. |
| Commit ambiguity | Storage replacement and journal commit/recovery; VM result/error handling; HTTP responses | Transaction outcome policy; `test_transaction_cancellation` includes before-call rejection, admitted commit, post-replace error and joined publication after timeout. File/evolution tests cover partial writes and admitted journal completion. |
| Cleanup limits | Heap instruction guard, frame/pin/handoff ownership, `released`, collect; worker joins and process reaping | Consolidation explicitly specifies synchronous cleanup barriers. Existing heap/parallel/evolution tests establish their exercised ownership cases, not bounded OS latency or every resource schedule. |

`inventory.json` explicitly assigns each name once; no “all remaining names”
fallback can silently classify a new native. It also records reviewed runtime
file hashes. The checker passes on Python 3.11.16 and 3.14.7. Six negative controls
reject a missing name, duplicate assignment, obsolete name, wrong runtime,
changed reviewed source and missing conversion form. These controls test the
inventory guard; they do not test cancellation behavior.

The reviewed runtime already passed all 854 tests and packaging checks in the
[observation qualification](../observe-deadline/README.md). This change modifies
only policy, review evidence and the inventory tool. No runtime, test, lock or
packaging inputs change, so those existing runtime results are reused rather than
presented as a new full run. `qualification-identities.json` freezes relevant
regression source/log identities. No latency benchmark was tuned for this review.

The review supports the **specification** scope of A1 once its referenced changes
are merged. Remaining issue work includes queue/buffer/retry/fairness scope,
all required graceful cleanup behavior and sustained slow-client/saturation/
cancellation/write-shutdown latency and resource evidence. No claim is made that
every native is preemptible, that heap pauses are bounded, that sketches capture
every terminated event, or that an optional provider has been qualified.
