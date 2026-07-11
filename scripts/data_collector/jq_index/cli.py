"""命令行入口。

用法：
  # 查看各指数拉取进度
  python -m scripts.data_collector.jq_index.cli status

  # 首次/续拉历史快照（跨天续拉：不传 --index 则按配额依序拉所有指数）
  # 方式一：通过环境变量（推荐，避免明文密码留在 shell history）
  export JQ_USER=18657117125
  export JQ_PWD=your_password
  python -m scripts.data_collector.jq_index.cli pull
  python -m scripts.data_collector.jq_index.cli pull --index csi2000

  # 方式二：命令行直接传参（会留在 shell history，不推荐）
  python -m scripts.data_collector.jq_index.cli pull --user <账号> --pwd <密码>

  # 从快照构建 instruments（默认输出 csi300_jq.txt 等，不覆盖原文件）
  python -m scripts.data_collector.jq_index.cli build
  python -m scripts.data_collector.jq_index.cli build --index csi500 --suffix ""   # 直接覆盖原文件（慎用）

  # 与旧缓存校验（目前只对 csi300/csi100 有旧缓存）
  python -m scripts.data_collector.jq_index.cli validate
  python -m scripts.data_collector.jq_index.cli validate --index csi300

  # 每日维护（收盘后调用，4 次 JQ 调用）
  python -m scripts.data_collector.jq_index.cli update
"""

from __future__ import annotations

import os
import sys

import fire
from loguru import logger

from . import config as cfg
from .builder import build, build_all
from .puller import auth, pull, pull_all, status, account_info
from .updater import update_today
from .validator import validate


logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")


def _resolve_credentials(user=None, pwd=None):
    """优先级：显式参数 > 环境变量 JQ_USER/JQ_PWD。

    用法：在 pull/update 等命令里调用
        user, pwd = _resolve_credentials(user, pwd)
        auth(user, pwd)
    """
    if user is None:
        user = os.environ.get("JQ_USER")
    if pwd is None:
        pwd = os.environ.get("JQ_PWD")
    if not user or not pwd:
        raise SystemExit(
            "缺少聚宽账号信息。请通过 --user/--pwd 传参，"
            "或设置环境变量 JQ_USER / JQ_PWD。"
        )
    return user, pwd


class CLI:
    """聚宽指数成分股重建工具。"""

    def account(self, user: str = None, pwd: str = None) -> None:
        """查看聚宽账号权限范围（可查询的历史数据时间窗口）。"""
        user, pwd = _resolve_credentials(user, pwd)
        auth(user, pwd)
        info = account_info()
        logger.info(f"完整账号信息:\n{info}")

    def status(self, index: str = "all") -> None:
        """查看各指数的拉取进度。"""
        names = cfg.ALL_INDEX_NAMES if index == "all" else [index]
        for name in names:
            s = status(name)
            done = s["covered_trading_days"]
            total = s["estimated_total_trading_days"]
            pct = done / total * 100 if total else 0
            flag = "✓" if s["is_complete"] else f"{pct:.0f}%"
            logger.info(
                f"  {name:<10} last={s['last_pulled_date'] or 'N/A':12}"
                f" days={done}/{total} [{flag}]"
                f" total_calls={s['total_api_calls']}"
            )

    def pull(
        self,
        user: str = None,
        pwd: str = None,
        index: str = "all",
        start_date: str = None,
        end_date: str = None,
        max_calls: int = cfg.DAILY_CALL_LIMIT,
        sleep: float = 0.3,
    ) -> None:
        """
        拉取历史每日快照（支持断点续拉）。

        --user/--pwd  聚宽账号密码；不传则从环境变量 JQ_USER/JQ_PWD 读取
        --index  指定单个指数（csi300/csi500/csi1000/csi2000）或 all
        --start_date  拉取起始日（YYYY-MM-DD）。账号权限有限时传入权限起始日，
                      如 --start_date 2025-03-28。不传则从指数成立日开始
        --end_date  拉取截止日，默认今天
        --max_calls 本次最多调用次数
        --sleep  每次调用间隔秒数，默认 0.3
        """
        user, pwd = _resolve_credentials(user, pwd)
        auth(user, pwd)
        cfg.ensure_dirs()

        if index == "all":
            results = pull_all(start_date=start_date, end_date=end_date,
                               max_calls=max_calls, sleep_sec=sleep)
        else:
            results = {index: pull(index, start_date=start_date, end_date=end_date,
                                   max_calls=max_calls, sleep_sec=sleep)}

        for name, r in results.items():
            logger.info(
                f"  {name}: pulled={r.get('pulled', 0)}"
                f"  last={r.get('last_date')}"
                f"  stopped_early={r.get('stopped_early', False)}"
            )

        stopped = any(r.get("stopped_early") for r in results.values())
        if stopped:
            logger.warning("已达调用上限，明日继续运行 pull 命令即可续拉（断点续传）。")

    def build(
        self,
        index: str = "all",
        suffix: str = "_jq",
        next_trading_day: str = None,
    ) -> None:
        """
        从快照构建 instruments.txt。

        --index   指定单个指数或 all
        --suffix  输出文件名后缀（默认 _jq，即 csi300_jq.txt）
                  设为 '' 则直接覆盖 csi300.txt（慎用）
        --next_trading_day  快照最后一天的下一交易日（用于设置 end_date）
        """
        names = cfg.ALL_INDEX_NAMES if index == "all" else [index]
        for name in names:
            build(name, output_suffix=suffix, next_trading_day=next_trading_day)

    def validate(self, index: str = "all") -> None:
        """
        与旧缓存（~/.cache/qlib/index/CSI*/）交叉校验。

        当前只有 CSI300 和 CSI100 有旧缓存可供校验。
        """
        names = cfg.ALL_INDEX_NAMES if index == "all" else [index]
        for name in names:
            validate(name, output_report=True)

    def update(
        self,
        user: str = None,
        pwd: str = None,
        index: str = "all",
        suffix: str = "_jq",
        write_csi2000: bool = True,
    ) -> None:
        """
        每日维护：拉取今日成分（每指数 1 次，共最多 4 次 JQ 调用）。

        csi2000 默认写入正式 instruments/csi2000.txt；
        其余指数默认仍写旁路 {index}{suffix}.txt（suffix 默认 _jq）。
        --user/--pwd  聚宽账号密码；不传则从环境变量 JQ_USER/JQ_PWD 读取
        """
        user, pwd = _resolve_credentials(user, pwd)
        auth(user, pwd)
        names = cfg.ALL_INDEX_NAMES if index == "all" else [index]
        update_today(
            index_names=names,
            rebuild_instruments=bool(suffix),
            output_suffix=suffix,
            write_csi2000=write_csi2000,
        )

    def run_all(
        self,
        user: str = None,
        pwd: str = None,
        start_date: str = None,
        end_date: str = None,
        max_calls: int = cfg.DAILY_CALL_LIMIT,
        sleep: float = 0.3,
        suffix: str = "_jq",
    ) -> None:
        """
        完整流程：pull → build → validate（一键运行）。

        首次运行或续拉完成后推荐使用。
        --user/--pwd  聚宽账号密码；不传则从环境变量 JQ_USER/JQ_PWD 读取
        --start_date  账号权限有限时传入权限起始日
        """
        user, pwd = _resolve_credentials(user, pwd)
        auth(user, pwd)
        cfg.ensure_dirs()

        logger.info("=== Step 1: Pull ===")
        results = pull_all(start_date=start_date, end_date=end_date,
                           max_calls=max_calls, sleep_sec=sleep)
        stopped = any(r.get("stopped_early") for r in results.values())

        if stopped:
            logger.warning("拉取未完成，明日继续。先对已完成的指数构建 instruments。")

        logger.info("=== Step 2: Build ===")
        for name, r in results.items():
            if r.get("pulled", 0) > 0 or not r.get("stopped_early"):
                build(name, output_suffix=suffix)

        logger.info("=== Step 3: Validate ===")
        for name in cfg.ALL_INDEX_NAMES:
            validate(name, output_report=True)


def main():
    fire.Fire(CLI)


if __name__ == "__main__":
    main()
