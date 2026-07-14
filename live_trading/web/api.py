"""实盘监控只读 REST API。

数据源：LiveRecorder（batches/orders/fills/positions）+
MonitorStore（快照/流程事件/告警）。全部只读，不提供写操作。
"""

from datetime import date as _date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.stock_names import (
    resolve_paper_db,
    sync_stock_names_from_paper,
)

MONITOR_STAGES = ["postmarket", "report", "evening"]


def create_router(config: dict, project_root: Path) -> APIRouter:
    db_path = str(project_root / config["storage"]["db_path"])
    recorder = LiveRecorder(db_path)
    store = MonitorStore(db_path)
    importer = FillImporter(config["live"]["bridge_root"], recorder)

    # 启动时若 live 无名称表，从 paper 同步一次（失败不影响服务）
    try:
        if not recorder.get_stock_names():
            sync_stock_names_from_paper(
                recorder, resolve_paper_db(config, project_root))
    except Exception:
        pass

    def _names() -> dict:
        return recorder.get_stock_names()

    def _with_name(row: dict) -> dict:
        names = _names()
        out = dict(row)
        out["name"] = names.get(row.get("stock_code"), "")
        return out

    router = APIRouter()

    @router.get("/overview")
    def overview():
        latest = store.get_latest_snapshot()
        active = recorder.get_latest_active_batch("LIVE")
        today = _date.today().strftime("%Y-%m-%d")
        events = store.get_pipeline_events(trade_date=today)
        stage_status = {}
        for e in events:
            stage_status[e["stage"]] = {
                "status": e["status"], "message": e["message"],
                "at": e["created_at"],
            }
        alerts = store.get_alerts(limit=5)
        return {
            "snapshot": latest,
            "cash": recorder.get_cash(),
            "position_count": len(recorder.get_positions()),
            "today": today,
            "stages": stage_status,
            "recent_alerts": alerts,
            "strategy_id": config["live"].get("strategy_id", ""),
            "mode": config["live"].get("default_mode", ""),
            "account_id": active.get("account_id", "") if active else "",
            "active_batch_id": active.get("batch_id", "") if active else "",
        }

    @router.get("/nav")
    def nav(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
        return store.get_snapshots(start=start, end=end)

    @router.get("/positions")
    def positions():
        current = recorder.get_positions()
        latest = store.get_latest_snapshot()
        snap_rows = {}
        if latest:
            snap_rows = {r["stock_code"]: r
                         for r in store.get_position_snapshots(latest["date"])}
        names = _names()
        result = []
        for code, p in sorted(current.items()):
            row = snap_rows.get(code, {})
            result.append({
                "stock_code": code,
                "name": names.get(code, ""),
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                "close_price": row.get("close_price"),
                "market_value": row.get("market_value"),
                "profit": row.get("profit"),
                "weight": row.get("weight"),
                "snapshot_date": latest["date"] if latest else None,
            })
        return {"positions": result, "cash": recorder.get_cash()}

    @router.get("/positions/history")
    def positions_history(date: str = Query(...)):
        names = _names()
        rows = store.get_position_snapshots(date)
        for r in rows:
            r["name"] = names.get(r["stock_code"], "")
        return {
            "date": date,
            "positions": rows,
            "dates": store.get_position_snapshot_dates(limit=90),
        }

    @router.get("/batches")
    def batches(limit: int = Query(30)):
        result = []
        for b in recorder.list_batches(limit=limit):
            r = importer.reconcile(b["batch_id"])
            raw_missing = r["missing"]
            superseded = bool(b.get("superseded_by"))
            result.append({
                **b,
                **r,
                "raw_missing": raw_missing,
                "missing": 0 if superseded else raw_missing,
                "lifecycle_status": (
                    "SUPERSEDED" if superseded else "ACTIVE"
                ),
            })
        return result

    @router.get("/batches/{batch_id}")
    def batch_detail(batch_id: str):
        """执行计划 + 成交回执（回执前仍可看计划）。"""
        return {
            "batch": recorder.get_batch(batch_id),
            "orders": [_with_name(o) for o in recorder.get_orders(batch_id)],
            "fills": [_with_name(f) for f in recorder.get_fills(batch_id)],
            "reconcile": importer.reconcile(batch_id),
        }

    @router.get("/batches/{batch_id}/fills")
    def batch_fills(batch_id: str):
        return [_with_name(f) for f in recorder.get_fills(batch_id)]

    @router.get("/pipeline")
    def pipeline(days: int = Query(10)):
        events = store.get_pipeline_events(days=days)
        matrix = {}
        for e in events:
            day = matrix.setdefault(e["trade_date"], {})
            day[e["stage"]] = {"status": e["status"], "message": e["message"],
                               "at": e["created_at"]}
        return {"stages": MONITOR_STAGES, "days": matrix}

    @router.get("/alerts")
    def alerts(limit: int = Query(50)):
        return store.get_alerts(limit=limit)

    @router.get("/cashflows")
    def cashflows(limit: int = Query(100)):
        rows = [_with_name(r) for r in recorder.get_cash_flows(limit=limit)]
        return {"flows": rows, "cash": recorder.get_cash()}

    @router.get("/corporate-actions")
    def corporate_actions(limit: int = Query(100)):
        return {
            "events": recorder.get_corporate_actions(limit=limit),
            "balances": recorder.get_corporate_balances(),
        }

    return router
