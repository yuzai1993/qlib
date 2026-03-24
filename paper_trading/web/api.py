"""FastAPI API routes for the paper trading dashboard."""

import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

import pandas as pd


def create_router(config: dict, project_root: Path) -> APIRouter:
    sys.path.insert(0, str(project_root / "paper_trading"))
    from modules.recorder import Recorder
    from modules.reporter import Reporter

    db_path = project_root / config["storage"]["db_path"]
    recorder = Recorder(str(db_path))
    initial_cash = config["paper_trading"]["initial_cash"]
    reporter = Reporter(recorder, initial_cash)

    router = APIRouter()

    @router.get("/overview")
    def overview():
        summary = recorder.get_latest_account_summary()
        if not summary:
            return {"error": "No data"}

        perf = reporter.calculate_performance()

        positions = recorder.get_positions()
        pos_list = positions.to_dict("records") if not positions.empty else []

        all_summaries = recorder.get_account_summary()
        trading_days = len(all_summaries) if not all_summaries.empty else 0
        # Exclude the 'init' row
        if not all_summaries.empty and all_summaries.iloc[0]["date"] == "init":
            trading_days -= 1

        return {
            "summary": summary,
            "performance": perf,
            "position_count": len(pos_list),
            "trading_days": trading_days,
        }

    @router.get("/account/summary")
    def account_summary(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
    ):
        df = recorder.get_account_summary(start=start, end=end)
        # Filter out the init row
        if not df.empty:
            df = df[df["date"] != "init"]
        return df.to_dict("records")

    @router.get("/positions/current")
    def current_positions():
        df = recorder.get_positions()
        names = recorder.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/positions")
    def positions(date: Optional[str] = Query(None)):
        df = recorder.get_positions(date)
        names = recorder.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/orders")
    def orders(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
        direction: Optional[str] = Query(None),
    ):
        df = recorder.get_orders(start=start, end=end, direction=direction)
        names = recorder.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/predictions")
    def predictions(date: Optional[str] = Query(None)):
        df = recorder.get_predictions(date)
        names = recorder.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/performance")
    def performance():
        return reporter.calculate_performance()

    @router.get("/performance/monthly")
    def monthly_performance():
        return reporter.monthly_returns()

    @router.get("/benchmark")
    def benchmark(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
    ):
        df = recorder.get_account_summary(start=start, end=end)
        if df.empty:
            return []
        cols = ["date", "benchmark_return", "benchmark_cumulative_return"]
        return df[cols].to_dict("records")

    @router.get("/stock/names")
    def stock_names():
        return recorder.get_stock_names()

    @router.get("/logs")
    def logs(limit: int = Query(100)):
        return recorder.get_system_logs(limit=limit)

    @router.get("/system/status")
    def system_status():
        summary = recorder.get_latest_account_summary()
        pred_date = recorder.get_prediction_date()
        names_updated = recorder.get_stock_names_updated_at()

        log_dir = project_root / config["storage"]["log_dir"]
        recent_logs = []
        if log_dir.exists():
            log_files = sorted(log_dir.glob("*.log"), reverse=True)[:5]
            recent_logs = [f.name for f in log_files]

        return {
            "last_trading_date": summary["date"] if summary else None,
            "last_prediction_date": pred_date,
            "stock_names_updated_at": names_updated,
            "recent_log_files": recent_logs,
            "db_path": str(db_path),
            "config_name": config["paper_trading"]["name"],
        }

    @router.get("/positions/dates")
    def position_dates():
        """Return all dates that have position data."""
        df = recorder.get_account_summary()
        if df.empty:
            return []
        dates = df[df["date"] != "init"]["date"].tolist()
        return dates

    return router
