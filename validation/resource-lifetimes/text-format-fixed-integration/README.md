Corrected text-formatting integration evidence

Frozen source/runtime identity is recorded in source.json (head
e5bdc4cf5f32fc1cb0c4eee0c0c9104e5b16587d, runtime
sha256:eccbee4a7c10a70759865cb7fe6a272a53dfb87543e393705a78c69bc1c69e4a).
Two wheel builds under SOURCE_DATE_EPOCH=1788998400 are byte-identical; the
report is in wheel-report.json. Installed smoke, upgrade/rollback, Guard,
22-module stdlib synchronization and all contract-demo outcomes passed.

Corrected Python 3.14.7 full language suite: 1,127 tests passed in 443.516s
(full314.log).

Corrected Python 3.11.16 full language suite: 1,127 tests passed in 552.032s
(full311.log). The 159 frozen source hashes in source.json were unchanged for
both runs.

Earlier failed and interrupted trials remain under text-format,
text-format-depth and text-format-integration. That evidence records the
reproduced Python 3.11 recursion-depth regression and does not qualify this
corrected source.
