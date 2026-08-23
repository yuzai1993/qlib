#!/bin/bash
# 补算 v1–v4 官方合成信号的全局 RankIC。v3 若无 pred 先 dump。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt/eval_m0fast.yaml
PRED_DIR=backtest/result/phase_s_regime/preds
LOG=backtest/result/patch_official_rank_ic.log
SEEDS=(42 1000 2000 3000 4000)
mkdir -p "$PRED_DIR"

echo "===== PATCH-RIC-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

if [[ ! -f "$PRED_DIR/m0h20t3h5es_ensemble_pred.pkl" ]]; then
  SESSIONS=()
  for s in "${SEEDS[@]}"; do
    SESSIONS+=("regimeadaptfast_m0h20_t3h5es_s${s}:${s}")
  done
  echo "DUMP-V3-START $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/dump_regime_preds.py \
    --config "$CFG" \
    --sessions "${SESSIONS[@]}" \
    --pool all --segment test \
    --out-dir "$PRED_DIR" \
    --ensemble-name m0h20t3h5es_ensemble_pred.pkl | tee -a "$LOG"
  echo "DUMP-V3-DONE $(date '+%m-%d %H:%M')" | tee -a "$LOG"
fi

$PY backtest/scripts/patch_official_rank_ic.py --config "$CFG" | tee -a "$LOG"
$PY backtest/scripts/promote_phase_m_v1_rankices.py | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec rankices | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"

echo "===== PATCH-RIC-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
