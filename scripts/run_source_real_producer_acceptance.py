#!/usr/bin/env python3
"""Run one unified Math/CS408/English source-mode acceptance round."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from source_real_producer_acceptance import (  # noqa: E402
    PRODUCER_MODES,
    SourceAcceptanceError,
    load_json,
    run_acceptance,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--source-root", type=Path, required=True)
    value.add_argument("--expected-source-root", type=Path)
    value.add_argument("--expected-branch")
    value.add_argument("--expected-head")
    value.add_argument("--producer-mode", choices=PRODUCER_MODES, required=True)
    value.add_argument("--config", type=Path, required=True)
    value.add_argument("--output-root", type=Path, required=True)
    value.add_argument("--barrier-timeout-seconds", type=float, default=30)
    value.add_argument("--enable-real-provider", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        config = load_json(args.config)
        if not isinstance(config, dict):
            raise SourceAcceptanceError("source_acceptance_config_invalid")
        summary = run_acceptance(
            source_root=args.source_root,
            expected_source_root=args.expected_source_root,
            expected_branch=args.expected_branch,
            expected_head=args.expected_head,
            output_root=args.output_root,
            producer_mode=args.producer_mode,
            config=config,
            enable_real_provider=args.enable_real_provider,
            barrier_timeout_seconds=args.barrier_timeout_seconds,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary.get("status") == "passed" else 1
    except (SourceAcceptanceError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": str(exc),
                    "formal_write_count": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
