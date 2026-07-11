"""每日维护：4 次 JQ 调用更新四指数当日成分。

- csi2000：差分写入正式 instruments/csi2000.txt（公告附件不全，以聚宽为准）
- csi300/500/1000：只更新本地快照，供与 csindex_v2 交叉校验（不覆盖正式文件）

典型用法：
    python -m scripts.data_collector.jq_index.cli update
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from . import config as cfg
from .builder import build
from .puller import _append_snapshots, _save_progress, load_snapshots, _get_jq

OPEN_END = "2099-12-31"
CSI2000_WRITE_INDICES = ("csi2000",)
CROSSCHECK_INDICES = ("csi300", "csi500", "csi1000")


def _load_instruments(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "start_date", "end_date"])
    df = pd.read_csv(
        path, sep="\t", header=None, names=["symbol", "start_date", "end_date"], dtype=str
    )
    return df


def _write_instruments(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{r.symbol}\t{r.start_date}\t{r.end_date}" for r in df.itertuples(index=False)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def apply_today_diff_to_instruments(
    index_name: str,
    today_set: set[str],
    today_str: str,
) -> dict:
    """
    在现有 instruments 上应用今日成分差分（不依赖完整历史快照）。

    - 调出：把仍在册区间的 end_date 改为 today
    - 调入：追加 [today, 2099-12-31)
    """
    path = cfg.INSTRUMENTS_DIR / f"{index_name}.txt"
    df = _load_instruments(path)
    if df.empty:
        # 无历史文件：用今日全量初始化
        rows = [
            {"symbol": s, "start_date": today_str, "end_date": OPEN_END}
            for s in sorted(today_set)
        ]
        out = pd.DataFrame(rows)
        _write_instruments(out, path)
        logger.warning(f"{index_name}: 无既有 instruments，已用今日 {len(today_set)} 只初始化 → {path}")
        return {
            "added": sorted(today_set),
            "removed": [],
            "updated": True,
            "path": str(path),
            "count": len(today_set),
        }

    active_mask = (df["start_date"] <= today_str) & (df["end_date"] > today_str)
    active = set(df.loc[active_mask, "symbol"])
    added = sorted(today_set - active)
    removed = sorted(active - today_set)

    if not added and not removed:
        logger.info(f"{index_name}: 成分无变化（{len(today_set)} 只）")
        return {
            "added": [],
            "removed": [],
            "updated": False,
            "path": str(path),
            "count": len(today_set),
        }

    # 关闭调出区间
    if removed:
        close_mask = active_mask & df["symbol"].isin(removed)
        df.loc[close_mask, "end_date"] = today_str

    # 追加调入
    if added:
        new_rows = pd.DataFrame(
            [{"symbol": s, "start_date": today_str, "end_date": OPEN_END} for s in added]
        )
        df = pd.concat([df, new_rows], ignore_index=True)

    df = df.sort_values(["symbol", "start_date"]).reset_index(drop=True)
    _write_instruments(df, path)
    logger.info(
        f"{index_name}: 成分变动 +{len(added)} -{len(removed)} → {path}"
    )
    return {
        "added": added,
        "removed": removed,
        "updated": True,
        "path": str(path),
        "count": len(today_set),
    }


def _get_index_stocks_with_fallback(jq, jq_code: str, preferred_date: str) -> tuple[str, list]:
    """
    拉取成分；若因权限日期失败，自动回退到账号可查的最后一天。

    Returns
    -------
    (actual_date, stocks)
    """
    try:
        stocks = jq.get_index_stocks(jq_code, date=preferred_date)
        return preferred_date, stocks
    except Exception as e:
        msg = str(e)
        # 典型：您的账号权限仅能获取2025-04-02至2026-04-09的数据
        import re

        m = re.search(r"(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})", msg)
        if not m:
            raise
        start_perm, end_perm = m.group(1), m.group(2)
        if preferred_date <= end_perm:
            raise
        logger.warning(
            f"{jq_code}: 请求日 {preferred_date} 超出权限 [{start_perm},{end_perm}]，"
            f"回退到 {end_perm}"
        )
        stocks = jq.get_index_stocks(jq_code, date=end_perm)
        return end_perm, stocks


def update_today(
    index_names: list[str] = cfg.ALL_INDEX_NAMES,
    rebuild_instruments: bool = True,
    output_suffix: str = "_jq",
    write_csi2000: bool = True,
) -> dict[str, dict]:
    """
    拉取今日成分股。

    Parameters
    ----------
    write_csi2000 : bool
        True 时把 csi2000 差分写入正式 instruments/csi2000.txt。
        其余指数仍可按 output_suffix 重建旁路文件（默认 _jq，供校验）。
    """
    jq = _get_jq()
    today_str = str(date.today())
    results: dict[str, dict] = {}

    for name in index_names:
        meta = cfg.INDEX_META[name]
        result: dict = {
            "added": [],
            "removed": [],
            "updated": False,
            "today_set": set(),
            "query_date": today_str,
            "error": None,
        }

        try:
            query_date, today_stocks_jq = _get_index_stocks_with_fallback(
                jq, meta["jq_code"], today_str
            )
        except Exception as e:
            logger.error(f"{name}: get_index_stocks 失败: {e}")
            result["error"] = str(e)
            results[name] = result
            continue

        result["query_date"] = query_date
        today_set = {cfg.jq_to_qlib(s) for s in today_stocks_jq}
        result["today_set"] = today_set

        snapshots = load_snapshots(name)
        last_date = snapshots["date"].max() if not snapshots.empty else None
        if last_date == query_date:
            logger.info(f"{name}: {query_date} 快照已是最新，跳过追加。")
        else:
            new_rows = [{"date": query_date, "symbol": s} for s in sorted(today_set)]
            _append_snapshots(name, new_rows)
            _save_progress(name, query_date, 0)

            if not snapshots.empty and last_date is not None:
                last_set = set(snapshots.loc[snapshots["date"] == last_date, "symbol"])
                result["added"] = sorted(today_set - last_set)
                result["removed"] = sorted(last_set - today_set)
                result["updated"] = bool(result["added"] or result["removed"])
            else:
                result["updated"] = True
                result["added"] = sorted(today_set)

            if result["updated"]:
                logger.info(
                    f"{name}: 快照变动({query_date}) +{len(result['added'])} -{len(result['removed'])}"
                )
            else:
                logger.info(f"{name}: 成分无变化（{len(today_set)} 只，date={query_date}）。")

        # csi2000 → 正式 instruments（用实际查询日做差分）
        if write_csi2000 and name in CSI2000_WRITE_INDICES:
            diff = apply_today_diff_to_instruments(name, today_set, query_date)
            result.update({k: diff[k] for k in ("added", "removed", "updated") if k in diff})
            result["installed"] = diff.get("path")

        # 旁路 _jq 文件（可选，便于人工对比）
        elif rebuild_instruments and output_suffix:
            try:
                build(name, output_suffix=output_suffix)
            except Exception as e:
                logger.warning(f"{name}: 旁路 build 失败: {e}")

        results[name] = result

    _print_summary(today_str, results)
    return results


def crosscheck_with_csindex(
    jq_results: dict[str, dict],
    csindex_members: dict[str, set[str]],
) -> list[dict]:
    """对比 csindex_v2 安装结果与聚宽今日成分，返回差异列表。"""
    diffs: list[dict] = []
    for name in CROSSCHECK_INDICES:
        jq_set = jq_results.get(name, {}).get("today_set") or set()
        cs_set = csindex_members.get(name) or set()
        if not jq_set or not cs_set:
            logger.warning(f"crosscheck {name}: 缺少一侧数据 jq={len(jq_set)} cs={len(cs_set)}")
            continue
        only_jq = sorted(jq_set - cs_set)
        only_cs = sorted(cs_set - jq_set)
        if only_jq or only_cs:
            logger.warning(
                f"crosscheck {name}: 不一致 仅聚宽={len(only_jq)} 仅公告={len(only_cs)}"
            )
            diffs.append(
                {
                    "index": name,
                    "only_jq": only_jq,
                    "only_csindex": only_cs,
                    "jq_count": len(jq_set),
                    "cs_count": len(cs_set),
                }
            )
        else:
            logger.info(f"crosscheck {name}: 一致（{len(jq_set)} 只）")
    return diffs


def _print_summary(today_str: str, results: dict[str, dict]) -> None:
    any_change = any(r.get("updated") for r in results.values())
    errors = {k: r["error"] for k, r in results.items() if r.get("error")}
    if errors:
        logger.error(f"[{today_str}] JQ 拉取失败: {errors}")
    if not any_change:
        logger.info(f"[{today_str}] 今日四个指数成分均无变化（或仅刷新快照）。")
        return
    logger.info(f"[{today_str}] 成分变动摘要：")
    for name, r in results.items():
        if r.get("updated"):
            logger.info(f"  {name}: +{len(r.get('added', []))} -{len(r.get('removed', []))}")
