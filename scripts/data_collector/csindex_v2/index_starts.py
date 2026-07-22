"""各指数成分覆盖起始日：检测逻辑与元数据输出。

策略（2026-07）：
  csi300   全量官方公告，2005-07-01 生效的锚点按公告日 2005-06-22 正推
             （2005-04-08 初始名单反推）
  csi500   官方公告为主；两条快速纳入由人工审核的 Tushare 月末快照补录；
             自「无缺失定期调样」的最早一期起算（2015-12-14 生效）
  csi1000  仅官方公告；同上（2015-12-14 生效，上市初期 2015-06 无完整公告）
  csi2000  Tushare 月末差分，初始名单按公告日 2023-08-10 正推

输出: changes/index_starts.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config as cfg

INDEX_LAUNCH = {
    "csi300": "2005-04-08",
    "csi500": "2007-01-15",
    "csi1000": "2014-10-17",
    "csi2000": "2023-08-11",
}

# 已知缺口说明（官方-only 模式下，起始日之前的不可重建区间）
KNOWN_GAPS = {
    "csi500": "2010-07-01 ~ 2015-06-15：11 期定期调样仅有 news 摘要，无完整名单",
}

CSI300_ANCHOR = {
    "effective_date": "2005-07-01",
    "announce_date": "2005-06-22",
    "source_id": "6773",
    "note": "2005-07-01 全量名单正推；2005-04-08 初始名单由首次调样反推",
}

CSI2000_ANCHOR = {
    "effective_date": "2023-08-11",
    "announce_date": "2023-08-10",
    "source_id": "14883",
    "note": "发布日初始 2000 只名单 + Tushare 差分补齐后续变更",
}


def _periodic_dates(changes: pd.DataFrame, expected: int) -> list[tuple[str, dict]]:
    """识别定期调样生效日（大规模、进出平衡）。"""
    min_count = max(40, int(expected * 0.08))
    out: list[tuple[str, dict]] = []
    for eff, grp in changes.groupby("effective_date"):
        n_add = int((grp["type"] == "add").sum())
        n_remove = int((grp["type"] == "remove").sum())
        if (
            n_add >= min_count
            and n_remove >= min_count
            and abs(n_add - n_remove) <= max(2, int(expected * 0.01))
        ):
            ann = grp["announce_date"].dropna()
            sid = grp["source_id"].dropna()
            out.append((
                str(eff)[:10],
                {
                    "announce_date": str(ann.iloc[0])[:10] if len(ann) else str(eff)[:10],
                    "source_id": str(sid.iloc[0]) if len(sid) else None,
                    "n_add": n_add,
                    "n_remove": n_remove,
                    "sources": sorted(set(grp["source"])),
                },
            ))
    out.sort(key=lambda x: x[0])
    return out


def detect_official_continuous_start(
    changes: pd.DataFrame, expected: int, gap_days: int = 200
) -> tuple[str, dict]:
    """找「之后无大缺口」的最早定期调样生效日。"""
    periodic = _periodic_dates(changes, expected)
    if not periodic:
        raise ValueError("未找到任何定期调样记录")

    split_at = 0
    max_gap = 0
    for i in range(1, len(periodic)):
        gap = (pd.Timestamp(periodic[i][0]) - pd.Timestamp(periodic[i - 1][0])).days
        if gap > max_gap:
            max_gap = gap
            split_at = i

    if max_gap > gap_days:
        eff, meta = periodic[split_at]
    else:
        eff, meta = periodic[0]
    return eff, meta


def build_index_start_records(
    changes: pd.DataFrame,
    date_mode: str = "announce",
) -> dict[str, dict]:
    """为四个指数生成起始日元数据。"""
    records: dict[str, dict] = {}

    # csi300
    records["csi300"] = {
        "data_source": "official_only",
        "index_launch": INDEX_LAUNCH["csi300"],
        "coverage_start_effective": CSI300_ANCHOR["effective_date"],
        "coverage_start_announce": CSI300_ANCHOR["announce_date"],
        "coverage_start": INDEX_LAUNCH["csi300"],
        "source_id": CSI300_ANCHOR["source_id"],
        "build_mode": "forward_from_anchor",
        "note": CSI300_ANCHOR["note"],
    }

    # csi500 / csi1000：自动检测官方连续覆盖起点；csi500 另含两条人工快照修正
    for idx in ("csi500", "csi1000"):
        sub = changes[changes["index_name"] == idx].copy()
        expected = cfg.INDEX_META[idx]["expected_size"]
        eff, meta = detect_official_continuous_start(sub, expected)
        start = meta["announce_date"] if date_mode == "announce" else eff
        rec = {
            "data_source": (
                "official_with_manual_tushare_snapshot_fixes"
                if idx == "csi500"
                else "official_only"
            ),
            "index_launch": INDEX_LAUNCH[idx],
            "coverage_start_effective": eff,
            "coverage_start_announce": meta["announce_date"],
            "coverage_start": start,
            "source_id": meta["source_id"],
            "build_mode": "backward_from_snapshot_at_start",
            "first_periodic": meta,
            "note": (
                f"自 id={meta['source_id']} 起官方定期调样无缺失；"
                f"早于此日的成分不可仅靠公告重建"
            ),
        }
        if idx in KNOWN_GAPS:
            rec["gap_before_start"] = KNOWN_GAPS[idx]
        records[idx] = rec

    # csi2000
    records["csi2000"] = {
        "data_source": "tushare",
        "index_launch": INDEX_LAUNCH["csi2000"],
        "coverage_start_effective": CSI2000_ANCHOR["effective_date"],
        "coverage_start_announce": CSI2000_ANCHOR["announce_date"],
        "coverage_start": (
            CSI2000_ANCHOR["announce_date"]
            if date_mode == "announce"
            else CSI2000_ANCHOR["effective_date"]
        ),
        "source_id": CSI2000_ANCHOR["source_id"],
        "build_mode": "forward_from_anchor",
        "note": CSI2000_ANCHOR["note"],
    }
    return records


def write_index_starts(
    changes: pd.DataFrame,
    date_mode: str = "announce",
    dest: Path | None = None,
) -> dict:
    """写入 changes/index_starts.json，返回完整文档。"""
    dest = dest or (cfg.CHANGES_DIR / "index_starts.json")
    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_mode": date_mode,
        "date_mode_note": (
            "coverage_start 与 instruments 区间起点均使用公告日；"
            "缺公告日时回退生效日"
            if date_mode == "announce"
            else "coverage_start 与 instruments 区间起点均使用生效日"
        ),
        "indices": build_index_start_records(changes, date_mode),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return doc
