#!/usr/bin/env python
"""训练起点扫描：train end 固定，start 从 2004..2017 依次训练+回测。"""
from __future__ import annotations

import argparse
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
DEFAULT_BASE = ROOT / "backtest" / "configs" / "csi300_lgbm_train_start_sweep.yaml"
RESULT_ROOT = ROOT / "backtest" / "result"
PYTHON = sys.executable


def parse_args():
    p = argparse.ArgumentParser(description="训练起点扫描（2004-2017）")
    p.add_argument("--base-config", type=str, default=str(DEFAULT_BASE))
    p.add_argument("--years", type=str, default="2004-2017", help="如 2004-2017 或 2004,2005,2010")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def parse_years(spec: str) -> list[int]:
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x.strip()) for x in spec.split(",") if x.strip()]


def build_cfg(base: dict, year: int) -> dict:
    cfg = yaml.safe_load(yaml.dump(base))  # deep copy
    train_start = f"{year}-01-02"
    train_end = cfg["segments"]["train"][1]
    cfg["run"]["mode"] = "train_backtest"
    cfg["run"]["note"] = f"train_start_{year}"
    cfg["run"]["generate_figures"] = False
    cfg["segments"]["train"] = [train_start, train_end]
    cfg["data"]["handler"]["fit_start_time"] = train_start
    cfg["data"]["handler"]["fit_end_time"] = train_end
    # handler.start_time 保持基线（滚动窗口需要 train 起点之前的历史）
    return cfg


def latest_session_for_note(note: str) -> Path | None:
    matches = sorted(RESULT_ROOT.glob(f"*_{note}"), key=lambda p: p.name)
    return matches[-1] if matches else None


def read_metrics(session_dir: Path) -> dict:
    metrics_path = session_dir / "run_01" / "metrics.json"
    summary_path = session_dir / "summary.json"
    out = {"session": session_dir.name, "status": "unknown"}
    if metrics_path.is_file():
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
        out.update(m)
    elif summary_path.is_file():
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        out["status"] = "success" if s.get("success_runs") else "failed"
        out.update(s.get("metrics_mean") or {})
    return out


def main():
    args = parse_args()
    base_path = Path(args.base_config).expanduser().resolve()
    if not base_path.is_file():
        print(f"基线配置不存在: {base_path}", file=sys.stderr)
        sys.exit(2)

    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    years = parse_years(args.years)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sweep_dir = RESULT_ROOT / f"{stamp}_train_start_sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = sweep_dir / "sweep_summary.csv"

    print(f"扫描目录: {sweep_dir}")
    print(f"基线: {base_path}")
    print(f"年份: {years}")
    print(f"train end: {base['segments']['train'][1]}")

    rows: list[dict] = []
    for i, year in enumerate(years, 1):
        cfg = build_cfg(base, year)
        note = cfg["run"]["note"]
        print("\n" + "=" * 60)
        print(f"[{i}/{len(years)}] train_start={year}-01-02  note={note}")
        print("=" * 60)

        if args.dry_run:
            print(yaml.dump(cfg["segments"], allow_unicode=True))
            rows.append({"year": year, "note": note, "status": "dry_run"})
            continue

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=f"_train_start_{year}.yaml",
            dir=str(SCRIPTS),
            delete=False,
            encoding="utf-8",
        ) as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
            tmp_cfg = Path(f.name)

        try:
            proc = subprocess.run(
                [PYTHON, str(SCRIPTS / "run_backtest.py"), "--config", str(tmp_cfg)],
                cwd=str(ROOT),
                check=False,
            )
            session = latest_session_for_note(note)
            row = {"year": year, "note": note, "exit_code": proc.returncode}
            if session is None:
                row["status"] = "no_session"
                row["session"] = ""
            else:
                m = read_metrics(session)
                row.update(m)
                row["session"] = session.name
            rows.append(row)
            print(f"[{year}] exit={proc.returncode} session={row.get('session')} "
                  f"IR={row.get('excess_with_cost_information_ratio')} "
                  f"ann={row.get('excess_with_cost_annualized_return')}")
        finally:
            tmp_cfg.unlink(missing_ok=True)

    # 写汇总
    fieldnames = [
        "year", "note", "session", "status", "exit_code",
        "excess_with_cost_information_ratio",
        "excess_with_cost_annualized_return",
        "excess_with_cost_max_drawdown",
        "excess_no_cost_information_ratio",
        "excess_no_cost_annualized_return",
        "portfolio_cum_return",
        "benchmark_cum_return",
        "excess_cum_return",
    ]
    # 补齐出现过的字段
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)

    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    (sweep_dir / "sweep_meta.json").write_text(
        json.dumps(
            {
                "base_config": str(base_path),
                "years": years,
                "train_end": base["segments"]["train"][1],
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "summary_csv": str(summary_csv),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print("扫描完成")
    print(f"汇总: {summary_csv}")
    print("=" * 60)
    for r in rows:
        print(
            f"  {r.get('year')}: status={r.get('status')} "
            f"IR={r.get('excess_with_cost_information_ratio')} "
            f"ann={r.get('excess_with_cost_annualized_return')} "
            f"session={r.get('session')}"
        )


if __name__ == "__main__":
    main()
