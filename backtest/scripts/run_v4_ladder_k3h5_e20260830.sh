#!/bin/bash
# v4 官方合成信号 × 真阶梯 k3h5，窗延长到 2026-08-30。
# 假设：延长官方窗约一个月，看全期/2026 年化与回撤相对 BT v4（截止 2026-07-31）怎么变。
# 只作诊断，不晋升、不改写官方窗或官方 JSON。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt/eval_m0fast.yaml
PRED_DIR=backtest/result/phase_s_regime/preds_e20260830
LOG=backtest/result/m0h20rankices_ladder_k3h5_e20260830.log
END=2026-08-30
SEEDS=(42 1000 2000 3000 4000)
mkdir -p "$PRED_DIR"

echo "===== V4-LADDER-K3H5-E20260830-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

SESSIONS=()
for s in "${SEEDS[@]}"; do
  SESSIONS+=("regimeadaptfast_m0h20_rankices_s${s}:${s}")
done

if [[ ! -f "$PRED_DIR/m0h20rankices_ensemble_pred.pkl" ]]; then
  echo "DUMP-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/dump_regime_preds.py \
    --config "$CFG" \
    --sessions "${SESSIONS[@]}" \
    --pool all --segment test \
    --end-time "$END" \
    --out-dir "$PRED_DIR" \
    --ensemble-name m0h20rankices_ensemble_pred.pkl | tee -a "$LOG"
  echo "DUMP-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"
else
  echo "DUMP-SKIP" | tee -a "$LOG"
fi

echo "BT-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/run_regime_phase_s.py \
  --arms m0h20rankices --pool all --strategy ladder_k3h5 \
  --account 1000000 --universe-filter --ensemble-only --generate-figures \
  --no-skip-existing \
  --end-time "$END" \
  --result-suffix e20260830 \
  --pred-dir "$PRED_DIR" | tee -a "$LOG"
echo "BT-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"

echo "===== V4-LADDER-K3H5-E20260830-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
