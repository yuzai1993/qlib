#!/usr/bin/env python
"""A single Harness-wrapped market-style classifier fit (never self-registers)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtest.market_style.modeling import CANDIDATES, SEEDS, run_fit_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['features', 'targets', 'manifest', 'output-dir']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--candidate', choices=CANDIDATES, required=True)
    parser.add_argument('--seed', choices=SEEDS, type=int, required=True)
    parser.add_argument('--phase', choices=['inner', 'final'], required=True)
    parser.add_argument('--inner-receipt', type=Path)
    args = parser.parse_args()
    receipt = run_fit_job(args.features, args.targets, args.manifest, args.candidate, args.seed, args.phase, args.output_dir, args.inner_receipt)
    print(json.dumps({'receipt': str((args.output_dir / 'receipt.json').resolve()), 'candidate': receipt['candidate'], 'seed': receipt['seed'], 'phase': receipt['phase'], 'metrics': receipt['metrics']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
