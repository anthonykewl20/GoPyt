"""GOPYT_E* emission. First error only; exact wire format."""

from __future__ import annotations

from dataclasses import dataclass


IDS = {
    1: "internal",
    2: "tab",
    3: "encoding",
    4: "unformatted",
    10: "lex",
    11: "parse",
    12: "module_header",
    13: "missing_use",
    14: "unused_use",
    15: "use_star",
    16: "ident",
    17: "keyword",
    18: "extra_impl",
    19: "extra_spec_item",
    20: "unknown_type",
    21: "type_mismatch",
    22: "arity",
    23: "missing_field",
    24: "extra_field",
    25: "union",
    26: "optional",
    27: "coerce",
    28: "builtin_shadow",
    29: "generic",
    30: "unresolved",
    31: "spec_body",
    32: "impl_header",
    33: "missing_impl",
    34: "spec_unresolved",
    35: "visibility",
    36: "rebind",
    37: "method",
    38: "comma",
    39: "wildcard",
    40: "toolchain",
    41: "lock_stale",
    42: "dep_cycle",
    43: "dep_missing",
    44: "dep_name",
    45: "dep_dup",
    46: "toml",
    50: "effect_missing",
    51: "effect_unused",
    52: "effect_unknown",
    53: "fn_task",
    54: "parallel",
    55: "parallel_type",
    60: "contract_type",
    61: "contract_f64",
    62: "contract_task",
    63: "open_id",
    64: "english_contract",
    65: "open_hole",
    69: "ffi_app",
    70: "match",
    71: "if_expression",
    72: "for",
    73: "return_path",
    74: "unknown_name",
    80: "provide_orphan",
    81: "provide_dup",
    82: "provide_sig",
    83: "provide_effect",
    84: "trait_fn",
    90: "http_verb",
    91: "http_handler",
    92: "http_param",
    93: "serve",
    94: "agent_task",
    95: "agent_effect",
    96: "stdlib",
    97: "json",
    98: "from_str",
    99: "f64_eq",
    100: "opcode",
    101: "trap",
    110: "dynamic_code",
    111: "egress",
    112: "secret_leak",
    114: "evolve_model",
    115: "evolve_gate",
    116: "evolve_hot",
    117: "observe_bound",
}


@dataclass
class Diag:
    code: int
    file: str | None = None
    line: int | None = None
    offset: int = 0
    repair: str = ""
    trap: int | None = None

    @property
    def ident(self) -> str:
        return IDS.get(self.code, "internal")


class CompileError(Exception):
    def __init__(self, diag: Diag) -> None:
        self.diag = diag
        super().__init__(format_diag(diag))


def format_diag(d: Diag) -> str:
    if d.code not in IDS:
        d = Diag(1, repair="")
    name = d.ident
    lines = [f"GOPYT_E{d.code:03d} {name}"]
    if d.file:
        lines.append(f"file: {d.file.replace(chr(92), '/')}")
    if d.line is not None:
        lines.append(f"line: {d.line}")
    if d.trap is not None:
        lines.append(f"trap: {d.trap}")
    repair = d.repair
    raw = repair.encode("utf-8")
    lines.append(f"repair-bytes: {len(raw)}")
    text = "\n".join(lines) + "\n"
    if raw:
        text += "\n" + repair
        if not repair.endswith("\n"):
            text += "\n"
    return text
