"""Run the independent platform with: python -m quant_platform."""

import argparse
import re
import socket
from pathlib import Path


def build_parser():
    parser = argparse.ArgumentParser(description="量化研究平台（实盘监控、离线实验、分析档案统一服务）")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--live-config", default="alla_v2_top5d1f100_postclose_real_active")
    parser.add_argument("--research-root", type=Path, help="研究源目录，默认使用 --project-root")
    parser.add_argument("--data-root", type=Path, help="分析目录集合，默认使用研究源的 backtest/diagnostics")
    parser.add_argument("--port", type=int, default=8090)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.research_root is None:
        args.research_root = args.project_root
    if args.data_root is None:
        args.data_root = args.research_root / "backtest/diagnostics"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.live_config):
        parser.error("实盘配置必须是 configs 目录中的配置名称")
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024–65535 之间")
    if not args.research_root.is_dir():
        parser.error(f"研究源目录不存在：{args.research_root}")
    # Fail before app initialization if the independent port is already occupied.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"127.0.0.1:{args.port} 已被占用；保留现有服务，请使用其他端口")
    import uvicorn
    from live_trading.modules.live_config import load_live_config
    from .app import create_app

    try:
        config = load_live_config(
            args.project_root / "live_trading/configs" / f"{args.live_config}.yaml", args.project_root
        )
    except (OSError, ValueError) as exc:
        parser.error(f"实盘配置不可读或无效：{exc}")
    app = create_app(args.research_root, args.data_root, live_config=config, project_root=args.project_root)
    print(f"量化研究平台：http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
