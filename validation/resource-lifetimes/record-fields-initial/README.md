Initial record-field full-suite failures (retained)

The first integrated Python 3.14 full suite over the record-field source failed
six tests: four in gopyt.test_http_retention and two dependent diagnostics-coverage
tests that require the rest of the suite to pass.

- test_failed_response_write_releases_payload_in_retained_traceback (both
  subtests): the charge observed during the response write was 44 bytes, not 12.
  The extra 32 bytes are the live result record's admitted field array.
- test_request_body_capacity_rejection_and_release: 32 bytes remained charged
  after the server closed, because managed allocations stay registered until the
  VM's explicit mark and sweep.
- test_response_payload_admission_and_charge_during_write: an exhausted native
  byte budget produced 500, not 503, because the refusal became an allocation
  trap inside the handler instead of a resource limit at response encoding.

The corrected source sweeps the managed heap once before a refusal traps, marks
such a trap as overload so serving answers 503, and pins the exact retained
charge and its reclamation in the tests. See record-field-integration.
