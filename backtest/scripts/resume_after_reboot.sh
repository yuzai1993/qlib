#!/bin/bash
# 重启后续跑：M0 H20 官方评估已完成，从 M0 H20 ES 接着评；
# 然后重训 DoubleEnsemble 种子 42（上次没写出 session）。
set -euo pipefail
cd /Users/yuxianqi/Project/qlib_exp
export MLFLOW_ALLOW_FILE_STORE=true
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
LOG=backtest/result/resume_after_reboot.log

echo "===== RESUME-START $(date '+%F %H:%M') =====" | tee -a "$LOG"

echo "EVAL-START m0h20es $(date '+%m-%d %H:%M')" | tee -a "$LOG"
$PY backtest/scripts/eval_ic_multi_pool.py --config "$CFG/eval_m0fast.yaml" \
    --sessions \
      regimeadaptfast_m0h20_t5h5es_s42:42 \
      regimeadaptfast_m0h20_t5h5es_s1000:1000 \
      regimeadaptfast_m0h20_t5h5es_s2000:2000 \
      regimeadaptfast_m0h20_t5h5es_s3000:3000 \
      regimeadaptfast_m0h20_t5h5es_s4000:4000 \
    --pools all --segment test \
    --horizons 2 3 5 10 \
    --head-k 5 15 50 \
    --exclude-limit-up \
    --min-listing-days 60 \
    --min-amount 10000000 \
    --st-daily scripts/data_collector/tushare/st_daily.csv \
    --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv" \
    --regime-pools all \
    --output backtest/result/eval_regime_m0_t5h5es/eval_m0h20.json | tee -a "$LOG"
echo "EVAL-DONE m0h20es $(date '+%m-%d %H:%M')" | tee -a "$LOG"

$PY backtest/scripts/register_regime_m0_labels.py --refresh | tee -a "$LOG"
$PY backtest/scripts/register_regime_ablation.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/build_regime_phase_m_detail_report.py --spec t5h5es | tee -a "$LOG"
$PY backtest/scripts/build_phase_m_v1_report.py | tee -a "$LOG"
echo "===== OFFICIAL-EVAL-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
echo "===== RESUME-DONE $(date '+%F %H:%M') =====" | tee -a "$LOG"
