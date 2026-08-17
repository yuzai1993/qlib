#!/bin/bash
# M0 改标签：补训 h=2/3/20，按新口径评估全部 M0 臂，只出 M0 报告。
# 评估：主格 top5×h5 扣费净年化/波动/夏普；网格 5/15/50×2/3/5/10；D/F/T。
# 过滤：ST + 成交额>=1000万 + 上市>=60 + 剔 t+1 涨停。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
trap 'echo M0LABEL-FAILED $(date "+%F %H:%M")' ERR
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_m0_labels
LOG=backtest/result/m0_label_eval.log
mkdir -p "$OUT"

echo "===== M0LABEL-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

echo "----- pytest -----" | tee -a "$LOG"
$PY -m pytest tests/backtest/test_regime_adapt.py tests/backtest/test_eval_ic_multi_pool.py -q --tb=short | tee -a "$LOG"

echo "----- alt labels h2/h3/h20 -----" | tee -a "$LOG"
$PY backtest/scripts/build_regime_alt_labels.py --horizons 2 3 20 | tee -a "$LOG"

SEEDS=(42 1000 2000 3000 4000)
for h in 2 3 20; do
  for s in "${SEEDS[@]}"; do
    sess="regimeadaptfast_m0h${h}_s${s}"
    if [[ -d "backtest/result/${sess}" ]]; then
      echo "TRAIN-SKIP $sess" | tee -a "$LOG"
      continue
    fi
    echo "TRAIN-START $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
    $PY backtest/scripts/train_regime_arm.py --arm m0 --seed "$s" --model single \
      --label-horizon "$h" --session-name "$sess" | tee -a "$LOG"
    echo "TRAIN-DONE $sess $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  done
done

COMMON=(--pools all --segment test
  --horizons 2 3 5 10
  --head-k 5 15 50
  --exclude-limit-up
  --min-listing-days 60
  --min-amount 10000000
  --st-daily scripts/data_collector/tushare/st_daily.csv
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all)

run_eval() {
  local name="$1"; shift
  local json="$OUT/eval_${name}.json"
  if [[ -f "$json" ]]; then
    echo "EVAL-SKIP $name" | tee -a "$LOG"
    return 0
  fi
  echo "EVAL-START $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
  $PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
      --sessions "$@" "${COMMON[@]}" --output "$json" | tee -a "$LOG"
  echo "EVAL-DONE $name $(date '+%m-%d %H:%M')" | tee -a "$LOG"
}

for h in 1 2 3 5 10 20; do
  SESSIONS=()
  for s in "${SEEDS[@]}"; do SESSIONS+=("regimeadaptfast_m0h${h}_s${s}:${s}"); done
  run_eval "m0h${h}" "${SESSIONS[@]}"
done

run_eval m0h40 \
  20260812_222756_regimeadaptfast_m0_s42:42 \
  20260812_223202_regimeadaptfast_m0_s1000:1000 \
  20260812_223531_regimeadaptfast_m0_s2000:2000 \
  20260812_224058_regimeadaptfast_m0_s3000:3000 \
  20260812_224512_regimeadaptfast_m0_s4000:4000

echo "----- report + registry -----" | tee -a "$LOG"
$PY backtest/scripts/build_regime_m0_label_report.py | tee -a "$LOG"
$PY backtest/scripts/register_regime_m0_labels.py | tee -a "$LOG"

echo "===== M0LABEL-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
