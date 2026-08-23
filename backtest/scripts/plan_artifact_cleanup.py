"""列出 mlruns/ 与 backtest/result/ 里可清理的产物（只报告，不删除）。

规范 6.3：实验结束后只保留当前 baseline 与最佳合格候选的五种子产物。

保留白名单是**显式**的（不靠"被任意 JSON 提到过"，否则归档侧翼臂会被误判为保留）：
1. Phase M v1 模型基线 v2（M0 H20 ES）与历史 v1（M0 H20）的五种子训练 session
   —— 冻结模型就在 `result/regimeadaptfast_*/run_01/artifacts_root/artifacts/trained_model`，
   删了就没法再复现预测，务必保留
2. 执行层 BT v2（m0h20es top5d1）与历史 BT v1（m0h20 top5d1）当前那一轮的五种子 + ensemble
   —— 只认当前 `all_top5d1/m0h20.json` / `m0h20es.json` 里写的 session，
   修复前 / 旧 ST 的历史 session 不保留（结论已存进这两份 JSON 与 LESSONS）
3. CSI1000 当前 Phase S 基线 B4-S 的带图 session
4. LESSONS 引用的 t25d5h5 诊断 session

用法（只看清单）：
    python backtest/scripts/plan_artifact_cleanup.py
生成删除脚本（仍不执行）：
    python backtest/scripts/plan_artifact_cleanup.py --emit-script /tmp/cleanup.sh
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional

EXP_ROOT = Path(__file__).resolve().parents[2]
MLRUNS = EXP_ROOT / "mlruns"
RESULT = EXP_ROOT / "backtest" / "result"
TOP5D1 = RESULT / "phase_s_regime" / "all_top5d1"

# 当前/历史模型基线的五种子训练 session（v4 RankIC ES + 历史 v3/v2/v1）
KEEP_TRAIN = tuple(
    f"regimeadaptfast_m0h20{suffix}_s{seed}"
    for suffix in ("_rankices", "_t3h5es", "_t5h5es", "")
    for seed in (42, 1000, 2000, 3000, 4000)
)
# CSI1000 当前 Phase S 基线带图 session + LESSONS 引用的诊断
KEEP_EXTRA_SESSIONS = (
    "20260808_205609_baseline_b4s_topk-t22-d2-h2_figures",
    "20260820_001212_diag_m0h20es_all_t25d5h5_ensemble",
    "20260820_001330_diag_m0h20_all_t25d5h5_ensemble",
)
CURRENT_BT_JSONS = ("m0h20.json", "m0h20es.json")


def sizes_kb(parent: Path) -> dict[str, int]:
    dirs = [str(p) for p in sorted(parent.iterdir()) if p.is_dir()] if parent.is_dir() else []
    if not dirs:
        return {}
    out = subprocess.run(["du", "-sk", *dirs], capture_output=True, text=True).stdout
    res: dict[str, int] = {}
    for line in out.splitlines():
        kb, _, path = line.partition("\t")
        if path:
            res[Path(path).name] = int(kb)
    return res


def experiment_names() -> dict[str, str]:
    names = {}
    for meta in MLRUNS.glob("*/meta.yaml"):
        m = re.search(r"^name:\s*(.+)$", meta.read_text(errors="ignore"), re.M)
        names[meta.parent.name] = m.group(1).strip() if m else "?"
    return names


def keep_sessions() -> set[str]:
    """当前 BT 两条线引用的 result 会话名 + 显式白名单 + 两个基线的训练 session。"""
    keep = set(KEEP_EXTRA_SESSIONS) | set(KEEP_TRAIN)
    for fname in CURRENT_BT_JSONS:
        path = TOP5D1 / fname
        if not path.is_file():
            continue
        doc = json.loads(path.read_text())
        blocks = list(doc.get("seeds", {}).values()) + [doc.get("ensemble") or {}]
        for blk in blocks:
            sd = blk.get("session_dir") or ""
            if sd:
                keep.add(Path(sd).name)

    # 评估产物目录（eval_*）体积极小，却是所有报告的数据源（registry.eval_output），
    # 删掉报告就无法再生成。连同 registry 里显式引用到的 result 子目录一起保留。
    if RESULT.is_dir():
        keep |= {p.name for p in RESULT.iterdir() if p.is_dir() and p.name.startswith("eval_")}
    reg = EXP_ROOT / "backtest" / "experiments" / "registry.jsonl"
    if reg.is_file():
        for line in reg.read_text(errors="ignore").splitlines():
            if not line.strip():
                continue
            hits = re.findall(r"backtest/result/([A-Za-z0-9_\-]+)", line)
            # baseline 行（含 CSI1000 B6-M / B4-S）引用到的一律保留，体积很小；
            # 其余行只保留非带时间戳的评估产物目录。
            is_baseline = '"exp_id": "baseline/' in line or '"exp_id":"baseline/' in line
            for m in hits:
                if not (RESULT / m).is_dir():
                    continue
                if is_baseline or not re.match(r"^\d{8}_\d{6}_", m):
                    keep.add(m)
    return keep


def keep_mlruns_ids(names: dict[str, str], sessions: set[str]) -> set[str]:
    """会话 run_01/mlruns_link.json 指向的实验 + 训练 session 白名单。"""
    ids: set[str] = set()
    for session in sessions:
        link = RESULT / session / "run_01" / "mlruns_link.json"
        if link.is_file():
            ids.update(re.findall(r"mlruns/(\d{6,})", link.read_text(errors="ignore")))
    for exp_id, name in names.items():
        stem = re.sub(r"^(backtest|train)_", "", name)
        stem = re.sub(r"_run\d+$", "", stem)
        stem = re.sub(r"^\d{8}_\d{6}_", "", stem)
        if stem in KEEP_TRAIN:
            ids.add(exp_id)
    return ids


def category(name: str) -> str:
    if "strategy_neighborhood" in name:
        return "CSI1000 策略邻域网格（已收敛到 B4-S）"
    if "strategy_stability" in name:
        return "CSI1000 策略稳定性网格"
    if "strategy_sweep" in name or "strategy_beta_overlay" in name:
        return "CSI1000 策略 sweep / beta overlay"
    if "phase_s_" in name:
        return "Phase S 全A 侧翼回测（归档臂 / 修复前 top5d1）"
    if "regimeadaptfast" in name:
        return "regime 训练 session（归档臂）"
    if name.startswith("train_"):
        return "旧训练 run"
    return "其他历史实验"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-script", type=Path, default=None)
    args = ap.parse_args()

    names = experiment_names()
    ml_sizes = sizes_kb(MLRUNS)
    sessions = keep_sessions()
    keep_ids = keep_mlruns_ids(names, sessions)

    ml_groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    drop_ids: list[str] = []
    keep_kb = 0
    for exp_id, name in names.items():
        kb = ml_sizes.get(exp_id, 0)
        if exp_id in keep_ids:
            keep_kb += kb
            continue
        ml_groups[category(name)].append((exp_id, kb))
        drop_ids.append(exp_id)

    total = sum(ml_sizes.values())
    drop_kb = total - keep_kb
    print(f"mlruns  实验 {len(names)}  合计 {total/1048576:.2f} GB")
    print(f"  保留 {len(keep_ids)} 个 / {keep_kb/1048576:.2f} GB")
    print(f"  可删 {len(drop_ids)} 个 / {drop_kb/1048576:.2f} GB")
    for cat, items in sorted(ml_groups.items(), key=lambda kv: -sum(k for _, k in kv[1])):
        print(f"    {sum(k for _, k in items)/1048576:7.2f} GB  n={len(items):4d}  {cat}")

    rs = sizes_kb(RESULT)
    r_keep = {n for n in rs if n in sessions or n == "phase_s_regime"}
    r_drop = {n: kb for n, kb in rs.items() if n not in r_keep}
    print(
        f"\nbacktest/result  会话 {len(rs)}  合计 {sum(rs.values())/1048576:.2f} GB\n"
        f"  保留 {len(r_keep)} 个 / {sum(rs[n] for n in r_keep)/1048576:.2f} GB\n"
        f"  可删 {len(r_drop)} 个 / {sum(r_drop.values())/1048576:.2f} GB"
    )
    r_groups: dict[str, int] = defaultdict(int)
    for n, kb in r_drop.items():
        r_groups[category(n)] += kb
    for cat, kb in sorted(r_groups.items(), key=lambda kv: -kv[1]):
        print(f"    {kb/1048576:7.2f} GB  {cat}")

    print("\n保留明细：")
    for n in sorted(r_keep):
        print(f"  result/{n}")

    if args.emit_script:
        lines = ["#!/bin/sh", "set -eu", f"cd {EXP_ROOT}", ""]
        lines += [f"rm -rf mlruns/{i}" for i in sorted(drop_ids)]
        lines += [f"rm -rf backtest/result/{n}" for n in sorted(r_drop)]
        args.emit_script.write_text("\n".join(lines) + "\n")
        print(f"\n已写出删除脚本（未执行）：{args.emit_script}  共 {len(drop_ids)+len(r_drop)} 条")


if __name__ == "__main__":
    main()
