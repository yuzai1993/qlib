"""共享配置：缓存路径、指数元信息、符号格式转换等。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


# ── 目录 ──────────────────────────────────────────────────────────────────────
CACHE_ROOT = Path("~/.cache/qlib/jq_index").expanduser().resolve()
LEGACY_CACHE_ROOT = Path("~/.cache/qlib/index").expanduser().resolve()
INSTRUMENTS_DIR = Path("~/.qlib/qlib_data/cn_data/instruments").expanduser().resolve()


def ensure_dirs() -> None:
    for meta in INDEX_META.values():
        (CACHE_ROOT / meta["name"]).mkdir(parents=True, exist_ok=True)
    INSTRUMENTS_DIR.mkdir(parents=True, exist_ok=True)


# ── 指数元信息 ─────────────────────────────────────────────────────────────────
# jq_code   : 聚宽指数代码
# start_date: 指数正式发布日（第一个有效查询日）
# expected_size: 标称样本数，用于校验
# legacy_dir: ~/.cache/qlib/index/ 下的旧缓存目录名（无则为 None）
INDEX_META: dict[str, dict] = {
    "csi300": {
        "name": "csi300",
        "jq_code": "000300.XSHG",
        "start_date": pd.Timestamp("2005-04-08"),
        "expected_size": 300,
        "legacy_dir": "CSI300",
    },
    "csi500": {
        "name": "csi500",
        "jq_code": "000905.XSHG",
        "start_date": pd.Timestamp("2007-01-15"),
        "expected_size": 500,
        "legacy_dir": None,
    },
    "csi1000": {
        "name": "csi1000",
        "jq_code": "000852.XSHG",
        "start_date": pd.Timestamp("2014-10-17"),
        "expected_size": 1000,
        "legacy_dir": None,
    },
    "csi2000": {
        "name": "csi2000",
        "jq_code": "932000.XSHG",
        "start_date": pd.Timestamp("2023-02-17"),
        "expected_size": 2000,
        "legacy_dir": None,
    },
}

ALL_INDEX_NAMES = list(INDEX_META.keys())

# ── 每日 API 调用量限额 ───────────────────────────────────────────────────────
# 聚宽付费版每日 1,000,000 次调用，留 5 万余量
DAILY_CALL_LIMIT = 950_000
PROGRESS_FILENAME = "progress.json"
SNAPSHOTS_FILENAME = "snapshots.parquet"


# ── 符号格式转换 ───────────────────────────────────────────────────────────────
def jq_to_qlib(jq_symbol: str) -> str:
    """聚宽格式 (600000.XSHG / 920xxx.XBSE) → Qlib 格式 (SH600000 / BJ920xxx)。"""
    code, exchange = jq_symbol.split(".")
    if exchange == "XSHG":
        return f"SH{code}"
    if exchange == "XSHE":
        return f"SZ{code}"
    if exchange == "XBSE":
        return f"BJ{code}"
    raise ValueError(f"unsupported jq exchange: {jq_symbol}")


def qlib_to_jq(qlib_symbol: str) -> str:
    """Qlib 格式 (SH600000 / BJ920xxx) → 聚宽格式。"""
    prefix, code = qlib_symbol[:2], qlib_symbol[2:]
    if prefix == "SH":
        return f"{code}.XSHG"
    if prefix == "SZ":
        return f"{code}.XSHE"
    if prefix == "BJ":
        return f"{code}.XBSE"
    raise ValueError(f"unsupported qlib symbol: {qlib_symbol}")


# ── 路径辅助 ──────────────────────────────────────────────────────────────────
def snapshots_path(index_name: str) -> Path:
    return CACHE_ROOT / index_name / SNAPSHOTS_FILENAME


def progress_path(index_name: str) -> Path:
    return CACHE_ROOT / index_name / PROGRESS_FILENAME


def instruments_output_path(index_name: str, suffix: str = "_jq") -> Path:
    """输出到 instruments 目录，默认加 _jq 后缀，不覆盖现有文件。"""
    return INSTRUMENTS_DIR / f"{index_name}{suffix}.txt"
