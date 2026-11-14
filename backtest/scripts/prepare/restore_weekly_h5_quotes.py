"""Restore the archived all-A close and amount panel from frozen evaluation quotes."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backtest.market_style.data import fingerprint, read_bin_column

QUOTES = ROOT / (
    "backtest/experiments/models/train-data/dft-specialists-20261108-v1/"
    "extensions/forward-spread-20261112/data/quotes"
)
PROVIDER = ROOT / "backtest/result/20261103_h20_causal_inputs/evaluation-provider"


def restore(provider, quote_dir, output):
    inventory = json.loads((quote_dir / "input-inventory.json").read_text())
    expected = inventory["retained_panel"]["sha256"]
    if output.exists():
        if fingerprint(output)["sha256"] != expected:
            raise ValueError(f"Existing output differs from the archived panel: {output}")
        return expected

    dates = (provider / "calendars/day.txt").read_text().splitlines()[: inventory["rows"]]
    calendar = np.array(dates, dtype="datetime64[ns]")
    records = inventory["rows_by_source"]
    symbols = np.array([record["symbol"] for record in records], dtype="U8")
    close = np.empty((len(calendar), len(symbols)), dtype=np.float32)
    amount = np.empty_like(close)
    with np.load(quote_dir / "quote-overrides.npz", allow_pickle=False) as overrides:
        columns = {symbol: i for i, symbol in enumerate(overrides["symbols"])}
        override_close = overrides["close"]
        override_amount = overrides["amount"]
    for i, (symbol, record) in enumerate(zip(symbols, records)):
        if symbol in columns:
            close[:, i] = override_close[:, columns[symbol]]
            amount[:, i] = override_amount[:, columns[symbol]]
            continue
        folder = provider / "features" / symbol.lower()
        price = read_bin_column(folder / "close.day.bin", len(calendar))
        volume = read_bin_column(folder / "volume.day.bin", len(calendar))
        vwap = read_bin_column(folder / "vwap.day.bin", len(calendar))
        in_life = (calendar >= np.datetime64(record["life_start"])) & (
            calendar <= np.datetime64(record["life_end"])
        )
        active = in_life & (volume > 0) & (price > 0)
        close[:, i] = np.where(active, price, np.nan)
        amount[:, i] = np.where(active, volume * vwap * 100, np.nan)

    with NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, calendar=calendar, symbols=symbols, close=close, amount=amount)
        if fingerprint(temporary)["sha256"] != expected:
            raise ValueError("Reconstructed panel differs from the frozen SHA-256 in input-inventory.json")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", type=Path, default=PROVIDER)
    parser.add_argument("--quote-dir", type=Path, default=QUOTES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(restore(args.provider, args.quote_dir, args.output or args.quote_dir / "effective-quotes.npz"))
