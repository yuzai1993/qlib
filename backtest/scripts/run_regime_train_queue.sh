#!/bin/zsh
# regime-adapt 训练队列：跳过已完成的 arm+seed，串行跑完 10 个种子。
# MODEL=single（默认，阶段1 B3-M 单 LGBM 筛选）| densemble（阶段2 确认臂）
# 用独立会话启动以脱离终端存活（setsid 双 fork，见主会话记录）。
cd "$(dirname "$0")/../.." || exit 1
PY=/opt/anaconda3/envs/qlib/bin/python
MODEL="${MODEL:-single}"
if [ "$MODEL" = "single" ]; then TAG=regimeadaptfast; else TAG=regimeadapt; fi
for seed in 42 1000 2000 3000 4000; do
  for arm in m0 m3; do
    if ls backtest/result/*_${TAG}_${arm}_s${seed} >/dev/null 2>&1; then
      echo "SKIP $MODEL $arm s$seed (已完成)"
      continue
    fi
    log="backtest/result/train_${TAG}_${arm}_s${seed}.log"
    echo "START $MODEL $arm s$seed $(date '+%m-%d %H:%M')"
    "$PY" backtest/scripts/train_regime_arm.py --model "$MODEL" --arm "$arm" --seed "$seed" > "$log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
      echo "RUN-FAILED $MODEL $arm s$seed rc=$rc $(date '+%m-%d %H:%M')"
    else
      echo "RUN-DONE $MODEL $arm s$seed $(date '+%m-%d %H:%M')"
      grep -E '^\[fit\]|^\[done\]' "$log"
    fi
  done
done
echo "TRAIN-QUEUE-DONE $(date '+%m-%d %H:%M')"
