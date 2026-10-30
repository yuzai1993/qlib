"""Compatibility entry point for the unified, fail-closed cleanup dry-run.

An exported script re-runs the validated cleaner when explicitly executed.
It never contains an independently computed list of rm commands.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Sequence

import cleanup_experiment_artifacts as cleanup

EXP_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Unified artifact cleanup dry-run")
    parser.add_argument("--repo-root", type=Path, default=EXP_ROOT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--emit-script", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().absolute()
    registry = args.registry or repo_root / "backtest/experiments/registry.jsonl"
    plan = cleanup.build_cleanup_plan(repo_root, cleanup.load_registry(registry))
    print(json.dumps(cleanup._jsonable(plan), ensure_ascii=False, indent=2))
    if args.emit_script:
        if plan["errors"]:
            raise ValueError(
                "cleanup plan contains retention errors; refusing script export"
            )
        command = [
            sys.executable,
            str(Path(cleanup.__file__).resolve()),
            "--repo-root",
            str(repo_root),
            "--registry",
            str(registry.resolve()),
            "--apply",
        ]
        args.emit_script.write_text(
            "#!/bin/sh\nset -eu\nexec " + shlex.join(command) + "\n", encoding="utf-8"
        )
        print(
            f"Validated cleanup command exported (not executed): {args.emit_script}",
            file=sys.stderr,
        )
    else:
        print("dry-run only", file=sys.stderr)


if __name__ == "__main__":
    main()
