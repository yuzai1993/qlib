"""Run the independent platform with: python -m quant_platform."""

import argparse
import socket
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="量化研究平台（独立本地服务）")
    parser.add_argument("--research-root", type=Path, default=Path("/Users/yuxianqi/Project/qlib_exp"))
    parser.add_argument("--data-root", type=Path, default=Path(__file__).parent / "data/analyses")
    parser.add_argument("--monitor-url", default="http://127.0.0.1:8082")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
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
    from .app import create_app

    app = create_app(args.research_root, args.data_root, args.monitor_url)
    print(f"量化研究平台：http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
