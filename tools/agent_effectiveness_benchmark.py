#!/usr/bin/env python3
"""Freeze and run the small, cooperative quotation-v1 fresh-agent pilot.

No agent-authored solution is repaired by this controller. Evaluation happens
after the session ends; withheld cases never become agent feedback.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import selectors
import traceback
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks/agent_eval"
LANGUAGES = ("gopyt", "python", "typescript")
MODULES = ("model", "pricing", "quote", "checkout")
IGNORED = {"build", "__pycache__", ".mypy_cache", ".gopyt-state"}
TASKS = {
    "a": "Change member discounts to 5% when the undiscounted subtotal is less than 20,000 cents and 10% when it is at least 20,000 cents. Apply the selected rate to the entire subtotal and round down once. Determine free shipping using the discounted merchandise amount as before. Preserve retail behavior, input domains, the public response envelope, and error outcomes.",
    "b": "Add customer kind nonprofit with a 7% discount on the entire subtotal, rounded down once. Preserve the baseline retail and member rates. Shipping still uses the amount after discount. Out-of-range quantity must now produce outcome invalid_quantity and four zero amounts, taking precedence even when other fields are invalid. All other invalid requests still produce invalid and zero amounts. Extend the internal typed customer and outcome variants and update all producers and exhaustive consumers; do not erase types to bypass checking.",
    "c": "Give loyal customers better shipping.",
}


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED for part in rel.parts) or path.name == ".gopyt-transaction.lock":
            continue
        if path.is_symlink():
            result[str(rel)] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def copy_source(source: Path, target: Path) -> None:
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(*IGNORED, "*.pyc", ".gopyt-transaction.lock"))


def load_oracle(path: Path):
    spec = importlib.util.spec_from_file_location("quotation_oracle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment(campaign: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GOPYT_EVAL_TOOLCHAIN"] = str(BENCH / "toolchain")
    env["PYTHONPATH"] = str(campaign / "support")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONSTARTUP", None)
    return env


def commands(language: str) -> dict[str, list[str]]:
    toolchain = BENCH / "toolchain"
    python = str(toolchain / "venv/bin/python")
    if language == "python":
        return {"static": [str(toolchain / "venv/bin/mypy"), "--config-file", "mypy.ini"],
                "public": [python, "smoke_test.py"], "adapter": [python, "adapter.py"]}
    if language == "typescript":
        return {"static": [str(toolchain / "node_modules/.bin/tsc"), "-p", "tsconfig.json", "--typeRoots", str(toolchain / "node_modules/@types")],
                "public": [shutil.which("node") or "node", "build/smoke_test.js"],
                "adapter": [shutil.which("node") or "node", "build/adapter.js"]}
    return {"static": [python, "-m", "gopyt", "check"],
            "public": [python, "smoke_test.py"], "native": [python, "-m", "gopyt", "test"],
            "adapter": [python, "adapter.py"]}


def run_command(command: list[str], cwd: Path, env: dict[str, str], timeout: float = 60) -> dict:
    started = time.monotonic()
    try:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return {"command": command, "returncode": None, "error": str(exc), "pass": False}
    timed_out = False
    try:
        out, err = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = child.communicate()
    return {"command": command, "returncode": child.returncode, "timed_out": timed_out,
            "pass": child.returncode == 0 and not timed_out, "elapsed_seconds": time.monotonic() - started,
            "stdout": out.decode(errors="replace"), "stderr": err.decode(errors="replace")}


def writable(language: str) -> list[str]:
    if language == "gopyt":
        return [f"{folder}/{module}.gopyt" for folder in ("spec", "impl") for module in MODULES] + ["gopyt.lock"]
    return [module + (".py" if language == "python" else ".ts") for module in MODULES]


def prompt(language: str, task: str, campaign: Path) -> str:
    return f"""You are maintaining the unfamiliar quotation project in your current directory.
Read README.md and the local business modules, then address this request:

{TASKS[task]}

Tasks are independent; the README describes the current baseline. Preserve all
behavior outside this request. Keep the internal typed variants and exhaustive
consumers. Do not weaken static checks, suppress errors, add dependencies, or
move business behavior into infrastructure. Source/spec changes are authorized
only in these paths: {json.dumps(writable(language))}. Generated build/cache
files may be created. Adapters, public tests, config, and documentation are fixed.

For any request that lacks necessary business policy, ask concrete clarification
questions before implementing assumptions. Record them in clarification.json as
{{"status":"needs_clarification","questions":["..."],"assumptions":[]}} and leave
business sources unchanged. No follow-up policy answers are supplied this run.
Otherwise implement the request and use the documented checks/public tests.

Use only this project's files and its documented tools. Toolchain directory:
{BENCH / 'toolchain'}. GoPyT runtime is on PYTHONPATH at {campaign / 'support'}.
Do not inspect parent directories, other trials, evaluator files, external
repositories, user configuration, environment secrets, or network resources.
Do not delegate or use another agent. Finish within 360 seconds and 40 observed
tool items. Report what changed and checks performed. This is a fresh session;
you have no prior solution or trial feedback.
"""


def freeze(campaign: Path) -> None:
    if campaign.exists():
        raise SystemExit(f"Refusing to overwrite campaign: {campaign}")
    campaign.mkdir(parents=True)
    copy_source(ROOT / "gopyt", campaign / "support/gopyt")
    for language in LANGUAGES:
        copy_source(BENCH / "subjects" / language, campaign / "baseline" / language)
    for filename in ("contracts.md", "evaluator.py"):
        shutil.copy2(BENCH / filename, campaign / filename)
    shutil.copy2(__file__, campaign / "runner.py")
    shutil.copy2(ROOT / "docs/agent-effectiveness-benchmark-plan.md", campaign / "methodology.md")
    shutil.copy2(BENCH / "toolchain/package-lock.json", campaign / "toolchain-package-lock.json")
    shutil.copy2(BENCH / "toolchain/requirements.txt", campaign / "toolchain-requirements.txt")
    rng = random.Random(20260905)
    blocks = [(task, repeat) for repeat in (1, 2) for task in TASKS]
    rng.shuffle(blocks)
    schedule = []
    for block, (task, repeat) in enumerate(blocks):
        languages = list(LANGUAGES)
        languages = languages[block % 3:] + languages[:block % 3]
        for language in languages:
            trial_id = f"{task}-{language}-r{repeat}"
            schedule.append({"id": trial_id, "task": task, "language": language, "repeat": repeat, "block": block})
            target = campaign / "prompts" / f"{trial_id}.txt"
            target.parent.mkdir(exist_ok=True)
            target.write_text(prompt(language, task, campaign))
    versions = {}
    for label, cmd in {"codex": ["codex", "--version"], "node": ["node", "--version"],
                       "typescript": [str(BENCH / "toolchain/node_modules/.bin/tsc"), "--version"],
                       "mypy": [str(BENCH / "toolchain/venv/bin/mypy"), "--version"],
                       "python": [str(BENCH / "toolchain/venv/bin/python"), "--version"]}.items():
        versions[label] = run_command(cmd, ROOT, environment(campaign))
    manifest = {"schema": "quotation-v1-campaign", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": "gpt-6-astra", "reasoning_effort": "high", "seed": 20260905,
                "wall_limit_seconds": 360, "observed_tool_limit": 40, "parallelism": 3,
                "visibility": "cooperative withholding; adversarial read isolation unverified",
                "token_budget": "none; CLI-reported usage is post-hoc, not an enforced token cap",
                "retry_policy": "no automatic replacements; retain every scheduled disposition",
                "external_toolchain_integrity": "installed tooling is outside the campaign; versions and dependency lock files are retained, full installed-file integrity is not enforced",
                "schedule": schedule, "versions": versions, "commands": {lang: commands(lang) for lang in LANGUAGES},
                "allowlists": {lang: writable(lang) for lang in LANGUAGES}, "frozen_files": inventory(campaign)}
    dump(campaign / "manifest.json", manifest)
    print(f"Frozen {len(schedule)} trials at {campaign}", flush=True)


def verify_freeze(campaign: Path, manifest: dict) -> None:
    current = inventory(campaign)
    changed = [path for path, digest in manifest["frozen_files"].items() if current.get(path) != digest]
    for prefix in ("support", "baseline", "prompts"):
        expected = {path: digest for path, digest in manifest["frozen_files"].items() if path.startswith(prefix + "/")}
        actual = {path: digest for path, digest in current.items() if path.startswith(prefix + "/")}
        if expected != actual:
            changed.extend(path for path in expected.keys() | actual.keys() if expected.get(path) != actual.get(path))
    if changed:
        raise RuntimeError(f"Frozen campaign files changed: {sorted(set(changed))}")


def evaluate_subject(campaign: Path, work: Path, language: str, task: str, manifest: dict) -> dict:
    env = environment(campaign)
    cmds = manifest["commands"][language]
    # This directory is a clean copy of submitted source: no emitted JS, Python
    # bytecode, compiler cache or agent-created build artifacts may be reused.
    static = run_command(cmds["static"], work, env)
    public = run_command(cmds["public"], work, env)
    native = run_command(cmds["native"], work, env) if "native" in cmds else None
    # Use the oracle's CLI in a separate process with the explicit frozen-runtime
    # environment; never mutate global os.environ in concurrent trial workers.
    oracle_command = [str(BENCH / "toolchain/venv/bin/python"), str(campaign / "evaluator.py"),
                      "score", "--task", task, "--command-json", json.dumps(cmds["adapter"]),
                      "--cwd", str(work), "--timeout", "60"]
    oracle_process = run_command(oracle_command, work, env, timeout=75)
    try:
        behavior = json.loads(oracle_process["stdout"])
        if not isinstance(behavior, dict) or "business_pass" not in behavior:
            raise ValueError("oracle did not return a business report")
    except (KeyError, ValueError) as error:
        behavior = {"business_pass": False, "errors": [f"oracle infrastructure failure: {error}"]}
    answer = {"static": static, "public": public, "native": native,
              "behavior": behavior, "oracle_process": oracle_process}
    if task == "c":
        answer["clarification"] = load_oracle(campaign / "evaluator.py").clarification_structure(work / "clarification.json")
    return answer


def checks_pass(report: dict) -> bool:
    return (report["static"]["pass"] and report["public"]["pass"]
            and (report["native"] is None or report["native"]["pass"])
            and report["oracle_process"]["pass"] and report["behavior"]["business_pass"])


def baseline(campaign: Path, manifest: dict) -> None:
    reports = {}
    for language in LANGUAGES:
        work = campaign / "baseline-check" / language
        if work.exists():
            raise RuntimeError("Baseline check already exists; do not overwrite evidence")
        copy_source(campaign / "baseline" / language, work)
        before = inventory(work)
        report = evaluate_subject(campaign, work, language, "baseline", manifest)
        report["source_preserved"] = inventory(work) == before
        reports[language] = report
        dump(campaign / "baseline-check" / f"{language}.json", report)
    if not all(checks_pass(r) and r["source_preserved"] for r in reports.values()):
        raise RuntimeError("Baseline validation failed; no measured trials may start")
    dump(campaign / "baseline-pass.json", {"pass": True, "languages": list(LANGUAGES)})


def agent_session(campaign: Path, trial: dict, manifest: dict, directory: Path) -> dict:
    work = directory / "work"
    initial = (campaign / "prompts" / f"{trial['id']}.txt").read_text()
    cmd = [shutil.which("codex") or "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
           "--skip-git-repo-check", "--sandbox", "workspace-write", "--json", "--disable", "memories",
           "--disable", "apps", "--disable", "plugins", "--disable", "multi_agent_v2",
           "--enable", "skip_host_skill_discovery", "-m", manifest["model"],
           "-c", 'model_reasoning_effort=' + json.dumps(manifest["reasoning_effort"]),
           "-c", 'approval_policy="never"', "-c", 'web_search="disabled"', "-C", str(work),
           "--output-last-message", str(directory / "final-message.txt"), initial]
    dump(directory / "invocation.json", {"command": cmd, "cwd": str(work),
                                         "provided_environment": {key: environment(campaign)[key] for key in ("PYTHONPATH", "GOPYT_EVAL_TOOLCHAIN", "PYTHONDONTWRITEBYTECODE")}})
    started = time.monotonic()
    observed: dict[str, str] = {}
    usage_events = []
    terminal = None
    budget_event = None
    events = []
    malformed_lines = []
    process_error = None
    killed_at = None

    def consume(line):
        nonlocal terminal, budget_event
        if not line.strip():
            return
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("event is not an object")
        except ValueError:
            malformed_lines.append(line.decode(errors="replace"))
            return
        events.append(event)
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") not in (None, "agent_message", "reasoning", "todo_list", "error"):
            identity = item.get("id")
            if not isinstance(identity, str):
                identity = f"missing-id-event-{len(events)}"
            observed[identity] = item["type"]
        if event.get("type") in ("turn.completed", "turn.failed"):
            terminal = event["type"]
            usage_events.append({"type": terminal, "usage": event.get("usage")})
        if budget_event is None and len(observed) > manifest["observed_tool_limit"]:
            budget_event = {"reason": "observed_tool_limit", "observed": len(observed),
                            "elapsed_seconds": time.monotonic() - started,
                            "limitation": "detected after a streamed item event; limit may be exceeded by an already-started operation"}

    with (directory / "events.jsonl").open("wb") as raw, (directory / "stderr.txt").open("wb") as stderr:
        try:
            child = subprocess.Popen(cmd, cwd=work, env=environment(campaign), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
        except OSError as exc:
            return {"exit_code": None, "launch_error": str(exc), "wall_seconds": time.monotonic() - started,
                    "usage": None, "usage_events": [], "observed_tool_items": 0,
                    "terminal_event": None, "budget_event": None, "censored": False}
        assert child.stdout
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        buffer = b""
        eof = False
        try:
            # EOF does not mean the leader exited; conversely an exited leader
            # may leave descendants holding this pipe. Bound both situations.
            while not (eof and child.poll() is not None):
                elapsed = time.monotonic() - started
                if budget_event is None and elapsed >= manifest["wall_limit_seconds"]:
                    budget_event = {"reason": "wall_deadline", "elapsed_seconds": elapsed}
                if budget_event is not None and killed_at is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    killed_at = time.monotonic()
                if killed_at is not None and time.monotonic() - killed_at > 2:
                    process_error = "stdout drain or descendant exit exceeded two-second post-kill grace"
                    break
                if eof:
                    time.sleep(0.02)
                    continue
                for key, _mask in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        eof = True
                        break
                    raw.write(chunk)
                    raw.flush()
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        consume(line)
            if buffer:
                consume(buffer)
        finally:
            selector.close()
            child.stdout.close()
            # End any remaining processes in this session's group even when
            # the CLI leader has already exited normally.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process_error = "agent leader did not exit after forced termination"
    return {"exit_code": child.returncode, "wall_seconds": time.monotonic() - started,
            "usage": usage_events[-1]["usage"] if usage_events else None, "usage_events": usage_events,
            "observed_tool_items": len(observed), "observed_item_types": observed,
            "tool_count_definition": "unique non-message/reasoning/todo/error item IDs; missing IDs conservatively count per event",
            "terminal_event": terminal, "budget_event": budget_event,
            "censored": budget_event is not None, "process_error": process_error,
            "malformed_event_lines": malformed_lines,
            "thread_ids": [e["thread_id"] for e in events if e.get("type") == "thread.started" and "thread_id" in e]}


def run_trial(campaign: Path, trial: dict, manifest: dict) -> dict:
    directory = campaign / "trials" / trial["id"]
    directory.mkdir(parents=True, exist_ok=False)
    report = {"trial": trial, "mechanical_pass": False, "overall_status": "infrastructure_failure"}
    try:
        work = directory / "work"
        original = campaign / "baseline" / trial["language"]
        copy_source(original, work)
        before = inventory(work)
        dump(directory / "initial-inventory.json", before)
        print(f"START {trial['id']}", flush=True)
        session = agent_session(campaign, trial, manifest, directory)
        report["session"] = session
        dump(directory / "session.json", session)
        after = inventory(work)
        dump(directory / "final-inventory.json", after)
        # Preserve submitted source separately. Evaluation starts from another
        # clean copy, excluding every generated output/cache (including pyc).
        submitted = directory / "submitted"
        copy_source(work, submitted)
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        allowed = {"clarification.json"}
        if trial["task"] != "c":
            allowed.update(manifest["allowlists"][trial["language"]])
        unauthorized = [path for path in changed if path not in allowed]
        symlinks = [path for path, digest in after.items() if digest.startswith("symlink:")]
        diff = []
        for path in changed:
            old = (original / path).read_text(errors="replace").splitlines(keepends=True) if (original / path).is_file() else []
            new = (submitted / path).read_text(errors="replace").splitlines(keepends=True) if (submitted / path).is_file() and not (submitted / path).is_symlink() else []
            diff.extend(difflib.unified_diff(old, new, fromfile="before/" + path, tofile="after/" + path))
        (directory / "source.diff").write_text("".join(diff))
        report["integrity"] = {"mechanical_pass": False, "changed_files": changed,
                               "unauthorized_files": unauthorized, "symlinks": symlinks,
                               "source_semantic_review": "pending_review", "transcript_access_review": "pending_review"}
        if symlinks:
            raise RuntimeError("subject contains symlinks; refusing to execute an escaped source tree")
        evaluation_work = directory / "evaluation-work"
        copy_source(submitted, evaluation_work)
        evaluation = evaluate_subject(campaign, evaluation_work, trial["language"], trial["task"], manifest)
        report["evaluation"] = evaluation
        post_evaluation = inventory(evaluation_work)
        submitted_after = inventory(submitted)
        work_after = inventory(work)
        dump(directory / "post-evaluation-inventory.json", post_evaluation)
        dump(directory / "submitted-post-evaluation-inventory.json", submitted_after)
        dump(directory / "work-post-evaluation-inventory.json", work_after)
        mutations = sorted(path for path in after.keys() | post_evaluation.keys()
                           if after.get(path) != post_evaluation.get(path))
        snapshots_preserved = submitted_after == after and work_after == after
        mechanical_integrity = not unauthorized and not mutations and snapshots_preserved
        mechanical = (mechanical_integrity and checks_pass(evaluation) and session["budget_event"] is None
                      and session["exit_code"] == 0 and session["terminal_event"] == "turn.completed"
                      and not session.get("process_error") and not session.get("malformed_event_lines"))
        if trial["task"] == "c":
            mechanical = mechanical and evaluation["clarification"]["structure_pass"]
        report.update(integrity={"mechanical_pass": mechanical_integrity, "changed_files": changed,
                                "unauthorized_files": unauthorized, "symlinks": symlinks,
                                "evaluation_mutations": mutations, "submitted_and_work_preserved": snapshots_preserved,
                                "source_semantic_review": "pending_review", "transcript_access_review": "pending_review"},
                      mechanical_pass=bool(mechanical), overall_status="pending_independent_review")
    except BaseException as error:
        report["infrastructure_error"] = repr(error)
        report["traceback"] = traceback.format_exc()
    finally:
        dump(directory / "result.json", report)
    session = report.get("session", {})
    print(f"DONE {trial['id']}: mechanical={report['mechanical_pass']}, {session.get('wall_seconds')}s, {session.get('observed_tool_items')} tools", flush=True)
    return report


def run(campaign: Path, manifest: dict) -> None:
    if not (campaign / "baseline-pass.json").exists():
        raise RuntimeError("Run baseline validation first")
    baseline_gate = json.loads((campaign / "baseline-pass.json").read_text())
    if baseline_gate.get("pass") is not True or set(baseline_gate.get("languages", [])) != set(LANGUAGES):
        raise RuntimeError("Baseline validation record is incomplete or failed")
    if (campaign / "trials").exists():
        raise RuntimeError("Refusing to resume/overwrite measured attempts")
    campaign_error = None
    try:
        for block in sorted({row["block"] for row in manifest["schedule"]}):
            verify_freeze(campaign, manifest)
            rows = [row for row in manifest["schedule"] if row["block"] == block]
            with ThreadPoolExecutor(max_workers=manifest["parallelism"]) as pool:
                results = list(pool.map(lambda row: run_trial(campaign, row, manifest), rows))
            dump(campaign / f"block-{block}.json", {"trial_ids": [r["trial"]["id"] for r in results]})
        verify_freeze(campaign, manifest)
    except BaseException as error:
        campaign_error = repr(error)
    finally:
        for trial in manifest["schedule"]:
            result_path = campaign / "trials" / trial["id"] / "result.json"
            if not result_path.exists():
                dump(result_path, {"trial": trial, "mechanical_pass": False,
                                   "overall_status": "not_completed_campaign_failure",
                                   "infrastructure_error": campaign_error or "trial did not retain a result"})
        dump(campaign / "execution-complete.json", {"scheduled": len(manifest["schedule"]),
             "status": "campaign_failure" if campaign_error else "requires_independent_review",
             "campaign_error": campaign_error})
    if campaign_error:
        raise RuntimeError(campaign_error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "baseline", "run"))
    parser.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    if args.action == "freeze":
        freeze(campaign)
        return
    manifest = json.loads((campaign / "manifest.json").read_text())
    verify_freeze(campaign, manifest)
    {"baseline": baseline, "run": run}[args.action](campaign, manifest)


if __name__ == "__main__":
    main()
