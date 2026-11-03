"""regime-adapt 训练数据分块预处理（计划 v3 7.2 节第 1 步）.

对 2004~2020 每年建一个短跨度 handler（前置 6 个月 warmup 供 Alpha158 滚动窗，
后延 3 个月供 H40 标签前视；2020 年 handler 截止 2020-07-31，标签窗跨界的行由
DropnaLabel 自动剔除 = purge），processor 后 learn 数据（DK_L）裁剪到本年训练日，
剔除上市 < 60 交易日次新股，降为 float32 pickle 落盘。

CSRankNorm（label 逐日截面）与 DropnaLabel 均为日级操作，分年处理无跨界影响。
保留全部训练时代交易日（D 态下采样在组装阶段由 train_dates_v1.csv 完成，缓存
与采样协议解耦）。

用法:
  python backtest/scripts/prepare/prepare_regime_train_chunks.py --arm m3 [--years 2019]
产出:
  backtest/datasets/regime-adapt-cache/<arm>/year=YYYY.pkl  （跑完实验按规范 6.3 清理）
"""

from __future__ import annotations

# Support direct CLI execution from any working directory.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))


import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import gc
import hashlib
import json
import os
import re
import resource
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

EXP_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXP_ROOT))

CACHE_ROOT = EXP_ROOT / "backtest" / "datasets" / "regime-adapt-cache"
SHARED_CACHE_ROOT = CACHE_ROOT / "shared-v1"
REGIME_CSV = (
    EXP_ROOT / "backtest" / "configs" / "regime-adapt" / "regime_features_v1.csv"
)

TRAIN_START = pd.Timestamp("2004-01-02")
TRAIN_END = pd.Timestamp("2020-07-31")
LABEL_EXPR = "Ref($close, -41)/Ref($close, -1)-1"
MIN_LISTING_DAYS = 60

# 训练池: 全A 股票（剔除指数/B股/北交所），与 8 年 dry-run 一致
INSTRUMENTS = {
    "market": "all",
    "filter_pipe": [
        {
            "filter_type": "NameDFilter",
            "name_rule_re": "^(SH60|SH68|SZ00|SZ30)",
            "filter_start_time": None,
            "filter_end_time": None,
        }
    ],
}


def _json_bytes(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()


def _plain_path(path: Path) -> Path:
    path = Path(path).expanduser().absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"unsafe path or symlink: {path}")
    return path


def fingerprint_files(paths) -> list[dict]:
    """Content identity, deliberately independent of mtime and file size hints."""
    receipts = []
    for path in sorted({_plain_path(p) for p in paths}):
        if not path.is_file():
            raise ValueError(f"missing source file: {path}")
        before = path.stat()
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"source changed while hashing: {path}")
        receipts.append({"path": str(path), "sha256": digest, "size": after.st_size})
    return receipts


def cache_key(spec: dict) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", spec.get("kind", "")):
        raise ValueError("invalid cache kind")
    return hashlib.sha256(_json_bytes({"schema_version": 1, "spec": spec})).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path = _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def cache_lock(root: Path, name: str = "management"):
    root = _plain_path(root)
    locks = root / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    path = _plain_path(locks / f"{name}.lock")
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def cache_lease(root: Path, manifests):
    """Pin before opening; crashed consumers leave conservative persistent pins."""
    root = _plain_path(root)
    paths = sorted({_plain_path(p) for p in manifests})
    if any(not p.is_relative_to(root) or p.name != "manifest.json" for p in paths):
        raise ValueError("cache reference outside shared root")
    receipt = root / ".references" / f"{uuid.uuid4().hex}.json"
    with cache_lock(root):
        journal = root / ".cleanup-journal.json"
        if journal.exists():
            pending = json.loads(_plain_path(journal).read_text())
            targets = {r["manifest"] for r in pending["targets"]}
            if targets.intersection(str(p) for p in paths):
                raise ValueError(
                    "cache entry has pending cleanup; cannot acquire a new reference"
                )
        _atomic_json(
            receipt,
            {
                "pid": os.getpid(),
                "created_at": time.time(),
                "manifests": [str(p) for p in paths],
            },
        )
    try:
        yield
    finally:
        with cache_lock(root):
            receipt.unlink()


def _verify_sources(spec: dict) -> None:
    sources = spec.get("sources", [])
    recorded = {r["path"] for r in sources}
    for inventory in spec.get("source_inventory", []):
        actual = sorted(
            str(_plain_path(p))
            for p in _plain_path(Path(inventory["root"])).glob(inventory["glob"])
            if p.is_file()
        )
        if actual != inventory["paths"] or not set(actual).issubset(recorded):
            raise ValueError(
                "cache source inventory changed or contains unhashed dependencies"
            )
    if fingerprint_files([r["path"] for r in sources]) != sources:
        raise ValueError("cache source content changed; rebuild with a new identity")
    for parent in spec.get("parents", []):
        if fingerprint_files([parent["path"]]) != [parent]:
            raise ValueError("cache parent manifest changed")


def _block_receipt(path: Path) -> dict:
    result = fingerprint_files([path])[0]
    result["path"] = path.name
    return result


def cache_value_summary(value):
    """Logical evidence alongside the block's physical SHA receipt."""
    if isinstance(value, dict):
        return {key: cache_value_summary(item) for key, item in value.items()}
    if value is None:
        return None
    if not isinstance(value, (pd.DataFrame, pd.Series)):
        raise TypeError("shared cache blocks must contain pandas frames/series")
    return {
        "shape": list(value.shape),
        "columns": (
            [str(c) for c in value.columns]
            if isinstance(value, pd.DataFrame)
            else [str(value.name)]
        ),
        "dtypes": (
            [str(d) for d in value.dtypes]
            if isinstance(value, pd.DataFrame)
            else [str(value.dtype)]
        ),
        "index_sha256": hashlib.sha256(
            pd.util.hash_pandas_object(
                value.index.to_frame(index=False).astype(str), index=False
            )
            .to_numpy(dtype="<u8")
            .tobytes()
        ).hexdigest(),
        "values_sha256": hashlib.sha256(
            pd.util.hash_pandas_object(value, index=False)
            .to_numpy(dtype="<u8")
            .tobytes()
        ).hexdigest(),
    }


def _verify_blocks(directory: Path, blocks: dict) -> None:
    for key, receipt in blocks.items():
        if receipt["path"] != f"{key}.pkl" or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ValueError("invalid cache block path")
        path = _plain_path(directory / receipt["path"])
        if not path.is_file() or _block_receipt(path) != {
            k: receipt[k] for k in ("path", "sha256", "size")
        }:
            raise ValueError(f"cache block corrupted or missing: {path}")


def validate_cache_manifest(
    manifest_path: Path, expected_spec: dict, *, verify_sources: bool = True
) -> dict:
    path = _plain_path(manifest_path)
    doc = json.loads(path.read_text())
    checksum = doc.pop("checksum", None)
    if checksum != hashlib.sha256(_json_bytes(doc)).hexdigest():
        raise ValueError("cache manifest checksum mismatch")
    if (
        doc.get("schema_version") != 1
        or doc.get("spec") != expected_spec
        or doc.get("key") != cache_key(expected_spec)
    ):
        raise ValueError("cache manifest identity mismatch")
    if (
        path.parent.name != doc["key"]
        or path.parent.parent.name != expected_spec["kind"]
    ):
        raise ValueError("cache manifest location mismatch")
    if not doc.get("blocks"):
        raise ValueError("empty cache manifest")
    if verify_sources:
        _verify_sources(expected_spec)
    _verify_blocks(path.parent, doc["blocks"])
    doc["checksum"] = checksum
    return doc


def publish_cache(
    manifest_path: Path,
    spec: dict,
    blocks: dict[str, Path],
    *,
    summaries: dict | None = None,
) -> dict:
    """Called under the key lock, after all block receipts have been checked."""
    _verify_sources(spec)
    receipts = {key: _block_receipt(path) for key, path in blocks.items()}
    for key, summary in (summaries or {}).items():
        receipts[key]["summary"] = summary
    _verify_blocks(manifest_path.parent, receipts)
    doc = {
        "schema_version": 1,
        "key": cache_key(spec),
        "spec": spec,
        "blocks": receipts,
        "created_at": time.time(),
    }
    doc["checksum"] = hashlib.sha256(_json_bytes(doc)).hexdigest()
    _atomic_json(manifest_path, doc)
    return doc


def get_or_build_cache(
    root: Path, spec: dict, blocks, builder, *, lease_stack: ExitStack | None = None
) -> Path:
    """Transactional, resumable materialization. Builder returns a pickle-able block."""
    root = _plain_path(root)
    keys = sorted(set(str(k) for k in blocks))
    if not keys or any(not re.fullmatch(r"[a-zA-Z0-9_-]+", key) for key in keys):
        raise ValueError("invalid cache block keys")
    key = cache_key(spec)
    directory = _plain_path(root / spec["kind"] / key)
    manifest = directory / "manifest.json"
    parents = [Path(p["path"]) for p in spec.get("parents", [])]
    if lease_stack is not None:
        lease_stack.enter_context(cache_lease(root, [manifest, *parents]))
    with cache_lease(root, [manifest, *parents]), cache_lock(root, key):
        if manifest.exists():
            doc = validate_cache_manifest(manifest, spec)
            if sorted(doc["blocks"]) != keys:
                raise ValueError(
                    "cache block coverage mismatch; include coverage in recipe"
                )
            progress_path = directory / "progress.json"
            if progress_path.exists():
                progress = json.loads(_plain_path(progress_path).read_text())
                if progress != {"spec": spec, "keys": keys, "blocks": doc["blocks"]}:
                    raise ValueError("committed cache has unknown progress data")
                progress_path.unlink()
        else:
            _verify_sources(spec)
            directory.mkdir(parents=True, exist_ok=True)
            progress_path = directory / "progress.json"
            if progress_path.exists():
                progress = json.loads(progress_path.read_text())
                if progress.get("spec") != spec or progress.get("keys") != keys:
                    raise ValueError("partial cache identity mismatch")
                _verify_blocks(directory, progress["blocks"])
            else:
                if any(directory.iterdir()):
                    raise ValueError("unknown files in uncommitted cache")
                progress = {"spec": spec, "keys": keys, "blocks": {}}
                _atomic_json(progress_path, progress)
            for name in keys:
                if name in progress["blocks"]:
                    continue
                path = directory / f"{name}.pkl"
                if path.exists():
                    raise ValueError(f"unreceipted cache block: {path}")
                tmp = directory / f".{name}.{uuid.uuid4().hex}.tmp"
                try:
                    value = builder(name)
                    summary = cache_value_summary(value)
                    pd.to_pickle(value, tmp)
                    del value
                    gc.collect()
                    os.replace(tmp, path)
                    progress["blocks"][name] = _block_receipt(path)
                    progress["blocks"][name]["summary"] = summary
                    _atomic_json(progress_path, progress)
                finally:
                    tmp.unlink(missing_ok=True)
            publish_cache(
                manifest,
                spec,
                {name: directory / f"{name}.pkl" for name in keys},
                summaries={name: progress["blocks"][name]["summary"] for name in keys},
            )
            progress_path.unlink()
        with cache_lock(root):
            _atomic_json(directory / "access.json", {"last_used": time.time()})
    return manifest


def rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def build_handler(
    arm: str, start: str, end: str, instruments=None, *, learn: bool = True
):
    common = dict(
        feature_groups=["range"],
        instruments=instruments if instruments is not None else INSTRUMENTS,
        start_time=start,
        end_time=end,
        fit_start_time=start,
        fit_end_time=end,
        infer_processors=[{"class": "ProcessInf"}],
        learn_processors=[
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        label=[[LABEL_EXPR], ["LABEL0"]],
    )
    if not learn:
        common["learn_processors"] = []
    if arm == "m3":
        from backtest.features.regime import Alpha158RegimeTechnical

        return Alpha158RegimeTechnical(regime_csv=str(REGIME_CSV), **common)
    if arm == "m0":
        from backtest.features.technical import Alpha158Technical

        return Alpha158Technical(**common)
    raise ValueError(f"unknown arm: {arm}")


def shared_source_files(arm: str, provider: Path | None = None) -> list[Path]:
    """Conservative provider snapshot: includes file contents, not a copied date label."""
    provider = _plain_path(provider or Path.home() / ".qlib/qlib_data/cn_data")
    paths = [provider / "calendars/day.txt", provider / "instruments/all.txt"]
    paths.extend((provider / "instruments").glob("*.txt"))
    files = sorted((provider / "features").glob("*/*.day.bin"))
    if not files:
        raise ValueError("missing provider feature files")
    paths.extend(files)
    paths.extend((EXP_ROOT / "qlib").rglob("*.py"))
    paths.extend((EXP_ROOT / "qlib").rglob("*.so"))
    paths.extend([Path(__file__), EXP_ROOT / "backtest/features/technical.py"])
    if arm == "m3":
        paths.extend([REGIME_CSV, EXP_ROOT / "backtest/features/regime.py"])
    return paths


def shared_feature_spec(
    arm: str, years: list[int], *, provider: Path | None = None
) -> dict:
    from backtest.features.technical import Alpha158Technical

    if (
        arm not in ("m0", "m3")
        or not years
        or any(type(y) is not int or y not in range(2004, 2021) for y in years)
    ):
        raise ValueError("invalid shared feature arm/years")
    obj = Alpha158Technical.__new__(Alpha158Technical)
    obj.feature_groups = ("range",)
    expressions, columns = obj.get_feature_config()
    if arm == "m3":
        columns += [
            "REGIME_" + c
            for c in pd.read_csv(REGIME_CSV, index_col=0, comment="#", nrows=0).columns
        ]
    provider = _plain_path(provider or Path.home() / ".qlib/qlib_data/cn_data")
    sources = fingerprint_files(shared_source_files(arm, provider))
    inventory = [
        {
            "root": str(path),
            "glob": pattern,
            "paths": sorted(
                str(_plain_path(p)) for p in path.glob(pattern) if p.is_file()
            ),
        }
        for path, pattern in [
            (provider / "features", "*/*.day.bin"),
            (provider / "instruments", "*.txt"),
            (EXP_ROOT / "qlib", "**/*.py"),
            (EXP_ROOT / "qlib", "**/*.so"),
        ]
    ]
    return {
        "kind": "features",
        "sources": sources,
        "source_inventory": inventory,
        "parents": [],
        "recipe": {
            "arm": arm,
            "years": sorted(set(years)),
            "provider": str(provider),
            "calendar_last": (provider / "calendars/day.txt")
            .read_text()
            .splitlines()[-1],
            "instruments": INSTRUMENTS,
            "expressions": expressions,
            "columns": columns,
            "infer_processors": ["ProcessInf"],
            "learn_processors": [],
            "dtype": "float32",
            "train_start": str(TRAIN_START.date()),
            "train_end": str(TRAIN_END.date()),
            "warmup_months": 6,
            "trailing_months": 3,
            "handler_label": LABEL_EXPR,
            "numeric_environment": {
                "python": sys.version,
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
        },
    }


def _year_bounds(year: int):
    return (
        f"{year - 1}-07-01",
        str(min(pd.Timestamp(f"{year + 1}-03-31"), TRAIN_END).date()),
        max(pd.Timestamp(f"{year}-01-01"), TRAIN_START),
        min(pd.Timestamp(f"{year}-12-31"), TRAIN_END),
    )


def prepare_shared_features(
    arm: str, years: list[int], *, root: Path = SHARED_CACHE_ROOT
) -> Path:
    from qlib.data.dataset.handler import DataHandlerLP

    spec = shared_feature_spec(arm, years)

    def build(key):
        start, end, lo, hi = _year_bounds(int(key))
        t0 = time.monotonic()
        handler = build_handler(arm, start, end, learn=False)
        result = handler.fetch(
            slice(lo, hi), col_set="feature", data_key=DataHandlerLP.DK_I
        ).astype("float32")
        if (
            result.index.has_duplicates
            or not result.index.is_monotonic_increasing
            or list(result.columns) != spec["recipe"]["columns"]
        ):
            raise ValueError("shared feature index/column contract mismatch")
        print(
            f"[shared {arm} {key}] {result.shape}, {time.monotonic() - t0:.1f}s",
            flush=True,
        )
        return result

    return get_or_build_cache(root, spec, years, build)


def legacy_training_labels(
    index, h40: pd.Series, listing: pd.Series, horizon: int, alternate=None
) -> pd.Series:
    """Keep historical H40 rank-before-listing and alternate rank-after-listing distinct."""
    base = h40.reindex(index).dropna()
    if horizon == 40:
        ranked = (base.groupby(level="datetime").rank(pct=True) - 0.5) * 3.46
        result = ranked.loc[listing.reindex(ranked.index).fillna(False).to_numpy()]
    else:
        if alternate is None:
            raise ValueError("alternate horizon requires its own raw labels")
        base = base.loc[listing.reindex(base.index).fillna(False).to_numpy()]
        aligned = alternate.reindex(base.index)
        result = (aligned.groupby(level="datetime").rank(pct=True) - 0.5) * 3.46
    if np.isinf(result.to_numpy()).any():
        raise ValueError("infinite training labels")
    # Only the original H40 annual frame was cast to float32. Alternate overlays
    # stayed float64 all the way into fit_prepared; rounding here changes labels.
    return (result.astype("float32") if horizon == 40 else result).rename("LABEL0")


def shared_label_sources(feature_spec: dict) -> list[dict]:
    receipts = {r["path"]: r for r in feature_spec["sources"]}
    for receipt in fingerprint_files(
        [
            EXP_ROOT / "backtest/scripts/prepare/build_regime_alt_labels.py",
            EXP_ROOT / "backtest/scripts/evaluate/eval_ic_multi_pool.py",
        ]
    ):
        receipts[receipt["path"]] = receipt
    return sorted(receipts.values(), key=lambda r: r["path"])


def prepare_shared_labels(
    feature_manifest: Path, horizon: int, *, root: Path = SHARED_CACHE_ROOT
) -> Path:
    from qlib.data import D
    from qlib.data.dataset.processor import ProcessInf
    from backtest.scripts.prepare.build_regime_alt_labels import fetch_raw
    from backtest.scripts.evaluate.eval_ic_multi_pool import _horizon_label_expr

    if type(horizon) is not int or horizon not in (1, 2, 3, 5, 7, 10, 15, 20, 30, 40):
        raise ValueError("unsupported label horizon")
    feature_manifest = _plain_path(feature_manifest)
    with cache_lease(root, [feature_manifest]), ExitStack() as parent_leases:
        fdoc = json.loads(feature_manifest.read_text())
        validate_cache_manifest(feature_manifest, fdoc["spec"])
        if fdoc["spec"]["kind"] != "features":
            raise ValueError(
                "labels require complete pure features, not legacy filtered blocks"
            )
        years = fdoc["spec"]["recipe"]["years"]
        sources = shared_label_sources(fdoc["spec"])
        inventory = fdoc["spec"].get("source_inventory", [])
        raw_spec = {
            "kind": "raw-labels",
            "sources": sources,
            "source_inventory": inventory,
            "parents": [],
            "recipe": {
                "years": years,
                "horizon": 40,
                "expression": LABEL_EXPR,
                "instruments": INSTRUMENTS,
                "processor": "ProcessInf",
                "year_bounds": [list(map(str, _year_bounds(y))) for y in years],
            },
        }

        def base_labels(key):
            start, end, lo, hi = _year_bounds(int(key))
            data = D.features(INSTRUMENTS, [LABEL_EXPR], start_time=start, end_time=end)
            data = data.swaplevel().sort_index()
            data.index = data.index.set_names(["datetime", "instrument"])
            data = ProcessInf()(data)
            return data.loc[(slice(lo, hi), slice(None)), :].iloc[:, 0]

        base_manifest = get_or_build_cache(
            root, raw_spec, years, base_labels, lease_stack=parent_leases
        )
        parents = [feature_manifest, base_manifest]
        alternate = None
        if horizon != 40:
            alt_spec = {
                "kind": "raw-labels",
                "sources": sources,
                "source_inventory": inventory,
                "parents": [],
                "recipe": {
                    "horizon": horizon,
                    "expression": _horizon_label_expr(horizon),
                    "start": str(TRAIN_START.date()),
                    "end": str(TRAIN_END.date()),
                    "instruments": "all",
                    "processor": None,
                },
            }
            alt_manifest = get_or_build_cache(
                root,
                alt_spec,
                ["all"],
                lambda key: fetch_raw(
                    horizon, str(TRAIN_START.date()), str(TRAIN_END.date())
                ),
                lease_stack=parent_leases,
            )
            parents.append(alt_manifest)
        with cache_lease(root, parents):
            if horizon != 40:
                alternate = pd.read_pickle(alt_manifest.parent / "all.pkl")
            cal = pd.DatetimeIndex(
                D.calendar(start_time="2003-01-01", end_time=str(TRAIN_END.date()))
            )
            first = listing_first_pos(cal)
            spec = {
                "kind": "labels",
                "sources": sources,
                "source_inventory": inventory,
                "parents": fingerprint_files(parents),
                "recipe": {
                    "horizon": horizon,
                    "arm": fdoc["spec"]["recipe"]["arm"],
                    "years": years,
                    "sample_protocol": "legacy-h40-base-v1",
                    "min_listing_days": MIN_LISTING_DAYS,
                    "rank_order": (
                        "h40-before-listing"
                        if horizon == 40
                        else "alternate-after-h40-and-listing"
                    ),
                },
            }

            def build(key):
                features = pd.read_pickle(feature_manifest.parent / f"{key}.pkl")
                h40 = pd.read_pickle(base_manifest.parent / f"{key}.pkl")
                listing = build_listing_mask(features.index, cal, first)
                return legacy_training_labels(
                    features.index, h40, listing, horizon, alternate
                )

            return get_or_build_cache(root, spec, years, build)


def build_listing_mask(
    index: pd.MultiIndex, cal: pd.DatetimeIndex, first_pos: dict
) -> pd.Series:
    dt_pos = np.asarray(cal.searchsorted(index.get_level_values("datetime")))
    inst_first = np.array(
        [first_pos.get(i, 10**9) for i in index.get_level_values("instrument")],
        dtype=np.int64,
    )
    return pd.Series((dt_pos - inst_first) >= MIN_LISTING_DAYS, index=index)


def listing_first_pos(cal: pd.DatetimeIndex) -> dict:
    from qlib.data import D

    spans = D.list_instruments(
        D.instruments("all"),
        start_time=str(cal[0].date()),
        end_time=str(cal[-1].date()),
        as_list=False,
    )
    return {
        code: int(cal.searchsorted(min(pd.Timestamp(s) for s, _ in sp)))
        for code, sp in spans.items()
    }


def prepare_year(
    arm: str, year: int, cal: pd.DatetimeIndex, first_pos: dict, out_dir: Path
) -> None:
    out_path = out_dir / f"year={year}.pkl"
    if out_path.exists():
        print(f"[{arm} {year}] 已存在, 跳过", flush=True)
        return
    t0 = time.time()
    # warmup 6 个月（Alpha158 最长滚动窗 60 日）；后延 3 个月给 H40 前视标签
    start = f"{year - 1}-07-01"
    end = min(pd.Timestamp(f"{year + 1}-03-31"), TRAIN_END)
    row_lo = max(pd.Timestamp(f"{year}-01-01"), TRAIN_START)
    row_hi = min(pd.Timestamp(f"{year}-12-31"), TRAIN_END)

    handler = build_handler(arm, start, str(end.date()))
    from qlib.data.dataset.handler import DataHandlerLP

    df = handler.fetch(
        slice(row_lo, row_hi), col_set=["feature", "label"], data_key=DataHandlerLP.DK_L
    )
    del handler
    gc.collect()

    mask = build_listing_mask(df.index, cal, first_pos)
    n_drop = int((~mask).sum())
    df = df[mask.to_numpy()]
    df = df.astype("float32")

    df.to_pickle(out_path)
    mem = df.memory_usage(deep=True).sum() / 1e9
    print(
        f"[{arm} {year}] {df.shape[0]} 行 x {df.shape[1]} 列 "
        f"(剔次新 {n_drop} 行), {mem:.2f} GB, 峰值 RSS {rss_gb():.2f} GB, "
        f"{time.time() - t0:.0f}s",
        flush=True,
    )
    del df
    gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=["m0", "m3"])
    parser.add_argument(
        "--years", type=int, nargs="*", default=None, help="默认 2004..2020 全量"
    )
    parser.add_argument(
        "--shared", action="store_true", help="构建可复用纯特征缓存，输出 manifest 路径"
    )
    parser.add_argument("--kernels", type=int, default=2, help="取数并发，默认 2")
    args = parser.parse_args()

    import qlib

    qlib.init(
        provider_uri="~/.qlib/qlib_data/cn_data", region="cn", kernels=args.kernels
    )
    from qlib.data import D

    if args.shared:
        print(
            prepare_shared_features(args.arm, args.years or list(range(2004, 2021))),
            flush=True,
        )
        return

    out_dir = CACHE_ROOT / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    cal = pd.DatetimeIndex(
        D.calendar(start_time="2003-01-01", end_time=str(TRAIN_END.date()))
    )
    first_pos = listing_first_pos(cal)

    years = args.years or list(range(2004, 2021))
    for year in years:
        prepare_year(args.arm, year, cal, first_pos, out_dir)
    print(
        f"[{args.arm}] 全部完成: {sorted(p.name for p in out_dir.glob('year=*.pkl'))}",
        flush=True,
    )


if __name__ == "__main__":
    main()
