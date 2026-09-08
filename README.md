# GoPyT

GoPyT is an experimental programming language for typed backends and agent-assisted development. It has a compiler, a validating bytecode loader, a Python-hosted VM, explicit effects, and paired specification/implementation contracts.

The compiler rejects drift between paired contracts. Runtime checks catch violated preconditions and postconditions. `gopyt-guard` checks candidate changes against operator-pinned rules and finite acceptance cases. These controls do not prove arbitrary business requirements or make an application immune to attack.

## Install and try

Requires Python 3.11 or newer on Linux or macOS.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[security]'
.venv/bin/python examples/ticket_contracts/demo.py
cd examples/inventory
../../.venv/bin/gopyt check
```

Commands include `gopyt fmt`, `gopyt check`, `gopyt test`, `gopyt run <module.task>`, `gopyt-guard`, and the initial `gopyt-lsp` stdio server. Read the [language documents](docs/README.md) for exact syntax and APIs.

## Current engineering baseline

- [Inventory backend](examples/inventory/README.md): bounded reservations, idempotency, confirmation, cancellation, expiry, atomic persistence and independent model tests.
- [Security profile](SECURITY.md): authenticated HTTP behind a TLS gateway, authenticated encrypted storage, protected files and explicit threat boundaries.
- [Editor support](docs/editor.md): unsaved diagnostics, formatting, symbols and basic completion for dependency-free packages.
- [R&D and validation](docs/release-baseline.md): measured results, raw evidence and limitations.
- [Next priorities](docs/ROADMAP.md): security, language correctness and data-driven backends.

This is an alpha language repository. Model training is outside its scope. Historical reports retained under `docs/` describe earlier experiments; their external evidence archives are not all included in this checkout. Current claims are indexed in the baseline report above.

## Validate

```sh
.venv/bin/python -m unittest discover -s gopyt -t .
.venv/bin/python tools/check_stdlib_sync.py
.venv/bin/python -m tools.inventory_probe --out validation/runs/inventory-local
```

The inventory probe requires a new output directory and records raw timings. The Linux-only mapped-data prototype is research code, separate from the language runtime. See its [source and limitations](prototypes/mapped_data/README.md).
