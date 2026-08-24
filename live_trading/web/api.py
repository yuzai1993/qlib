"""实盘监控只读 REST API。

数据源：LiveRecorder（batches/orders/fills/positions）+
MonitorStore（快照/流程事件/告警）。全部只读，不提供写操作。
"""

from datetime import date as _date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from live_trading.modules.code_map import qmt_to_qlib
from live_trading.modules.fill_importer import FillImporter, LiveRecorder
from live_trading.modules.monitor_store import MonitorStore

MONITOR_STAGES = ["postmarket", "report", "evening"]
PROBE_STRATEGY_ID = "csi1000_pr49_one_lot_probe"
# Historical operator-probe pairing. Do not retarget this at the ladder:
# a leftover probe config would then be able to pause the new main.
MAIN_REAL_STRATEGY_ID = "csi1000_b6m_b2s_postclose_real"
PROBE_PROFILE = "AFTER_HOURS_FIXED_PRICE"


def create_router(config: dict, project_root: Path) -> APIRouter:
    db_path = str(project_root / config["storage"]["db_path"])
    recorder = LiveRecorder(db_path)
    store = MonitorStore(db_path)
    importer = FillImporter(config["live"]["bridge_root"], recorder)

    def _names() -> dict:
        return recorder.get_stock_names()

    def _with_name(row: dict) -> dict:
        names = _names()
        out = dict(row)
        out["name"] = names.get(row.get("stock_code"), "")
        return out

    def _latest_active(strategy_id: str, mode: str = None):
        for batch in recorder.list_batches(limit=1000, strategy_id=strategy_id):
            if batch.get("superseded_by"):
                continue
            if mode is None or batch.get("mode") == mode:
                return batch
        return None

    def _profile_name(strategy_id: str) -> str:
        if strategy_id == PROBE_STRATEGY_ID:
            return PROBE_PROFILE
        current_id = config["live"].get("strategy_id", "")
        if strategy_id == current_id:
            return config["live"].get("execution_session") or "CLOSE_AUCTION"
        return "CLOSE_AUCTION"

    def _probe_lifecycle():
        lifecycle = recorder.get_operator_probe_lifecycle(PROBE_STRATEGY_ID)
        if lifecycle is None:
            return None
        result = dict(lifecycle)
        result["stock_name"] = _names().get(result.get("stock_code"), "")
        return result

    def _probe_execution_state(lifecycle):
        if lifecycle is None:
            return "ARMED"
        return {
            "BUY_PLANNED": "RUNNING",
            "BUY_FILLED": "RUNNING",
            "SELL_PLANNED": "RUNNING",
            "CLOSED": "DONE",
            "FAILED": "FAILED",
        }.get(lifecycle.get("state"), "FAILED")

    router = APIRouter()

    @router.get("/overview")
    def overview():
        latest = store.get_latest_snapshot()
        current_strategy_id = config["live"].get("strategy_id", "")
        active = _latest_active(current_strategy_id, "LIVE")
        lifecycle = _probe_lifecycle()
        main_strategy_id = (
            MAIN_REAL_STRATEGY_ID
            if current_strategy_id == PROBE_STRATEGY_ID
            else current_strategy_id
        )
        main_state = recorder.get_execution_state(main_strategy_id)["state"]
        probe_state = _probe_execution_state(lifecycle)
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
            "account_value_adjustment": recorder.get_value_adjustment(),
            "position_count": len(recorder.get_positions()),
            "today": today,
            "stages": stage_status,
            "recent_alerts": alerts,
            "strategy_id": current_strategy_id,
            "mode": (
                active.get("mode", "") if active
                else config["live"].get("default_mode", "")
            ),
            "benchmark_name": config.get("monitor", {}).get(
                "benchmark_name", "基准",
            ),
            "account_id": active.get("account_id", "") if active else "",
            "active_batch_id": active.get("batch_id", "") if active else "",
            "execution_state": (
                probe_state
                if current_strategy_id == PROBE_STRATEGY_ID else main_state
            ),
            "execution_profile": _profile_name(current_strategy_id),
            "probe_lifecycle": lifecycle,
            "strategy_statuses": [
                {
                    "strategy_id": main_strategy_id,
                    "execution_profile": _profile_name(main_strategy_id),
                    "execution_state": main_state,
                    "active_batch_id": (
                        (_latest_active(main_strategy_id, "LIVE") or {}).get(
                            "batch_id", "",
                        )
                    ),
                },
                {
                    "strategy_id": PROBE_STRATEGY_ID,
                    "execution_profile": PROBE_PROFILE,
                    "execution_state": probe_state,
                    "active_batch_id": (
                        (_latest_active(PROBE_STRATEGY_ID, "LIVE") or {}).get(
                            "batch_id", "",
                        )
                    ),
                    "probe_lifecycle": lifecycle,
                },
            ],
        }

    @router.get("/nav")
    def nav(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
        return store.get_snapshots(start=start, end=end)

    def _to_qlib(stock_code: str):
        try:
            return qmt_to_qlib(stock_code)
        except ValueError:
            return None

    def _latest_predictions():
        """(prediction_date, {instrument: {score, rank}})；无数据返回 (None, {})。"""
        dates = recorder.get_prediction_dates()
        if not dates:
            return None, {}
        return dates[0], recorder.get_predictions_by_date(dates[0])

    def _attach_score(row: dict, preds: dict, stock_code: str) -> dict:
        inst = _to_qlib(stock_code or "")
        p = preds.get(inst) if inst else None
        row["score"] = p["score"] if p else None
        row["score_rank"] = p["rank"] if p else None
        return row

    @router.get("/positions")
    def positions():
        current = recorder.get_positions()
        latest = store.get_latest_snapshot()
        snap_rows = {}
        if latest:
            snap_rows = {r["stock_code"]: r
                         for r in store.get_position_snapshots(latest["date"])}
        names = _names()
        pred_date, preds = _latest_predictions()
        result = []
        for code, p in sorted(current.items()):
            row = snap_rows.get(code, {})
            result.append(_attach_score({
                "stock_code": code,
                "name": names.get(code, ""),
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                "close_price": row.get("close_price"),
                "market_value": row.get("market_value"),
                "profit": row.get("profit"),
                "weight": row.get("weight"),
                "snapshot_date": latest["date"] if latest else None,
            }, preds, code))
        cash_weight = None
        if latest and latest.get("total_value"):
            cash_weight = latest["cash"] / latest["total_value"]
        return {
            "positions": result,
            "cash": recorder.get_cash(),
            "cash_weight": cash_weight,
            "prediction_date": pred_date,
        }

    @router.get("/positions/history")
    def positions_history(date: str = Query(...)):
        names = _names()
        rows = store.get_position_snapshots(date)
        pred_dates = recorder.get_prediction_dates()
        # 历史持仓配当日或之前最近的信号
        pred_date = next((d for d in pred_dates if d <= date), None)
        preds = recorder.get_predictions_by_date(pred_date) if pred_date else {}
        for r in rows:
            r["name"] = names.get(r["stock_code"], "")
            _attach_score(r, preds, r["stock_code"])
        snap = store.get_snapshot(date)
        cash = snap["cash"] if snap else None
        cash_weight = (
            snap["cash"] / snap["total_value"]
            if snap and snap.get("total_value") else None
        )
        return {
            "date": date,
            "positions": rows,
            "dates": store.get_position_snapshot_dates(limit=90),
            "cash": cash,
            "cash_weight": cash_weight,
            "prediction_date": pred_date,
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
                "execution_profile": _profile_name(b.get("strategy_id", "")),
                "raw_missing": raw_missing,
                "missing": 0 if superseded else raw_missing,
                "lifecycle_status": (
                    "SUPERSEDED" if superseded else "ACTIVE"
                ),
            })
        return result

    @router.get("/batches/{batch_id}")
    def batch_detail(batch_id: str):
        """执行计划 + 成交回执（回执前仍可看计划），订单附 signal_date 预测分。"""
        batch = recorder.get_batch(batch_id)
        signal_date = batch.get("signal_date") if batch else None
        preds = (
            recorder.get_predictions_by_date(signal_date) if signal_date else {}
        )
        orders = []
        for o in recorder.get_orders(batch_id):
            row = _with_name(o)
            inst = o.get("instrument_qlib") or _to_qlib(o.get("stock_code", ""))
            p = preds.get(inst) if inst else None
            row["score"] = p["score"] if p else None
            row["score_rank"] = p["rank"] if p else None
            orders.append(row)
        return {
            "batch": batch,
            "orders": orders,
            "fills": [_with_name(f) for f in recorder.get_fills(batch_id)],
            "reconcile": importer.reconcile(batch_id),
            "signal_date": signal_date,
        }

    @router.get("/batches/{batch_id}/fills")
    def batch_fills(batch_id: str):
        return [_with_name(f) for f in recorder.get_fills(batch_id)]

    # ---------- 预测信号 ----------

    def _normalize_instrument_query(q: str):
        """支持 QMT（600000.SH）与 qlib（SH600000）两种输入格式。"""
        q = (q or "").strip()
        if "." in q:
            converted = _to_qlib(q)
            if converted:
                return converted
        return q

    @router.get("/predictions")
    def predictions(
        date: Optional[str] = Query(None),
        instrument: Optional[str] = Query(None),
        name: Optional[str] = Query(None),
        sort_by: str = Query("rank"),
        sort_order: str = Query("asc"),
        limit: int = Query(50),
        offset: int = Query(0),
    ):
        records, total = recorder.get_predictions_search(
            date_str=date,
            instrument=_normalize_instrument_query(instrument) if instrument else None,
            name=name,
            sort_by=sort_by, sort_order=sort_order,
            limit=limit, offset=offset,
        )
        return {"data": records, "total": total, "limit": limit, "offset": offset}

    @router.get("/predictions/dates")
    def prediction_dates():
        return recorder.get_prediction_dates()

    @router.get("/predictions/daily-mean")
    def prediction_daily_mean(
        instruments: Optional[str] = Query(
            None, description="逗号分隔的标的代码（qlib 或 QMT 格式）"),
    ):
        inst_list = None
        if instruments:
            inst_list = [
                _normalize_instrument_query(i)
                for i in instruments.split(",") if i.strip()
            ]
        return recorder.get_prediction_daily_mean(inst_list or None)

    @router.get("/predictions/summary")
    def prediction_summary(date: Optional[str] = Query(None), n: int = Query(3)):
        """某日（默认最新）均值 + top/bottom N 标的。"""
        dates = recorder.get_prediction_dates()
        if not dates:
            return {"date": None, "mean_score": None, "count": 0,
                    "top": [], "bottom": []}
        target = date if date in dates else dates[0]
        extremes = recorder.get_prediction_extremes(target, n=n)
        mean_rows = [
            r for r in recorder.get_prediction_daily_mean() if r["date"] == target
        ]
        return {
            "date": target,
            "mean_score": mean_rows[0]["mean_score"] if mean_rows else None,
            "count": mean_rows[0]["count"] if mean_rows else 0,
            **extremes,
        }

    @router.get("/predictions/instruments")
    def prediction_instruments():
        """全部有预测的标的（含 QMT 代码与名称），供前端联想。"""
        return recorder.get_prediction_instrument_list()

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
