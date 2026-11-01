"""Presentation labels that never change research identities or frozen versions."""

import re


def strategy_display_version(value: object, *, kind: str = "strategy", track: str = "all_a") -> str:
    """Separate all-A strategy labels (sN) from model labels (vN)."""
    text = value if isinstance(value, str) else ""
    if kind != "strategy" or track != "all_a":
        return text
    matched = re.fullmatch(r"(?:BT\s+)?v(\d+(?:\.\d+)*)", text.strip())
    return "s" + matched.group(1) if matched else text


def strategy_reference_text(value: object, *, kind: str = "strategy", track: str = "all_a") -> str:
    """Relabel explicit BT vN references in display text; preserve bare model vN."""
    text = value if isinstance(value, str) else ""
    if kind != "strategy" or track != "all_a":
        return text
    return re.sub(r"(?<![A-Za-z0-9_])BT\s+v(\d+(?:\.\d+)*)(?![A-Za-z0-9_])",
                  lambda match: "s" + match.group(1), text)
