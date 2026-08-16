#!/bin/bash
# regime-adapt 快筛评估队列：B6-M 参考行 + M0-fast + M3-fast
# 协议（计划 v3 第 4 节）：h1/5/10/20/40，冻结 70% 测试日，四池不分风格 + 全A分风格，tail top22/50
set -uo pipefail
cd /Users/yuxianqi/Project/qlib_exp
PY=/opt/anaconda3/envs/qlib/bin/python
CFG=backtest/configs/regime-adapt
OUT=backtest/result/eval_regime_fast
mkdir -p "$OUT"

COMMON=(--pools csi300 csi500 csi1000 all --segment test
  --horizons 1 5 10 20 40
  --date-list "$CFG/test_dates_stratified_70.csv"
  --regime-labels "$CFG/monthly_regime_labels_eval_window_v1.csv"
  --regime-pools all
  --tail-topk 22 50)

run_one() {
  local name="$1" config="$2"; shift 2
  echo "EVAL-START $name $(date +%m-%d\ %H:%M)"
  if $PY backtest/scripts/eval_ic_multi_pool.py --config "$config" \
      --sessions "$@" "${COMMON[@]}" \
      --output "$OUT/eval_${name}.json" > "$OUT/eval_${name}.log" 2>&1; then
    echo "EVAL-DONE $name $(date +%m-%d\ %H:%M)"
  else
    echo "EVAL-FAILED $name $(date +%m-%d\ %H:%M)"
  fi
}

run_one m0fast "$CFG/eval_m0fast.yaml" \
  20260812_222756_regimeadaptfast_m0_s42:42 \
  20260812_223202_regimeadaptfast_m0_s1000:1000 \
  20260812_223531_regimeadaptfast_m0_s2000:2000 \
  20260812_224058_regimeadaptfast_m0_s3000:3000 \
  20260812_224512_regimeadaptfast_m0_s4000:4000

run_one m3fast "$CFG/eval_m3fast.yaml" \
  20260812_223035_regimeadaptfast_m3_s42:42 \
  20260812_223412_regimeadaptfast_m3_s1000:1000 \
  20260812_223815_regimeadaptfast_m3_s2000:2000 \
  20260812_224338_regimeadaptfast_m3_s3000:3000 \
  20260812_224800_regimeadaptfast_m3_s4000:4000

run_one b6m_ref "$CFG/eval_b6m_ref.yaml" \
  20260731_030732_mh_rankic_es_lr010_s42:42 \
  20260731_032149_mh_rankic_es_lr010_s1000:1000 \
  20260731_033541_mh_rankic_es_lr010_s2000:2000 \
  20260731_034838_mh_rankic_es_lr010_s3000:3000 \
  20260731_040103_mh_rankic_es_lr010_s4000:4000

echo "EVAL-QUEUE-DONE $(date +%m-%d\ %H:%M)"
