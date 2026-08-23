"""把 regime-adapt 训练产物精简成实盘推理用的 artifact。

训练产物里 tradable_mask 占 97.6% 体积（单个 58.68 MB / 60.1 MB），而
RegimeSingleLGBMModel 没有覆写 predict，走 LGBModel 原生路径，tradable_mask
只在 fit_prepared 算早停指标时被读。仓库没有 git-lfs，不能塞进 289 MB 死重量。

用法：
    python live_trading/scripts/export_live_model.py
"""

import argparse
import hashlib
import logging
import pickle
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("live_trading.export_model")

# 只在 fit_prepared / 早停时使用，推理路径不读
TRAINING_ONLY_ATTRS = ("tradable_mask", "day_weights", "rankic_evals_result")

SEEDS = (42, 1000, 2000, 3000, 4000)
SRC_TEMPLATE = (
    "backtest/result/regimeadaptfast_m0h20_rankices_s{seed}"
    "/run_01/artifacts_root/artifacts/trained_model"
)
DST_TEMPLATE = "live_trading/models/v4_rankices/s{seed}/trained_model"


def slim_model(model) -> list:
    """把训练态属性置 None，返回实际清掉的属性名。"""
    cleared = []
    for name in TRAINING_ONLY_ATTRS:
        if getattr(model, name, None) is not None:
            setattr(model, name, None)
            cleared.append(name)
    return cleared


def export(src: Path, dst: Path) -> dict:
    """读训练产物、精简、写实盘 artifact，返回体积与哈希。"""
    src = Path(src)
    dst = Path(dst)
    src_bytes = src.stat().st_size
    with open(src, "rb") as fh:
        model = pickle.load(fh)

    cleared = slim_model(model)

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)

    payload = dst.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "src_bytes": src_bytes,
        "dst_bytes": len(payload),
        "cleared": cleared,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds", type=int, nargs="+", default=list(SEEDS),
        help="seeds to export (default: all five)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"{'seed':>6} {'src MB':>9} {'dst MB':>9}  sha256")
    for seed in args.seeds:
        src = PROJECT_ROOT / SRC_TEMPLATE.format(seed=seed)
        dst = PROJECT_ROOT / DST_TEMPLATE.format(seed=seed)
        if not src.is_file():
            raise FileNotFoundError(f"training artifact not found: {src}")
        info = export(src, dst)
        print(
            f"{seed:>6} {info['src_bytes'] / 1e6:>9.1f} "
            f"{info['dst_bytes'] / 1e6:>9.2f}  {info['sha256']}"
        )
        logger.info("cleared %s for seed %s", info["cleared"], seed)


if __name__ == "__main__":
    main()
