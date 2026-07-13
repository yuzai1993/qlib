"""股票名称同步：从 paper_trading 账本拷贝到 live 账本（QMT 代码格式）。

paper 存 instrument=SH600000；live Web 用 stock_code=600000.SH。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from live_trading.modules.code_map import qlib_to_qmt
from live_trading.modules.fill_importer import LiveRecorder

logger = logging.getLogger("live_trading.stock_names")


def resolve_paper_db(config: dict, project_root: Path) -> Path:
    """优先读 monitor.paper_db；否则找常见 paper 库路径。"""
    explicit = (config.get("monitor") or {}).get("paper_db")
    if explicit:
        return Path(project_root) / explicit
    candidates = [
        Path(project_root) / "paper_trading" / "data" / "csi300_topk10.db",
        Path(project_root) / "paper_trading" / "data" / "paper_trading.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def sync_stock_names_from_paper(live_recorder: LiveRecorder, paper_db_path) -> int:
    """从 paper SQLite 的 stock_names 同步到 live。返回写入条数。"""
    paper_db = Path(paper_db_path)
    if not paper_db.exists():
        logger.warning("paper db not found: %s", paper_db)
        return 0

    conn = sqlite3.connect(str(paper_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT instrument, name FROM stock_names WHERE name IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    mapped = []
    for r in rows:
        try:
            stock_code = qlib_to_qmt(r["instrument"])
        except ValueError:
            continue
        mapped.append({
            "stock_code": stock_code,
            "instrument": r["instrument"],
            "name": r["name"],
        })
    if mapped:
        live_recorder.save_stock_names(mapped)
    logger.info("synced %d stock names from %s", len(mapped), paper_db)
    return len(mapped)
