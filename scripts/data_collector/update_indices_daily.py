"""每日更新四指数成分并安装到 qlib instruments。

数据源：仅中证官网（csindex_v2）
  - 公告增量：维护历史调样区间（公告日口径）
  - 每日成分快照 XLS：对齐当前在册（替代原聚宽）

快照：
  csi300  .../000300cons.xls
  csi500  .../000905cons.xls
  csi1000 .../000852cons.xls
  csi2000 .../932000cons.xls

用法：
  python -m scripts.data_collector.update_indices_daily
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from loguru import logger

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def run() -> int:
    """返回 0 成功；非 0 表示失败（调用方告警但不阻断个股任务）。"""
    logger.remove()
    logger.add(sys.stderr, format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")

    try:
        from scripts.data_collector.csindex_v2.updater import update_daily
        from scripts.data_collector.csindex_v2.builder import load_current_snapshot
        from scripts.data_collector.csindex_v2.updater import current_members, INSTALL_INDICES

        logger.info("======== csindex_v2 日更（公告 + 官网快照，四指数）========")
        result = update_daily()
        logger.info(
            f"完成: new_details={result.get('new_details')} rebuilt={result.get('rebuilt')}"
        )

        # 校验：安装后当前在册 == 官网快照
        ok = True
        for name in INSTALL_INDICES:
            snap_date, snap = load_current_snapshot(name)
            mem = current_members(name)
            # 快照日期当日应完全一致
            mem_asof = current_members(name, asof=snap_date)
            only_snap = sorted(snap - mem_asof)
            only_inst = sorted(mem_asof - snap)
            sync = (result.get("snapshot_sync") or {}).get(name) or {}
            if only_snap or only_inst:
                ok = False
                logger.error(
                    f"校验失败 {name}@{snap_date}: snap={len(snap)} inst={len(mem_asof)} "
                    f"only_snap={only_snap[:5]} only_inst={only_inst[:5]}"
                )
            else:
                logger.info(
                    f"校验 OK {name}@{snap_date}: {len(mem)} 只 "
                    f"(快照差分 +{len(sync.get('added') or [])} "
                    f"-{len(sync.get('removed') or [])})"
                )
        if not ok:
            return 1
        logger.info("指数日更全部完成")
        return 0
    except Exception as e:
        logger.error(f"指数日更失败: {e}\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
