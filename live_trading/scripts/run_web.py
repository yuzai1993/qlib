#!/usr/bin/env python3
"""启动实盘监控 Web 仪表盘（只读）。

用法：
    python live_trading/scripts/run_web.py --config csi300_topk10_live [--host H] [--port P]
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.live_config import load_live_config

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def main():
    p = argparse.ArgumentParser(description="Live trading monitor dashboard")
    p.add_argument("--config", required=True, help="live config id (configs/*.yaml)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args()

    config = load_live_config(CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT)
    web_cfg = config.get("web", {})
    host = args.host or web_cfg.get("host", "127.0.0.1")
    port = args.port or web_cfg.get("port", 8081)

    import uvicorn
    from live_trading.web.app import create_app

    app = create_app(config, PROJECT_ROOT)
    print(f"Live monitor dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
