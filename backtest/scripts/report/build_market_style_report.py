#!/usr/bin/env python
"""Verify 40 fit receipts, evaluate fixed candidates, and render their saved report."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtest.market_style.evaluation import evaluate_archive
from backtest.market_style.reporting import render_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--result-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--hub', type=Path, default=Path(__file__).resolve().parents[2] / 'experiments/reports/market_style_report.html')
    parser.add_argument('--label-audit', type=Path)
    parser.add_argument('--feature-audit', type=Path)
    parser.add_argument('--bundle', type=Path, help='Render an already evaluated bundle without repeating bootstrap')
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text()) if args.bundle else evaluate_archive(args.archive, args.result_dir)
    output = args.output or args.archive / 'report.html'
    read = lambda path: json.loads(path.read_text()) if path else None
    render_report(bundle, output, read(args.label_audit), read(args.feature_audit), args.hub)
    print(json.dumps({'report': str(output.resolve()), 'hub': str(args.hub.resolve()), 'chosen_candidate': bundle.get('chosen_candidate')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
