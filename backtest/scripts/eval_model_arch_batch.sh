#!/bin/zsh
# Evaluate model-arch variants on the 3 default test pools (1d + self labels).
set -eu
PY=/opt/anaconda3/envs/qlib/bin/python
export MPLCONFIGDIR=/tmp/mpl
cd "$(dirname "$0")/../.."

typeset -A PREFIX
PREFIX=(xgboost ma_xgboost catboost ma_catboost double-ensemble ma_double_ensemble)
SEEDS=(42 1000 2000 3000 4000)

for variant in xgboost catboost double-ensemble; do
  prefix=${PREFIX[$variant]}
  sessions=()
  for seed in "${SEEDS[@]}"; do
    dir=$(ls -d backtest/result/*_${prefix}_s${seed} 2>/dev/null | tail -1)
    if [[ -z "$dir" ]]; then
      echo "MISSING session for ${prefix}_s${seed}" >&2
      exit 1
    fi
    sessions+="$(basename "$dir"):${seed}"
  done
  echo "===== EVAL $variant (1d) ====="
  "$PY" backtest/scripts/eval_ic_multi_pool.py \
    --config "model-arch/${variant}/${prefix}_s42.yaml" \
    --sessions "${sessions[@]}" \
    --pools csi300 csi500 csi1000 \
    --output "backtest/experiments/ic/${prefix}_test_1d.json"
  echo "===== EVAL $variant (self) ====="
  "$PY" backtest/scripts/eval_ic_multi_pool.py \
    --config "model-arch/${variant}/${prefix}_s42.yaml" \
    --sessions "${sessions[@]}" \
    --pools csi300 csi500 csi1000 \
    --eval-label 'Ref($close, -41)/Ref($close, -1)-1' \
    --eval-label-role self \
    --output "backtest/experiments/ic/${prefix}_test_self.json"
done
echo "===== EVAL ALL DONE ====="
