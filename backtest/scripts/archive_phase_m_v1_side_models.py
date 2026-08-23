"""归档 Phase M v1 总报告里不再对比的侧翼模型（旧 ST / 非 baseline 版本）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from register_regime_m0_labels import load_registry, write_registry

KEEP = {
    "regime-adapt/m0-h20-label-v4",
    "regime-adapt/m0-h20-t5h5-es-v1",
}
ARCHIVE = {
    "regime-adapt/m0-h1-label-v4",
    "regime-adapt/m0-h2-label-v4",
    "regime-adapt/m0-h3-label-v4",
    "regime-adapt/m0-h5-label-v4",
    "regime-adapt/m0-h10-label-v4",
    "regime-adapt/m0-h40-label-v4",
    "regime-adapt/m0-h20-regime-feat-v1",
    "regime-adapt/m0-h20-sample-v1",
    "regime-adapt/m0-h20-densemble-s42-v1",
    "regime-adapt/m0-h20-st-daily-reeval",
}
VERSIONS = {
    "regime-adapt/m0-h20-label-v4": "v1",
    "regime-adapt/m0-h20-t5h5-es-v1": "v2",
}


def main() -> None:
    rows = load_registry()
    n_arch = n_ver = 0
    for row in rows:
        eid = row.get("exp_id")
        if eid in ARCHIVE:
            row["state"] = "archived"
            row.pop("phase_m_protocol", None)
            prev = row.get("note") or ""
            tag = "2026-08-19 移出 Phase M v1 总报告对比（未按日频 ST 重评）"
            if tag not in str(prev):
                row["note"] = f"{tag}；{prev}" if prev else tag
            n_arch += 1
        if eid in VERSIONS:
            row["baseline_version"] = VERSIONS[eid]
            n_ver += 1
        if eid in KEEP and row.get("state") == "archived":
            row["state"] = "completed"
    write_registry(rows)
    print(f"archived={n_arch} versioned={n_ver}")


if __name__ == "__main__":
    main()
