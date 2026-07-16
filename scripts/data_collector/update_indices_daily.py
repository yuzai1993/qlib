"""每日更新四指数成分并安装到 qlib instruments。

数据源：仅中证官网（csindex_v2）
  - 公告增量：维护成分区间（公告日口径，含当前在册）——唯一写入来源
  - 每日成分快照 XLS：只读校验（公告→生效窗口内的滞后属预期，不告警；
    无法解释的漂移才失败）。快照绝不回写 instruments，否则会抹掉
    公告日口径的调仓提前量。

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
        from scripts.data_collector.csindex_v2.updater import update_daily, INSTALL_INDICES

        logger.info("======== csindex_v2 日更（公告写入 + 官网快照只读校验）========")
        result = update_daily()
        logger.info(
            f"完成: new_details={result.get('new_details')} rebuilt={result.get('rebuilt')}"
        )

        # 校验结论来自 update_daily 内的只读快照比对：
        # 公告→生效窗口内的滞后（pending_add/pending_drop）属预期，不失败；
        # 无法解释的漂移（unexplained_*）或快照/清单缺失才失败。
        ok = True
        for name in INSTALL_INDICES:
            check = (result.get("snapshot_check") or {}).get(name)
            if not check:
                ok = False
                logger.error(f"校验失败 {name}: 无快照比对结果")
                continue
            snap_date = check.get("snap_date")
            if check.get("error"):
                ok = False
                logger.error(f"校验失败 {name}@{snap_date}: {check['error']}")
                continue
            pending_add = check.get("pending_add") or []
            pending_drop = check.get("pending_drop") or []
            bad_local = check.get("unexplained_local") or []
            bad_snap = check.get("unexplained_snap") or []
            if not check.get("ok"):
                ok = False
                logger.error(
                    f"校验失败 {name}@{snap_date}: 无法解释的漂移 "
                    f"仅在册={bad_local[:5]} 仅快照={bad_snap[:5]}"
                )
            elif pending_add or pending_drop:
                logger.info(
                    f"校验 OK {name}@{snap_date}: 公告日口径提前量 "
                    f"+{len(pending_add)} -{len(pending_drop)}（待生效，属预期）"
                )
            else:
                logger.info(
                    f"校验 OK {name}@{snap_date}: 快照与在册一致 "
                    f"({check.get('snap_count')} 只)"
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
