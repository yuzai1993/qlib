#!/usr/bin/env python3
"""Render fail-closed QMT templates into locally bound deployment sources."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re


def _replace_setting(source: str, name: str, value: str) -> str:
    pattern = re.compile(
        rf"^(?P<prefix>{re.escape(name)}\s*=\s*)[^\n#]*?"
        rf"(?P<suffix>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} setting, found {len(matches)}")
    return pattern.sub(rf"\g<prefix>{value}\g<suffix>", source, count=1)


def _validate_account_id(account_id: str) -> str:
    value = str(account_id or "")
    if not value.isdigit() or not 6 <= len(value) <= 32:
        raise ValueError("account id must be 6-32 digits")
    return value


def render_main_source(source: str, account_id: str, expected_cash: float) -> str:
    account_id = _validate_account_id(account_id)
    cash = float(expected_cash)
    if not math.isfinite(cash) or cash < 0:
        raise ValueError("expected cash must be a finite non-negative number")
    settings = (
        ("ACCOUNT_ID", f'"{account_id}"'),
        ("STRATEGY_NAME", '"qlib_bridge_main"'),
        ("ACCOUNT_ENVIRONMENT", '"REAL"'),
        ("ALLOW_REAL_MONEY", "True"),
        ("REAL_EXPECTED_INITIAL_CASH", f"{cash:.2f}"),
        ("REAL_REQUIRE_EMPTY_POSITIONS", "False"),
    )
    rendered = source
    for name, value in settings:
        rendered = _replace_setting(rendered, name, value)
    return rendered


def render_pr49_source(source: str, account_id: str) -> str:
    account_id = _validate_account_id(account_id)
    return _replace_setting(source, "ACCOUNT_ID", f'"{account_id}"')


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with open(temporary, "x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-source", type=Path, required=True)
    parser.add_argument("--pr49-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-cash", type=float, required=True)
    args = parser.parse_args()

    account_id = os.environ.get("QMT_REAL_ACCOUNT_ID", "")
    main_text = render_main_source(
        args.main_source.read_text(encoding="utf-8"),
        account_id,
        args.expected_cash,
    )
    pr49_text = render_pr49_source(
        args.pr49_source.read_text(encoding="utf-8"), account_id,
    )
    _atomic_write(args.output_dir / args.main_source.name, main_text)
    _atomic_write(args.output_dir / args.pr49_source.name, pr49_text)
    print(f"rendered 2 QMT runtime sources to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
