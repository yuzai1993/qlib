"""从训练 session 推理 test 段 pred，并写成官方合成信号。

给执行层回测 / RankIC 补算用；不重算头部网格。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS))

from config_loader import load_config  # noqa: E402
from ensemble_preds import blend_score_series  # noqa: E402
from eval_ic_multi_pool import (  # noqa: E402
    _build_dataset,
    _init_qlib,
    _load_model,
    _normalize_prediction,
    _parse_session,
)


def dump_preds(
    cfg: dict,
    sessions: list[tuple[str, object]],
    *,
    pool: str,
    segment: str,
    out_dir: Path,
    ensemble_name: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = _build_dataset(cfg, pool, segment=segment)
    scores = []
    for session, seed in sessions:
        print(f"[PRED] {session} seed={seed}", flush=True)
        model = _load_model(session)
        pred = _normalize_prediction(model.predict(dataset, segment=segment))
        path = out_dir / f"{session}_pred.pkl"
        pred.to_frame("score").to_pickle(path)
        scores.append(pred)
        print(f"[DONE] {path} rows={len(pred)}", flush=True)
    blended = blend_score_series(scores)
    ens_path = out_dir / ensemble_name
    blended.to_frame("score").to_pickle(ens_path)
    print(f"[ENS ] {ens_path} rows={len(blended)}", flush=True)
    return ens_path


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="训练 session → test pred + 官方合成信号")
    p.add_argument("--config", required=True)
    p.add_argument("--sessions", nargs="+", required=True, metavar="SESSION[:SEED]")
    p.add_argument("--pool", default="all")
    p.add_argument("--segment", default="test")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--ensemble-name", default="ensemble_pred.pkl")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    _init_qlib(cfg)
    dump_preds(
        cfg,
        [_parse_session(s) for s in args.sessions],
        pool=args.pool,
        segment=args.segment,
        out_dir=args.out_dir,
        ensemble_name=args.ensemble_name,
    )


if __name__ == "__main__":
    main()
