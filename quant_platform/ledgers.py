"""Read-only monitor sources selected from server-owned configuration files."""

from __future__ import annotations

import hashlib
import inspect
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml
from fastapi import HTTPException, Request

from live_trading.modules.signal_schema import SchemaError
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.execution_state import validate_identifier
from live_trading.web.api import create_router


UNAVAILABLE = "实盘账本暂不可读，研究与分析仍可使用；请检查数据源，不要在平台中初始化账本"
MONITOR_TABLES = {"daily_snapshot", "position_snapshot", "pipeline_events", "alerts"}
MONITOR_COLUMNS = {
    "daily_snapshot": "date,cash,total_value,cumulative_return",
    "position_snapshot": "date,stock_code,shares,avg_cost,close_price,market_value,profit,weight",
    "positions": "stock_code,shares,avg_cost",
    "account_state": "key,value",
    "batches": "batch_id,trade_date,mode,planned_orders,strategy_id,signal_date,superseded_by",
    "signal_orders": "batch_id,client_order_id,stock_code,side,quantity,target_value,price_type,priority",
    "fills": "batch_id,client_order_id,stock_code,side,status,mode,applied_fee",
    "stock_names": "stock_code,instrument,name",
    "predictions": "date,instrument,score,rank",
    "pipeline_events": "id,trade_date,stage,status,message,created_at",
    "alerts": "id,trade_date,level,rule,message",
    "cash_flows": "id,trade_date,flow_type,stock_code,amount,note",
    "corporate_actions": "event_key,stock_code,ex_date,cash_settled,bonus_settled,tax_settled,gross_cash,tax_provision,bonus_shares",
    "broker_snapshot_imports": "batch_id,trade_date,is_fresh,ordering_trusted,import_sequence",
    "broker_position_snapshot": "batch_id,stock_code,market_value",
}


def _has_monitor_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return bool(tables & MONITOR_TABLES)


class _OptionalMonitorStore:
    """A freshly published ledger can predate its first monitor report.

    Treat a wholly absent monitor schema as empty, without initializing it. A
    partial or incompatible schema still fails closed; later reports become
    visible automatically through the underlying read-only store.
    """

    empty_methods = {
        "get_latest_snapshot", "get_snapshot", "get_snapshots", "get_position_snapshots",
        "get_position_snapshot_dates", "get_pipeline_events", "get_alerts",
    }

    def __init__(self, db_path):
        self.store = MonitorStore(str(db_path), read_only=True)

    def __getattr__(self, name):
        underlying = getattr(self.store, name)
        if name not in self.empty_methods:
            return underlying

        def read(*args, **kwargs):
            try:
                return underlying(*args, **kwargs)
            except sqlite3.OperationalError:
                with self.store._conn() as conn:
                    if not _has_monitor_tables(conn):
                        return None if name in {"get_latest_snapshot", "get_snapshot"} else []
                raise

        return read


@dataclass(frozen=True)
class Ledger:
    id: str
    label: str
    strategy_id: str
    db_path: Path
    endpoints: dict
    router: object

    def readable(self):
        if not self.db_path.is_file():
            return False
        try:
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
            try:
                conn.execute("PRAGMA query_only=ON")
                initialized = _has_monitor_tables(conn)
                for table, columns in MONITOR_COLUMNS.items():
                    if table in MONITOR_TABLES and not initialized:
                        continue
                    conn.execute(f"SELECT {columns} FROM {table} LIMIT 0")
            finally:
                conn.close()
        except (OSError, SchemaError, sqlite3.Error, ValueError):
            return False
        return True


def _label(strategy_id):
    labels = {
        "alla_v2_top5d1f100_postclose_real": "全A Top5 · 新版",
        "alla_v4_ladder_k1h5_postclose_real": "全A 阶梯 k1×h5 · 历史",
        "alla_v4_ladder_k3h5_postclose_real": "全A 阶梯 k3×h5",
        "csi1000_b6m_b2s_postclose_real": "CSI1000 B6-M / B4-S",
    }
    return labels.get(strategy_id, strategy_id)


class LedgerCatalog:
    def __init__(self, project_root: Path, default_config: dict | None):
        self.project_root = project_root.resolve()
        data_root = (self.project_root / "live_trading/data").resolve()
        config_root = (self.project_root / "live_trading/configs").resolve()
        candidates = []
        # The explicit server configuration is trusted, just as before ledger selection.
        # Browser-supplied IDs never become paths or configuration names.
        if default_config is not None:
            candidates.append((default_config, True))
        for path in sorted(config_root.glob("*.y*ml")):
            try:
                if not path.resolve().is_relative_to(config_root) or path.stat().st_size > 1024 * 1024:
                    continue
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, RuntimeError, UnicodeError, yaml.YAMLError):
                continue
            candidates.append((config, False))

        entries = {}
        self.default_id = None
        for config, explicit in candidates:
            try:
                if not isinstance(config, dict):
                    continue
                if any(not isinstance(config.get(section, {}), dict) for section in ("live", "storage", "monitor")):
                    continue
                raw_path = config["storage"]["db_path"]
                live = config["live"]
                strategy_id = live.get("strategy_id", "")
                if not isinstance(raw_path, str) or not isinstance(strategy_id, str):
                    continue
                if not strategy_id and not explicit:
                    continue
                if strategy_id:
                    validate_identifier(strategy_id, "strategy_id")
                db_path = (self.project_root / raw_path).resolve()
                if not explicit and not db_path.is_relative_to(data_root):
                    continue
                ledger_id = hashlib.sha256(str(db_path).encode()).hexdigest()[:24]
                if ledger_id in entries or not db_path.is_file():
                    continue
                normalized = {**config, "storage": {**config["storage"], "db_path": str(db_path)}}
                router = create_router(
                    normalized, self.project_root, read_only=True, monitor_store=_OptionalMonitorStore(db_path)
                )
                endpoints = MappingProxyType({route.path: route.endpoint for route in router.routes})
                ledger = Ledger(ledger_id, _label(strategy_id) or "默认账本", strategy_id, db_path, endpoints, router)
                if not ledger.readable():
                    continue
            except (KeyError, TypeError, AttributeError, OSError, RuntimeError, SchemaError, sqlite3.Error, ValueError):
                continue
            entries[ledger_id] = ledger
            if explicit:
                self.default_id = ledger_id
        self.entries = MappingProxyType(entries)

    def available(self):
        return [entry for entry in self.entries.values() if entry.readable()]

    def snapshot(self):
        available = self.available()
        default = next((entry for entry in available if entry.id == self.default_id), None)
        default = default or next(iter(available), None)
        return {
            "ledgers": [{"id": entry.id, "label": entry.label, "strategy_id": entry.strategy_id}
                        for entry in available],
            "default_ledger": default.id if default else None,
        }

    def resolve(self, request: Request):
        if "ledger" in request.query_params:
            requested = request.query_params.getlist("ledger")
            if len(requested) != 1 or requested[0] not in self.entries:
                raise HTTPException(status_code=404, detail="账本不存在，请重新选择账本")
            ledger = self.entries[requested[0]]
            if not ledger.readable():
                raise HTTPException(status_code=503, detail=UNAVAILABLE)
            return ledger
        available = self.available()
        default = next((entry for entry in available if entry.id == self.default_id), None)
        if default is not None:
            return default
        if available:
            return available[0]
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    def install_routes(self, app):
        if not self.entries:
            return
        template = next(iter(self.entries.values())).router
        for route in template.routes:
            endpoint = self._endpoint(route.path, route.endpoint)
            app.add_api_route("/api" + route.path, endpoint, methods=route.methods)

    def _endpoint(self, path, template):
        def selected_endpoint(_ledger_request: Request, **kwargs):
            ledger = self.resolve(_ledger_request)
            try:
                return ledger.endpoints[path](**kwargs)
            except (SchemaError, sqlite3.Error, OSError):
                raise HTTPException(status_code=503, detail=UNAVAILABLE) from None

        # Keep the existing query/path validation while choosing an immutable router
        # per request. No shared "current ledger" is changed by a browser selection.
        selected_endpoint.__signature__ = inspect.signature(template).replace(parameters=[
            inspect.Parameter("_ledger_request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
            *inspect.signature(template).parameters.values(),
        ])
        selected_endpoint.__name__ = "selected_" + template.__name__
        return selected_endpoint
