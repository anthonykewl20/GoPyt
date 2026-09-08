# On-disk P1 fixtures (C001–C054)

Each `C*/` is a package. `expect` first line is `ok` or `Ennn` (what `gopyt/test_fixtures.py` compares). Extra lines: `phase:`, `trap:`, `command:`, `alt:`, `note:`.

Do not rewrite sources to match a dialect.

Fixture fixes after compiler review: C012 adds `provide Json`; C031 drops unused `assert_eq`; C033 calls `math.charge_zero()`; C047 uses `value` so `_` is E039 not E016.

| ID | expect | phase |
|----|--------|--------|
| C001 | ok | check |
| C002 | E013 | check |
| C003 | E070 | check |
| C004 | E053 | check |
| C005 | E020 | check |
| C006 | E016 | check |
| C007 | E010 | check |
| C008 | E011 | check |
| C009 | E065 | check |
| C010 | E054 | check |
| C011 | E097 | check |
| C012 | E022 | run `billing.api.post_charge` (handler uses `log`; check must pass first) |
| C013 | E011 | check |
| C014 | ok | check |
| C015 | E011 | check |
| C016 | E011 | check |
| C017 | E073 | check |
| C018 | ok | check |
| C019 | ok | check |
| C020 | ok | check |
| C021 | E029 | check |
| C022 | ok | run |
| C023 | E054 | check |
| C024 | E101 | run trap 4; **task** `math.boom` |
| C025 | E071 | check |
| C026 | ok | fmt |
| C027 | ok | bytecode junk in `build/out.gobyte` |
| C028 | E052 | check |
| C029 | E065 | check |
| C030 | E041 | check |
| C031 | ok | check |
| C032 | ok | test |
| C033 | ok | check |
| C034 | E069 | check |
| C035 | E111 | check |
| C036 | E112 | check |
| C037 | ConvertError | run extra JSON key (`input.json`) |
| C038 | E074 | check; alt E110 |
| C039 | E114 | check |
| C040 | E115 | evolve |
| C041 | E117 | check |
| C042 | E017 | `class` |
| C043 | E017 | `async` |
| C044 | E020 | `string` |
| C045 | E020 | `any` |
| C046 | E017 | `null` |
| C047 | E039 | `_` |
| C048 | E011 | `=>` |
| C049 | E011 | `&&` |
| C050 | E017 | `public` |
| C051 | E017 | `from` import |
| C052 | E037 | `user.name()` |
| C053 | E010 | f-string |
| C054 | E046 | version `^1.0.0` |
