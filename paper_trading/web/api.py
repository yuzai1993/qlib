"""FastAPI API routes for the paper trading dashboard."""

import sys
from pathlib import Path
from typing import Optional

import yaml
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

    configs_dir = project_root / "paper_trading" / "configs"
    _recorder_cache = {}
    _reporter_cache = {}

    def _get_recorder_reporter(config_id: str = None):
        """Get recorder/reporter for a specific instance, with caching."""
        if not config_id or config_id == config.get("_config_id"):
            return recorder, reporter

        if config_id in _recorder_cache:
            return _recorder_cache[config_id], _reporter_cache[config_id]

        cfg_path = configs_dir / f"{config_id}.yaml"
        if not cfg_path.exists():
            return recorder, reporter

        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        db = project_root / cfg["storage"]["db_path"]
        rec = Recorder(str(db))
        cash = cfg["paper_trading"]["initial_cash"]
        rep = Reporter(rec, cash)
        _recorder_cache[config_id] = rec
        _reporter_cache[config_id] = rep
        return rec, rep

    router = APIRouter()

    @router.get("/instances")
    def list_instances():
        """List all available paper trading instances."""
        instances = []
        if configs_dir.exists():
            for f in sorted(configs_dir.glob("*.yaml")):
                try:
                    with open(f) as fh:
                        cfg = yaml.safe_load(fh)
                    inst_db = project_root / cfg["storage"]["db_path"]
                    instances.append({
                        "id": f.stem,
                        "name": cfg.get("paper_trading", {}).get("name", f.stem),
                        "initial_cash": cfg.get("paper_trading", {}).get("initial_cash", 0),
                        "start_date": cfg.get("paper_trading", {}).get("start_date", ""),
                        "has_data": inst_db.exists(),
                    })
                except Exception:
                    continue
        return instances

    @router.get("/overview")
    def overview(instance: Optional[str] = Query(None)):
        rec, rep = _get_recorder_reporter(instance)
        summary = rec.get_latest_account_summary()
        if not summary:
            return {"error": "No data"}

        perf = rep.calculate_performance()

        positions = rec.get_positions()
        pos_list = positions.to_dict("records") if not positions.empty else []

        all_summaries = rec.get_account_summary()
        trading_days = len(all_summaries) if not all_summaries.empty else 0
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
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_account_summary(start=start, end=end)
        if not df.empty:
            df = df[df["date"] != "init"]
        return df.to_dict("records")

    @router.get("/positions/current")
    def current_positions(instance: Optional[str] = Query(None)):
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_positions()
        names = rec.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/positions")
    def positions(
        date: Optional[str] = Query(None),
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_positions(date)
        names = rec.get_stock_names()
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
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_orders(start=start, end=end, direction=direction)
        names = rec.get_stock_names()
        records = df.to_dict("records")
        for r in records:
            if not r.get("name"):
                r["name"] = names.get(r["instrument"], "")
        return records

    @router.get("/predictions")
    def predictions(
        date: Optional[str] = Query(None),
        instrument: Optional[str] = Query(None),
        name: Optional[str] = Query(None),
        sort_by: str = Query("rank"),
        sort_order: str = Query("asc"),
        limit: int = Query(300),
        offset: int = Query(0),
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        records, total = rec.get_predictions_search(
            date_str=date, instrument=instrument, name=name,
            sort_by=sort_by, sort_order=sort_order,
            limit=limit, offset=offset,
        )
        return {"data": records, "total": total, "limit": limit, "offset": offset}

    @router.get("/predictions/dates")
    def prediction_dates(instance: Optional[str] = Query(None)):
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_prediction_dates()

    @router.get("/predictions/daily-mean")
    def prediction_daily_mean(
        instruments: Optional[str] = Query(None, description="Comma-separated instrument codes"),
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        inst_list = None
        if instruments:
            inst_list = [i.strip() for i in instruments.split(",") if i.strip()]
        return rec.get_prediction_daily_mean(inst_list or None)

    @router.get("/predictions/instruments")
    def prediction_instruments(instance: Optional[str] = Query(None)):
        """List all instruments with prediction data (for autocomplete)."""
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_prediction_instrument_list()

    @router.get("/performance")
    def performance(instance: Optional[str] = Query(None)):
        _, rep = _get_recorder_reporter(instance)
        return rep.calculate_performance()

    @router.get("/performance/daily")
    def daily_performance(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
        instance: Optional[str] = Query(None),
    ):
        _, rep = _get_recorder_reporter(instance)
        return rep.daily_returns(start=start, end=end)

    @router.get("/performance/yearly")
    def yearly_performance(instance: Optional[str] = Query(None)):
        _, rep = _get_recorder_reporter(instance)
        return rep.yearly_returns()

    @router.get("/performance/monthly")
    def monthly_performance(instance: Optional[str] = Query(None)):
        _, rep = _get_recorder_reporter(instance)
        return rep.monthly_returns()

    @router.get("/benchmark")
    def benchmark(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_account_summary(start=start, end=end)
        if df.empty:
            return []
        cols = ["date", "benchmark_return", "benchmark_cumulative_return"]
        return df[cols].to_dict("records")

    @router.get("/stock/names")
    def stock_names(instance: Optional[str] = Query(None)):
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_stock_names()

    @router.get("/stock/names/list")
    def stock_names_list(instance: Optional[str] = Query(None)):
        """List of {instrument, name} for autocomplete."""
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_stock_names_list()

    @router.get("/logs")
    def logs(
        limit: int = Query(100),
        instance: Optional[str] = Query(None),
    ):
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_system_logs(limit=limit)

    @router.get("/system/status")
    def system_status(instance: Optional[str] = Query(None)):
        rec, _ = _get_recorder_reporter(instance)
        summary = rec.get_latest_account_summary()
        pred_date = rec.get_prediction_date()
        names_updated = rec.get_stock_names_updated_at()

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
            "db_path": str(rec.db_path),
            "config_name": config["paper_trading"]["name"],
        }

    @router.get("/positions/dates")
    def position_dates(instance: Optional[str] = Query(None)):
        """Return all dates that have position data."""
        rec, _ = _get_recorder_reporter(instance)
        df = rec.get_account_summary()
        if df.empty:
            return []
        dates = df[df["date"] != "init"]["date"].tolist()
        return dates

    @router.get("/stock-pnl")
    def stock_pnl(instance: Optional[str] = Query(None)):
        """Per-stock P&L summary across all trades."""
        rec, _ = _get_recorder_reporter(instance)
        return rec.get_stock_pnl_summary()

    return router
