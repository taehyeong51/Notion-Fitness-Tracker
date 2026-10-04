"""python -m notion inspect|plan|apply|watch; plan never writes to Notion."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

from .api import Client, NotionError
from .deploy import Deploy, save
from .metrics import SEOUL, build
from .source import check_config, normalize, title


def inspect(client, output):
    # Return only source schemas and page IDs, not private workout rows.
    found = []
    for name in ["Fitness Tracker", "Workout Sessions", "Exercise Sets", "Exercise Library"]:
        for record in client.search(name):
            if title(record) != name:
                continue
            entry = {"title": name, "object": record["object"], "id": record["id"],
                     "parent": record.get("parent"), "url": record.get("url")}
            if record["object"] == "data_source":
                schema = client.request("GET", "/data_sources/" + record["id"])
                entry["properties"] = schema["properties"]
            found.append(entry)
    save(Path(output), {"sources": found})
    return {"metadata_file": output, "exact_title_matches": len(found),
            "message": "Verify parent and schema, then fill config; no source was changed."}


def run_once(args, client=None):
    client = client or Client()
    if args.command == "inspect":
        return inspect(client, args.output)
    config = json.loads(Path(args.config).read_text())
    if args.command == "prepare":
        from copy import deepcopy
        from .prepare import prepare
        inspected = deepcopy(config)
        inspected["properties"]["sets"]["historical_condition"] = None
        check_config(client, inspected)
        return {"display_properties": prepare(client, config, Path(args.config))}
    check_config(client, config)
    if args.command == "verify":
        from .verify import run
        result = run(client, config, args.state, args.original)
        save(Path(args.output), result)
        return {"verification_file": args.output, **{name: result[name] for name in ['source_counts', 'formula_checks', 'original_preservation', 'native_chart_checks']}}
    if args.command == "live-check":
        from .live_check import run
        state = json.loads(Path(args.state).read_text())
        result = run(client, config, state['pages']['관리·집계'])
        save(Path(args.output), result)
        return result
    snapshot = normalize(client, config)
    projection = build(snapshot, config.get("baselines", []))
    diagnostics = projection["diagnostics"]
    if args.command == "plan":
        return {"read_only": True, "diagnostics": diagnostics,
                "projection_counts": {role: len(projection[role]) for role in ["sessions", "sets", "membership", "progress", "weekly"]}}
    config["top10_exercise_ids"] = sorted({row["original_exercise"][0] for row in projection["sets"]
                                          if row["top10"] and row["original_exercise"]})
    config["top10_updated_at"] = datetime.now(SEOUL).isoformat(timespec="seconds")
    config["exercise_labels"] = {row["id"]: row["label"] for row in snapshot["exercises"]}
    save(Path(args.config), config)
    result = Deploy(client, config, args.state).apply(projection)
    return {**result, "diagnostics": diagnostics}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Official Notion-native chart automation; no HTML")
    parser.add_argument("command", choices=["inspect", "prepare", "plan", "apply", "verify", "live-check", "watch"])
    parser.add_argument("--config", default=".local/notion-config.json")
    parser.add_argument("--state", default=".local/notion-state.json")
    parser.add_argument("--output")
    parser.add_argument("--original", help="Optional private before-snapshot for read-only preservation verification")
    parser.add_argument("--interval", type=int, default=90)
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = '.local/notion-' + {'inspect':'discovery','verify':'verification','live-check':'live-check'}.get(args.command, 'output') + '.json'
    if args.interval < 60:
        parser.error("watch interval must be at least 60 seconds")
    if args.command != "watch":
        try:
            result = run_once(args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (NotionError, ValueError, KeyError, OSError) as error:
            print("Blocked: " + str(error), file=sys.stderr)
            return 1
    try:
        client = Client()
    except NotionError as error:
        print("Blocked: " + str(error), file=sys.stderr)
        return 1
    while True:
        started = time.monotonic()
        try:
            result = run_once(args, client)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except (NotionError, ValueError, KeyError, OSError) as error:
            # A failed scan before apply also needs to mark any previous snapshot stale.
            state_path = Path(args.state)
            if state_path.exists():
                try:
                    config = json.loads(Path(args.config).read_text())
                    deployment = Deploy(client, config, args.state)
                    if deployment.state["status_blocks"]:
                        deployment.status("원본 조회/집계 실패 · 마지막 성공: " + deployment.state.get("last_success", "없음"))
                except Exception:
                    pass
            print(json.dumps({"failed_at": datetime.now(SEOUL).isoformat(), "error": str(error)}, ensure_ascii=False), flush=True)
        remaining = max(1, args.interval - (time.monotonic() - started))
        # A live process must be restarted in each new cloud task.
        while remaining > 0:
            wait = min(remaining, 30)
            time.sleep(wait)
            remaining -= wait


if __name__ == "__main__":
    raise SystemExit(main())
