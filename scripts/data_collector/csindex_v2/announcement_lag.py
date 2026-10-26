"""Evidence for differences between announcement rosters and effective snapshots."""

from __future__ import annotations

import html
import json
import re

import pandas as pd

from . import config as cfg
from .detail_revisions import reviewed_correction_source
from .parser_content import (
    EFFECTIVE_PATTERNS,
    EFFECTIVE_PATTERNS_NO_YEAR,
    extract_effective_date,
    normalize_symbol,
)


def snapshot_lag_evidence(index_name: str, snap_date: str, boundaries: dict) -> dict:
    """Classify only symbols in the diff, using their latest published event.

    An approximate aggregator date is not a confirmed effective date. Conditional
    notices can remain pending beyond 25 days, until an official snapshot has
    observed the transition or a later dated announcement sets a deadline.
    """
    changes_path = cfg.PARSED_DIR / "all_changes.csv"
    if not boundaries or not changes_path.exists():
        return {}
    changes = pd.read_csv(changes_path, dtype=str)
    changes = changes.loc[
        (changes["index_name"] == index_name)
        & changes["symbol"].isin(boundaries)
        & (changes["announce_date"] <= snap_date)
    ]
    latest = changes.sort_values(["announce_date", "effective_date", "source_id"]).drop_duplicates(
        "symbol", keep="last"
    )
    evidence = {}
    for row in latest.to_dict("records"):
        symbol = row["symbol"]
        direction, boundary = boundaries[symbol]
        if row["type"] != direction:
            evidence[symbol] = {"status": "expired", "reason": "newer_opposite_event", "source_id": row["source_id"]}
            continue
        reviewed_source = reviewed_correction_source(row)
        if reviewed_source:
            # Audited revisions retain the old notice, so its publication date
            # cannot validate this newer delta. Use the exact reviewed deadline.
            effective = row["effective_date"]
            item = {
                "source_id": reviewed_source,
                "announce_date": row["announce_date"],
                "type": direction,
                "effective_date": effective,
                "status": "pending" if snap_date < effective else "expired",
                "reason": "reviewed_revision",
                "after_close": False,
            }
        else:
            if row["source"] not in {"excel", "pdf", "content"}:
                continue
            path = cfg.DETAILS_DIR / f"{row['source_id']}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text()).get("data") or {}
            if str(data.get("id")) != row["source_id"] or data.get("publishDate") != row["announce_date"]:
                continue
            text = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", data.get("content") or "")))
            # The general parser has title/first-date/month-start heuristics for old data.
            # An exemption/deadline requires an explicit effective-date context.
            dated_context = any(re.search(pattern, text) for pattern in EFFECTIVE_PATTERNS + EFFECTIVE_PATTERNS_NO_YEAR)
            effective = extract_effective_date(text, row["announce_date"]) if dated_context else None
            item = {
                "source_id": row["source_id"],
                "announce_date": row["announce_date"],
                "type": direction,
                "effective_date": effective,
            }
            if effective and "approx_date" not in str(row.get("method", "")):
                stamp = pd.Timestamp(effective)
                after_close = bool(re.search(rf"(?:{stamp.year}年)?0?{stamp.month}月0?{stamp.day}日收市后", text))
                pending = snap_date <= effective if after_close else snap_date < effective
                item.update(status="pending" if pending else "expired", reason="dated_notice", after_close=after_close)
            elif effective is None and re.search(
                r"(?:自|于)[^。；;]{0,120}(?:退市|终止上市|上市)(?:生效)?(?:之日|日)(?:起)?", text
            ):
                item.update(status="pending", reason="conditional_notice")
            else:
                continue
        # Use the current interval boundary, not the latest duplicate notice's
        # date, so a repeated same-side notice cannot reset an observed transition.
        history = changes.loc[changes["symbol"] == symbol]
        opposite = history.loc[history["type"] != direction, "announce_date"]
        same_side = history.loc[history["type"] == direction, "announce_date"]
        if not opposite.empty:
            same_side = same_side[same_side > opposite.max()]
        first_announcement = same_side.min() if not same_side.empty else row["announce_date"]
        item["observation_start"] = max(boundary, first_announcement)
        evidence[symbol] = item

    pending = {s: e for s, e in evidence.items() if e["status"] == "pending"}
    if pending:
        floor = min(e["observation_start"] for e in pending.values())
        code = cfg.INDEX_META[index_name]["code"]
        for path in sorted(cfg.SNAPSHOTS_DIR.glob(f"daily/*/{code}cons.xls")):
            folder = path.parent.name
            if not re.fullmatch(r"\d{8}", folder) or not floor.replace("-", "") <= folder <= snap_date.replace("-", ""):
                continue
            frame = pd.read_excel(path, dtype=str)
            observed = pd.to_datetime(str(frame.iloc[0, 0]), format="%Y%m%d").strftime("%Y-%m-%d")
            if not floor <= observed <= snap_date:
                continue
            members = {normalize_symbol(s) for s in frame.iloc[:, 4]}
            for symbol, item in pending.items():
                if item["status"] != "pending" or observed < item["observation_start"]:
                    continue
                transitioned = (symbol in members) if item["type"] == "add" else (symbol not in members)
                if transitioned:
                    item.update(status="expired", reason="transition_already_observed", observed_on=observed)
    return evidence
