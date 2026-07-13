#!/usr/bin/env python
"""topk=10 固定，扫描 n_drop；base=drop2 已有结果可跳过。"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
RESULT_ROOT = ROOT / "backtest" / "result"
BASE_CFG = ROOT / "backtest" / "configs" / "csi300_lgbm_bt_only_2006_top10.yaml"
PYTHON = sys.executable


def main():
    drops = [1, 3, 4, 5]  # 2 为 base，已有 session
    base = yaml.safe_load(BASE_CFG.read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sweep_dir = RESULT_ROOT / f"{stamp}_ndrop_sweep_top10"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    # 收录 base
    base_session = RESULT_ROOT / "20260712_223639_bt_only_2006_top10_drop2"
    bm = json.loads((base_session / "run_01" / "metrics.json").read_text(encoding="utf-8"))
    rows.append({
        "n_drop": 2,
        "note": "bt_only_2006_top10_drop2",
        "session": base_session.name,
        "status": bm.get("status", "success"),
        "is_base": True,
        **{k: bm.get(k) for k in bm if k.startswith(("excess_", "portfolio_", "benchmark_"))},
    })

    for i, nd in enumerate(drops, 1):
        cfg = yaml.safe_load(yaml.dump(base))
        cfg["run"]["note"] = f"bt_only_2006_top10_drop{nd}"
        cfg["run"]["generate_figures"] = False
        cfg["strategy"]["n_drop"] = nd
        print("\n" + "=" * 60)
        print(f"[{i}/{len(drops)}] n_drop={nd}")
        print("=" * 60)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=f"_drop{nd}.yaml", dir=str(SCRIPTS), delete=False, encoding="utf-8"
        ) as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
            tmp = Path(f.name)
        try:
            proc = subprocess.run(
                [PYTHON, str(SCRIPTS / "run_backtest.py"), "--config", str(tmp)],
                cwd=str(ROOT),
                check=False,
            )
        finally:
            tmp.unlink(missing_ok=True)

        matches = sorted(RESULT_ROOT.glob(f"*bt_only_2006_top10_drop{nd}"))
        # 排除 base 目录名若碰巧匹配；drop2 不在本循环
        session = matches[-1] if matches else None
        row = {"n_drop": nd, "note": cfg["run"]["note"], "exit_code": proc.returncode, "is_base": False}
        if session is None:
            row["status"] = "no_session"
            row["session"] = ""
        else:
            m = json.loads((session / "run_01" / "metrics.json").read_text(encoding="utf-8"))
            row["session"] = session.name
            row["status"] = m.get("status")
            for k, v in m.items():
                if k.startswith(("excess_", "portfolio_", "benchmark_")):
                    row[k] = v
        rows.append(row)
        print(
            f"[drop{nd}] IR={row.get('excess_with_cost_information_ratio')} "
            f"ann={row.get('excess_with_cost_annualized_return')} session={row.get('session')}"
        )

    # 选最优（非 base）：扣费超额 IR 优先，其次年化超额
    candidates = [r for r in rows if not r.get("is_base") and r.get("status") == "success"]
    best = max(
        candidates,
        key=lambda r: (
            float(r.get("excess_with_cost_information_ratio") or -1e9),
            float(r.get("excess_with_cost_annualized_return") or -1e9),
        ),
        default=None,
    )

    csv_path = sweep_dir / "ndrop_sweep_summary.csv"
    keys = [
        "n_drop", "is_base", "session", "status",
        "excess_with_cost_information_ratio",
        "excess_with_cost_annualized_return",
        "excess_with_cost_max_drawdown",
        "portfolio_annualized_return",
        "portfolio_information_ratio",
        "portfolio_max_drawdown",
        "portfolio_cum_return",
        "excess_cum_return",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: int(r["n_drop"])))

    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topk": 10,
        "base_n_drop": 2,
        "swept": drops,
        "best_n_drop": best["n_drop"] if best else None,
        "best_session": best["session"] if best else None,
        "summary_csv": str(csv_path),
    }
    (sweep_dir / "sweep_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("扫描完成")
    for r in sorted(rows, key=lambda x: int(x["n_drop"])):
        tag = " BASE" if r.get("is_base") else ""
        print(
            f"  drop={r['n_drop']}{tag}: IR={r.get('excess_with_cost_information_ratio')} "
            f"ann={r.get('excess_with_cost_annualized_return')} session={r.get('session')}"
        )
    if best:
        print(f"\n最优(非base): n_drop={best['n_drop']} session={best['session']}")
    print(f"汇总: {csv_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
