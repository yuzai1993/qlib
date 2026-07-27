#!/bin/zsh
# Evaluate model-arch-nn/tra on the 3 default test pools (1d + self labels).
set -eu
PY=/opt/anaconda3/envs/qlib/bin/python
export MPLCONFIGDIR=/tmp/mpl
cd "$(dirname "$0")/../.."

SEEDS=(42 1000 2000 3000 4000)
prefix=ma_tra
sessions=()
for seed in "${SEEDS[@]}"; do
  dir=$(ls -d backtest/result/*_${prefix}_s${seed} 2>/dev/null | tail -1)
  if [[ -z "$dir" ]]; then
    echo "MISSING session for ${prefix}_s${seed}" >&2
    exit 1
  fi
  sessions+="$(basename "$dir"):${seed}"
done

echo "===== EVAL tra (1d) ====="
"$PY" backtest/scripts/eval_ic_multi_pool.py \
  --config "model-arch-nn/tra/${prefix}_s42.yaml" \
  --sessions "${sessions[@]}" \
  --pools csi300 csi500 csi1000 \
  --output "backtest/experiments/ic/${prefix}_test_1d.json"

echo "===== EVAL tra (self) ====="
"$PY" backtest/scripts/eval_ic_multi_pool.py \
  --config "model-arch-nn/tra/${prefix}_s42.yaml" \
  --sessions "${sessions[@]}" \
  --pools csi300 csi500 csi1000 \
  --eval-label 'Ref($close, -41)/Ref($close, -1)-1' \
  --eval-label-role self \
  --output "backtest/experiments/ic/${prefix}_test_self.json"

echo "===== EVAL TRA DONE ====="
