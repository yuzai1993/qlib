"""Shared cache contracts; all writes are confined to pytest's temporary roots."""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backtest.scripts.prepare import prepare_regime_train_chunks as cache


def spec_for(tmp_path, name="features"):
    source = tmp_path / "source.bin"
    if not source.exists():
        source.write_bytes(b"original")
    return {
        "kind": name,
        "recipe": {"columns": ["x"]},
        "sources": cache.fingerprint_files([source]),
        "parents": [],
    }


def frame():
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2019-01-02"]), ["SH600001", "SH600002"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"x": np.array([1, np.nan], dtype="float32")}, index=idx)


def test_hot_cache_never_calls_builder_and_preserves_values(tmp_path):
    spec = spec_for(tmp_path)
    manifest = cache.get_or_build_cache(
        tmp_path / "cache", spec, ["2019"], lambda key: frame()
    )

    def forbidden(key):
        raise AssertionError("hot cache rebuilt features")

    assert (
        cache.get_or_build_cache(tmp_path / "cache", spec, ["2019"], forbidden)
        == manifest
    )
    doc = cache.validate_cache_manifest(manifest, spec)
    assert doc["blocks"]["2019"]["summary"]["shape"] == [2, 1]
    assert doc["blocks"]["2019"]["summary"]["dtypes"] == ["float32"]
    pd.testing.assert_frame_equal(
        pd.read_pickle(manifest.parent / doc["blocks"]["2019"]["path"]), frame()
    )


def test_source_mutation_same_size_and_mtime_rejected(tmp_path):
    spec = spec_for(tmp_path)
    manifest = cache.get_or_build_cache(
        tmp_path / "cache", spec, ["2019"], lambda key: frame()
    )
    src = tmp_path / "source.bin"
    stat = src.stat()
    src.write_bytes(b"modified")
    os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="source"):
        cache.validate_cache_manifest(manifest, spec)


def test_partial_build_resumes_only_verified_blocks(tmp_path):
    spec = spec_for(tmp_path)
    calls = []

    def interrupted(key):
        calls.append(key)
        if key == "2020":
            raise RuntimeError("interrupted")
        return frame()

    with pytest.raises(RuntimeError, match="interrupted"):
        cache.get_or_build_cache(
            tmp_path / "cache", spec, ["2019", "2020"], interrupted
        )
    assert not list((tmp_path / "cache").glob("*/*/manifest.json"))
    cache.get_or_build_cache(
        tmp_path / "cache",
        spec,
        ["2019", "2020"],
        lambda key: calls.append(key) or frame(),
    )
    assert calls == ["2019", "2020", "2020"]


def test_corrupt_block_and_unknown_existing_files_rejected(tmp_path):
    spec = spec_for(tmp_path)
    root = tmp_path / "cache"
    manifest = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    doc = json.loads(manifest.read_text())
    (manifest.parent / doc["blocks"]["2019"]["path"]).write_bytes(b"bad")
    with pytest.raises(ValueError, match="block"):
        cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())


def test_source_change_during_build_never_commits(tmp_path):
    spec = spec_for(tmp_path)

    def drifting(key):
        (tmp_path / "source.bin").write_bytes(b"modified")
        return frame()

    with pytest.raises(ValueError, match="source"):
        cache.get_or_build_cache(tmp_path / "cache", spec, ["2019"], drifting)
    assert not list((tmp_path / "cache").glob("*/*/manifest.json"))


def test_lease_pins_entry_and_releases_on_error(tmp_path):
    spec = spec_for(tmp_path)
    root = tmp_path / "cache"
    manifest = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    with pytest.raises(RuntimeError):
        with cache.cache_lease(root, [manifest]):
            refs = list((root / ".references").glob("*.json"))
            assert refs and json.loads(refs[0].read_text())["manifests"] == [
                str(manifest)
            ]
            raise RuntimeError("consumer failed")
    assert not list((root / ".references").glob("*.json"))


def test_symlink_source_rejected(tmp_path):
    real = tmp_path / "real"
    real.write_text("data")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        cache.fingerprint_files([link])


def test_legacy_h40_ranks_before_listing_and_h20_ranks_after_alignment():
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2020-07-31"]), list("ABCD")], names=["datetime", "instrument"]
    )
    h40 = pd.Series([1.0, 1.0, 3.0, np.nan], index=idx)
    listing = pd.Series([False, True, True, True], index=idx)
    h20 = pd.Series([9.0, 2.0, 2.0, 5.0], index=idx)
    old40 = cache.legacy_training_labels(idx, h40, listing, 40)
    old20 = cache.legacy_training_labels(idx, h40, listing, 20, h20)
    assert old40.index.tolist() == [idx[1], idx[2]]
    np.testing.assert_array_equal(
        old40.to_numpy(), np.array([0.0, 1.73], dtype="float32")
    )
    np.testing.assert_array_equal(
        old20.to_numpy(), np.array([0.865, 0.865], dtype="float64")
    )
    assert old20.dtype == "float64"


def test_each_horizon_retains_own_missing_labels_without_common_intersection():
    idx = frame().index
    base = pd.Series([1.0, 2.0], index=idx)
    listing = pd.Series(True, index=idx)
    h20 = cache.legacy_training_labels(
        idx, base, listing, 20, pd.Series([np.nan, 7.0], index=idx)
    )
    h5 = cache.legacy_training_labels(
        idx, base, listing, 5, pd.Series([9.0, 7.0], index=idx)
    )
    assert h20.index.equals(idx) and h5.index.equals(idx)
    assert np.isnan(h20.iloc[0]) and h5.notna().all()
    assert h20.iloc[1] == 1.73


def test_different_protocols_get_distinct_keys(tmp_path):
    spec = spec_for(tmp_path, "labels")
    spec["recipe"]["sample_protocol"] = "legacy-h40-base-v1"
    key = cache.cache_key(spec)
    spec["recipe"]["sample_protocol"] = "seven-horizon-intersection"
    assert cache.cache_key(spec) != key


def training_pair(tmp_path):
    root = tmp_path / "cache"
    fs = spec_for(tmp_path)
    fs["recipe"].update(arm="m0", years=[2019], columns=["x"])
    fm = cache.get_or_build_cache(root, fs, ["2019"], lambda key: frame())
    ls = spec_for(tmp_path, "labels")
    ls["parents"] = cache.fingerprint_files([fm])
    ls["recipe"].update(
        arm="m0", horizon=20, years=[2019], sample_protocol="legacy-h40-base-v1"
    )
    lm = cache.get_or_build_cache(
        root,
        ls,
        ["2019"],
        lambda key: pd.Series([1.0, 2.0], index=frame().index, dtype="float32"),
    )
    dates = tmp_path / "dates.csv"
    dates.write_text("date\n2019-01-02\n")
    return fm, lm, dates


def test_training_uses_explicit_pair_and_rejects_wrong_horizon(tmp_path):
    from backtest.scripts.train.train_regime_arm import load_shared_train_matrix

    fm, lm, dates = training_pair(tmp_path)
    train, receipt = load_shared_train_matrix(fm, lm, "m0", [2019], 20, dates)
    assert train.shape == (2, 2)
    assert train.columns.tolist() == [("feature", "x"), ("label", "LABEL0")]
    assert train.iloc[:, 1].tolist() == [1.0, 2.0]
    assert (
        receipt["feature_manifest"]["sha256"]
        == cache.fingerprint_files([fm])[0]["sha256"]
    )
    with pytest.raises(ValueError, match="horizon"):
        load_shared_train_matrix(fm, lm, "m0", [2019], 40, dates)


def test_training_rejects_unpaired_label_manifest(tmp_path):
    from backtest.scripts.train.train_regime_arm import load_shared_train_matrix

    fm, lm, dates = training_pair(tmp_path)
    fs = json.loads(fm.read_text())["spec"]
    fs["recipe"]["variant"] = "another"
    other = cache.get_or_build_cache(
        tmp_path / "cache", fs, ["2019"], lambda key: frame()
    )
    with pytest.raises(ValueError, match="paired"):
        load_shared_train_matrix(other, lm, "m0", [2019], 20, dates)


def test_cleanup_preserves_active_and_newest_and_deletes_only_known_old_files(tmp_path):
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    root = tmp_path / "cache"
    spec = spec_for(tmp_path)
    old = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    spec["recipe"]["version"] = 2
    new = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    with cache.cache_lease(root, [old]):
        plan = cleanup_shared_cache(root, max_gib=0, apply=True)
        assert plan["candidates"] == [] and old.exists() and new.exists()
    plan = cleanup_shared_cache(root, max_gib=0)
    assert [r["manifest"] for r in plan["candidates"]] == [str(old)]
    assert old.exists()
    cleanup_shared_cache(root, max_gib=0, apply=True)
    assert not old.parent.exists() and new.exists()


def test_cleanup_protects_parent_and_rejects_unknown_files(tmp_path):
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    root = tmp_path / "cache"
    spec = spec_for(tmp_path)
    old = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    spec["recipe"]["version"] = 2
    cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    ls = spec_for(tmp_path, "labels")
    ls["parents"] = cache.fingerprint_files([old])
    cache.get_or_build_cache(root, ls, ["2019"], lambda key: frame())
    assert not cleanup_shared_cache(root, max_gib=0)["candidates"]
    (old.parent / "user.txt").write_text("keep me")
    with pytest.raises(ValueError, match="unknown"):
        cleanup_shared_cache(root, max_gib=0, apply=True)
    assert (old.parent / "user.txt").exists()


def test_shared_cleanup_mode_is_mutually_exclusive_with_results():
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import parse_args

    assert parse_args(["--only-shared-cache"]).only_shared_cache
    with pytest.raises(SystemExit):
        parse_args(["--only-shared-cache", "--only-result"])


def _cache_writer(root, spec, counter):
    def build(key):
        with Path(counter).open("a") as handle:
            handle.write("build\n")
        return frame()

    cache.get_or_build_cache(Path(root), spec, ["2019"], build)


def test_two_processes_publish_one_complete_cache(tmp_path):
    root, counter = tmp_path / "cache", tmp_path / "calls"
    spec = spec_for(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    workers = [
        ctx.Process(target=_cache_writer, args=(str(root), spec, str(counter)))
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            assert worker.exitcode == 0
        assert counter.read_text().splitlines() == ["build"]
        assert len(list(root.glob("*/*/manifest.json"))) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()


def test_cleanup_journal_resumes_after_partial_deletion(tmp_path, monkeypatch):
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    root = tmp_path / "cache"
    spec = spec_for(tmp_path)
    old = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    spec["recipe"]["version"] = 2
    new = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == old.parent / "access.json":
            raise OSError("simulated interruption")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="interruption"):
            cleanup_shared_cache(root, max_gib=0, apply=True)
    assert not (old.parent / "2019.pkl").exists()
    assert (root / ".cleanup-journal.json").exists()
    with pytest.raises(ValueError, match="pending cleanup"):
        with cache.cache_lease(root, [old]):
            pass
    cleanup_shared_cache(root, max_gib=0, apply=True)
    assert not old.parent.exists() and new.exists()
    assert not (root / ".cleanup-journal.json").exists()


def test_validation_cache_reuses_payload_and_st_change_invalidates(
    tmp_path, monkeypatch
):
    from backtest.scripts.train import train_regime_arm as trainer

    root = tmp_path / "cache"
    st = tmp_path / "st.csv"
    st.write_text("old")
    monkeypatch.setattr(cache, "SHARED_CACHE_ROOT", root)
    monkeypatch.setattr(
        cache, "shared_feature_spec", lambda arm, years: spec_for(tmp_path)
    )
    monkeypatch.setattr(trainer, "ST_DAILY", st)
    monkeypatch.setattr(
        trainer,
        "build_t5h5es_valid_frame",
        lambda *a, **k: (frame(), pd.Series(True, index=frame().index)),
    )
    f1, m1, r1 = trainer.load_shared_valid_frame("m0", "eval_window")

    def forbidden(*args, **kwargs):
        raise AssertionError("identical validation inputs were rebuilt")

    monkeypatch.setattr(trainer, "build_t5h5es_valid_frame", forbidden)
    f2, m2, r2 = trainer.load_shared_valid_frame("m0", "eval_window")
    pd.testing.assert_frame_equal(f1, f2)
    pd.testing.assert_series_equal(m1, m2)
    assert r1 == r2
    st.write_text("new")
    with pytest.raises(AssertionError, match="rebuilt"):
        trainer.load_shared_valid_frame("m0", "eval_window")


def test_cache_management_rejects_symlink_reference_directory(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (root / ".references").symlink_to(external, target_is_directory=True)
    spec = spec_for(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    assert not list(external.iterdir())


def test_alternate_labels_preserve_old_overlay_precision():
    from backtest.scripts.prepare.build_regime_alt_labels import csranknorm

    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2019-01-02"]), list("ABC")], names=["datetime", "instrument"]
    )
    raw = pd.Series([0.1, 0.3, 0.2], index=idx)
    actual = cache.legacy_training_labels(idx, raw, pd.Series(True, index=idx), 20, raw)
    pd.testing.assert_series_equal(
        actual, csranknorm(raw).rename("LABEL0"), check_exact=True
    )


@pytest.mark.parametrize("during_build", [False, True])
def test_new_source_membership_is_detected(tmp_path, during_build):
    spec = spec_for(tmp_path)
    inventory = tmp_path / "provider"
    inventory.mkdir()
    spec["source_inventory"] = [{"root": str(inventory), "glob": "*.bin", "paths": []}]

    def build(key):
        if during_build:
            (inventory / "previously_missing.bin").write_bytes(b"new data")
        return frame()

    if during_build:
        with pytest.raises(ValueError, match="source inventory"):
            cache.get_or_build_cache(tmp_path / "cache", spec, ["2019"], build)
    else:
        manifest = cache.get_or_build_cache(tmp_path / "cache", spec, ["2019"], build)
        (inventory / "previously_missing.bin").write_bytes(b"new data")
        with pytest.raises(ValueError, match="source inventory"):
            cache.validate_cache_manifest(manifest, spec)


def test_producer_transfers_pin_until_consumers_finish(tmp_path):
    from contextlib import ExitStack
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    spec = spec_for(tmp_path)
    root = tmp_path / "cache"
    with ExitStack() as leases:
        old = cache.get_or_build_cache(
            root, spec, ["2019"], lambda key: frame(), lease_stack=leases
        )
        spec["recipe"]["version"] = 2
        cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
        assert not cleanup_shared_cache(root, max_gib=0, apply=True)["candidates"]
        assert old.exists()
    assert cleanup_shared_cache(root, max_gib=0)["candidates"]


def test_hot_read_recovers_committed_progress_after_crash(tmp_path, monkeypatch):
    spec = spec_for(tmp_path)
    root = tmp_path / "cache"
    publish = cache.publish_cache

    def interrupted(*args, **kwargs):
        publish(*args, **kwargs)
        raise RuntimeError("after manifest commit")

    with monkeypatch.context() as patch:
        patch.setattr(cache, "publish_cache", interrupted)
        with pytest.raises(RuntimeError, match="commit"):
            cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    manifest = cache.get_or_build_cache(
        root, spec, ["2019"], lambda key: pytest.fail("committed block rebuilt")
    )
    assert not (manifest.parent / "progress.json").exists()
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    assert cleanup_shared_cache(root)["result"] == "no_changes"


def test_new_child_cannot_reference_journaled_target(tmp_path, monkeypatch):
    from backtest.scripts.maintenance.cleanup_experiment_artifacts import (
        cleanup_shared_cache,
    )

    root = tmp_path / "cache"
    spec = spec_for(tmp_path)
    old = cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    spec["recipe"]["version"] = 2
    cache.get_or_build_cache(root, spec, ["2019"], lambda key: frame())
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == old.parent / "2019.pkl":
            raise OSError("interrupted")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError):
            cleanup_shared_cache(root, max_gib=0, apply=True)
    child = spec_for(tmp_path, "labels")
    child["parents"] = cache.fingerprint_files([old])
    with pytest.raises(ValueError, match="pending cleanup"):
        cache.get_or_build_cache(root, child, ["2019"], lambda key: frame())
    assert old.exists()


def test_validation_dependency_changes_invalidate_keys(tmp_path, monkeypatch):
    from backtest.scripts.train import train_regime_arm as trainer

    monkeypatch.setattr(
        cache, "shared_feature_spec", lambda arm, years: spec_for(tmp_path)
    )
    monkeypatch.setattr(trainer, "EXP_ROOT", tmp_path)
    st = tmp_path / "st.csv"
    st.write_text("old")
    monkeypatch.setattr(trainer, "ST_DAILY", st)
    deps = [
        "backtest/scripts/evaluate/eval_ic_multi_pool.py",
        "backtest/scripts/evaluate/universe_filter.py",
        "backtest/models/rankic_early_stop.py",
        "qlib/backtest/cn_limit.py",
        "scripts/data_collector/tushare/st_calendar.py",
    ]
    for relative in deps:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old")
    first = cache.cache_key(trainer.shared_validation_spec("m0", "eval_window"))
    (tmp_path / "qlib/backtest/cn_limit.py").write_text("new limits")
    second = cache.cache_key(trainer.shared_validation_spec("m0", "eval_window"))
    assert first != second
    (tmp_path / "scripts/data_collector/tushare/st_calendar.py").write_text(
        "new ST rules"
    )
    assert second != cache.cache_key(
        trainer.shared_validation_spec("m0", "eval_window")
    )


def test_named_provider_pool_is_a_content_dependency(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    for relative, content in {
        "features/sh600001/close.day.bin": "prices",
        "instruments/all.txt": "all",
        "instruments/csi300.txt": "pool",
        "calendars/day.txt": "2026-11-02",
    }.items():
        path = provider / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    source = tmp_path / "backtest/features/technical.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("fixture")
    monkeypatch.setattr(cache, "EXP_ROOT", tmp_path)
    spec = cache.shared_feature_spec("m0", [2019], provider=provider)
    pool = provider / "instruments/csi300.txt"
    assert str(pool) in {r["path"] for r in spec["sources"]}
    pool.write_text("changed")
    with pytest.raises(ValueError, match="source"):
        cache._verify_sources(spec)


def test_label_expression_dependency_is_separate_from_features(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "EXP_ROOT", tmp_path)
    for relative in [
        "backtest/scripts/prepare/build_regime_alt_labels.py",
        "backtest/scripts/evaluate/eval_ic_multi_pool.py",
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("old")
    spec = spec_for(tmp_path)
    before = cache.shared_label_sources(spec)
    (tmp_path / "backtest/scripts/evaluate/eval_ic_multi_pool.py").write_text(
        "new formula"
    )
    assert before != cache.shared_label_sources(spec)
    cache._verify_sources(spec)  # unchanged features remain valid


def test_weight_identity_changes_without_rebuilding_features(tmp_path):
    from backtest.scripts.train.train_regime_arm import load_shared_train_matrix

    fm, lm, dates = training_pair(tmp_path)
    weights = tmp_path / "weights.csv"
    weights.write_text("date,weight\n2019-01-02,1\n")
    first, r1 = load_shared_train_matrix(
        fm, lm, "m0", [2019], 20, dates, weights_csv=weights
    )
    weights.write_text("date,weight\n2019-01-02,2\n")
    second, r2 = load_shared_train_matrix(
        fm, lm, "m0", [2019], 20, dates, weights_csv=weights
    )
    pd.testing.assert_frame_equal(first, second)
    assert r1["feature_manifest"] == r2["feature_manifest"]
    assert r1["day_weights"]["sha256"] != r2["day_weights"]["sha256"]
