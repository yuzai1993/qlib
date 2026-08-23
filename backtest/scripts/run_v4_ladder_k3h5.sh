#!/bin/bash
# v4 官方合成信号 × 真阶梯 k3h5。只回测一次，不晋升执行层基线。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt/eval_m0fast.yaml
PRED_DIR=backtest/result/phase_s_regime/preds
LOG=backtest/result/m0h20rankices_ladder_k3h5.log
SEEDS=(42 1000 2000 3000 4000)
mkdir -p "$PRED_DIR"

echo "===== V4-LADDER-K3H5-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

SESSIONS=()
for s in "${SEEDS[@]}"; do
  SESSIONS+=("regimeadaptfast_m0h20_rankices_s${s}:${s}")
done

if [[ ! -f "$PRED_DIR/m0h20rankices_ensemble_pred.pkl" ]]; then
  echo "DUMP-V4-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/dump_regime_preds.py \
    --config "$CFG" \
    --sessions "${SESSIONS[@]}" \
    --pool all --segment test \
    --out-dir "$PRED_DIR" \
    --ensemble-name m0h20rankices_ensemble_pred.pkl | tee -a "$LOG"
  echo "DUMP-V4-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"
else
  echo "DUMP-V4-SKIP" | tee -a "$LOG"
fi

echo "BT-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/run_regime_phase_s.py \
  --arms m0h20rankices --pool all --strategy ladder_k3h5 \
  --account 1000000 --universe-filter --ensemble-only --generate-figures \
  --no-skip-existing | tee -a "$LOG"
echo "BT-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"

echo "===== V4-LADDER-K3H5-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
