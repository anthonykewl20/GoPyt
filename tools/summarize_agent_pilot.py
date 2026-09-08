#!/usr/bin/env python3
"""Derive descriptive pilot metrics from retained trial results and reviews."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import statistics


def summarize(campaign: Path) -> dict:
    manifest = json.loads((campaign / "manifest.json").read_text())
    reviews = {}
    for path in sorted((campaign / "reviews").glob("*.json")):
        document = json.loads(path.read_text())
        for row in document.get("trials", []):
            reviews.setdefault(row["id"], {}).update(row)
    rows = []
    for trial in manifest["schedule"]:
        path = campaign / "trials" / trial["id"] / "result.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        session = result.get("session") or {}
        evaluation = result.get("evaluation") or {}
        behavior = evaluation.get("behavior") or {}
        review = reviews.get(trial["id"], {})
        implementation_review = review.get("source_integrity_pass")
        access_review = review.get("access_review_pass")
        clarification_review = review.get("clarification_semantic_pass")
        applicable = [implementation_review, access_review]
        if trial["task"] == "c":
            applicable.append(clarification_review)
        final = result.get("mechanical_pass") is True and all(value is True for value in applicable)
        usage = session.get("usage") or {}
        rows.append({**trial, "disposition": "missing" if not result else ("infrastructure_error" if result.get("infrastructure_error") else "recorded"),
                     "static_pass": evaluation.get("static", {}).get("pass"),
                     "public_pass": evaluation.get("public", {}).get("pass"),
                     "business_pass": behavior.get("business_pass"),
                     "business_cases": behavior.get("case_count", 0),
                     "business_cases_passed": sum(group["passed"] for group in behavior.get("groups", {}).values()),
                     "mechanical_pass": result.get("mechanical_pass"),
                     "source_integrity_pass": implementation_review, "access_review_pass": access_review,
                     "clarification_semantic_pass": clarification_review,
                     "reviews_complete": all(value is not None for value in applicable), "final_pass": final,
                     "wall_seconds": session.get("wall_seconds"), "observed_tool_items": session.get("observed_tool_items"),
                     "input_tokens": usage.get("input_tokens"), "cached_input_tokens": usage.get("cached_input_tokens"),
                     "output_tokens": usage.get("output_tokens"), "budget_event": session.get("budget_event")})
    aggregates = {}
    for language in ("gopyt", "python", "typescript"):
        cells = {}
        for task in ("a", "b", "c"):
            group = [r for r in rows if r["language"] == language and r["task"] == task]
            metrics = {}
            for name in ("wall_seconds", "observed_tool_items", "input_tokens", "cached_input_tokens", "output_tokens"):
                values = [r[name] for r in group if r[name] is not None]
                metrics[name] = {"observed": len(values), "median": statistics.median(values) if values else None,
                                 "min": min(values) if values else None, "max": max(values) if values else None}
            cells[task] = {"scheduled": len(group), "final_pass": sum(r["final_pass"] for r in group),
                           "mechanical_pass": sum(r["mechanical_pass"] is True for r in group),
                           "reviews_complete": all(r["reviews_complete"] for r in group), "metrics_all_dispositions": metrics}
        aggregates[language] = cells
    return {"schema": "quotation-v1-descriptive-summary", "scheduled_trials": len(rows),
            "unit_of_analysis": "fresh agent trial; oracle cases are not independent agent trials",
            "resource_scope": "all dispositions, including failures; CLI input tokens can include repeated/cached context",
            "rows": rows, "by_language_and_task": aggregates}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    summary = summarize(args.campaign)
    (args.campaign / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with (args.campaign / "trials.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(summary["rows"][0]))
        writer.writeheader()
        writer.writerows(summary["rows"])
    print(json.dumps({"scheduled": summary["scheduled_trials"], "final_pass": sum(r["final_pass"] for r in summary["rows"]),
                      "reviews_complete": all(r["reviews_complete"] for r in summary["rows"])}, indent=2))


if __name__ == "__main__":
    main()
