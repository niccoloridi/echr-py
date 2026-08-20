"""Run a tiny explicit-provider study canary for release validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hudoc_py.studies import BudgetSpec, SourceSpec, StageSpec, StudyRunner, StudySpec


def run_canary(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.out).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"canary output is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.jsonl"
    source.write_text(
        "".join(
            json.dumps({"itemid": f"canary-{index}", "text": f"Record {index}."}) + "\n"
            for index in range(1, 4)
        ),
        encoding="utf-8",
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    stage = StageSpec(
        id="canary",
        kind="extract",
        provider=args.provider,
        model=args.model,
        prompt="Return JSON with ok set to true for {{source_id}}.",
        response_schema=schema,
        temperature=0,
        max_output_tokens=128,
        concurrency=2 if args.mode == "realtime" else 1,
        batch=args.mode == "batch",
    )
    spec = StudySpec(
        id=f"release-canary-{args.provider}-{args.mode}",
        version="1",
        source=SourceSpec(path=str(source)),
        stages=[stage],
        budget=BudgetSpec(
            max_requests=3,
            max_input_tokens=5_000,
            max_output_tokens=1_000,
            max_usd=args.max_usd,
        ),
        response_schema=schema,
    )
    pricing = {
        f"{args.provider}:{args.model}": {
            "input_per_m": args.input_per_million,
            "output_per_m": args.output_per_million,
            "batch_discount": args.batch_discount,
        }
    }
    run = StudyRunner(spec, root / "run", pricing=pricing).run(
        resume=False,
        wait=args.mode == "batch",
        poll_interval=args.poll_interval,
        max_polls=args.max_polls,
    )
    summary = {
        "provider": args.provider,
        "model": args.model,
        "mode": args.mode,
        "status": run.status,
        "records": run.records,
        "errors": run.errors,
        "invalid": run.invalid,
        "requests": run.requests,
        "cost_usd": run.cost_usd,
    }
    (root / "canary-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if run.status != "complete" or run.records != 3 or run.errors or run.invalid:
        raise RuntimeError(f"provider canary failed: {summary}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["gemini", "openai"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=["realtime", "batch"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--input-per-million", type=float, required=True)
    parser.add_argument("--output-per-million", type=float, required=True)
    parser.add_argument("--batch-discount", type=float, default=0.5)
    parser.add_argument("--max-usd", type=float, default=1.0)
    parser.add_argument("--poll-interval", type=float, default=30.0)
    parser.add_argument("--max-polls", type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(run_canary(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
