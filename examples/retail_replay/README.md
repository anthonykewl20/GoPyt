# Public retail ingestion acceptance

`retail.add`, `retail.read`, and `retail.commit` exercise compiled checked integer
arithmetic, consistent multi-key reads and atomic conditional batches. The host
driver streams source rows, groups bounded ingestion batches, and supplies typed
arguments. This is an ingestion acceptance application. Its internal HTTP
`POST /read` and `POST /commit` routes expose the same primitives through typed
JSON. Deploy those routes only with the strict service-authentication profile
and an operator namespace policy; they do not implement customer authorization.

Source: Daqing Chen (2015), [Online Retail, UCI](https://archive.ics.uci.edu/dataset/352/online+retail),
[DOI](https://doi.org/10.24432/C5BW33), CC BY 4.0. The workbook contains 541,909
historical transaction rows. The preparation script pins its SHA256, retains all
quantities including returns and cancellations, and omits unnecessary customer
identifiers from replay artifacts. SQL aggregation supplies an independent
oracle. No third-party workbook reader is needed.

```sh
curl -fL 'https://archive.ics.uci.edu/static/public/352/online+retail.zip' -o /tmp/retail.zip
unzip /tmp/retail.zip -d /tmp/retail-source
python tools/retail_prepare.py '/tmp/retail-source/Online Retail.xlsx' --output /tmp/retail-prepared
python tools/retail_stress.py --data /tmp/retail-prepared --output /tmp/retail-trial --workers 4 --encrypted
```

Each ingestion batch includes its content digest as an idempotency marker in
the same transaction as its SKU totals. A second pass must apply no quantity
twice. Workers exit before a new VM verifies all SKU totals. Contention uses
bounded retries and reports conflicts and latency, not just successful calls.
The driver checks source hashes before and after each trial and retains failures.
`--overlap --passes 1 --workers 8` makes eight processes race every batch.

For actual HTTP ingestion, typed malformed-input/authorization checks and a
forced server restart, run:

```sh
python tools/retail_http_probe.py --data /tmp/retail-prepared --rows 541909 --output /tmp/retail-http.json
```

This probe automatically creates disposable external credentials and encrypted
storage. It checks final values against the hash-pinned SQL oracle after restart.

Batch boundaries may split an original invoice. The measured guarantee covers
the ingestion batch. This workload does not test prices, payments, customer
authorization, power loss or beyond-RAM processing. The subprocess replay
measures VM ingestion; the separate HTTP probe measures the HTTP path.
