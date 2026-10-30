# Qlib Shell：禁止 stdin 多进程陷阱

在 macOS 上，`qlib.data.D.features` / Dataset handler 会通过 joblib `multiprocessing`（spawn）起子进程。子进程需要重新 import 主脚本路径。

## 禁止

不要用 heredoc / stdin 跑会触发 Qlib 并行取数的代码：

```bash
# BAD — 子进程会找 /path/to/project/<stdin> 并刷屏 FileNotFoundError
python - <<'EOF'
import qlib
from qlib.data import D
qlib.init(...)
D.features([...], [...], ...)
EOF
```

同类禁止：`python -c '...'` 里调用 `D.features`、handler `fetch`、大规模 `DatasetH` 准备（只要会走 `kernels>1` 并行）。

## 正确做法（二选一）

1. **写成临时 `.py` 再执行**（推荐，可保留并行）：

```bash
cat > /tmp/qlib_job.py <<'EOF'
import qlib
from qlib.data import D
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")
print(D.features(["SH600000"], ["$close"], start_time="2024-01-01", end_time="2024-01-10"))
EOF
/opt/anaconda3/envs/qlib/bin/python /tmp/qlib_job.py
```

或写到仓库内 `backtest/scripts/_tmp_*.py`，跑完删除。

2. **强制单进程**（短查询可用）：

```python
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn", kernels=1)
```

`kernels=1` 时即使 stdin 也能跑，但大范围取数会变慢。

## 若已刷屏

立刻杀掉对应 shell/python 进程，不要让 spawn 子进程空转占满终端输出。
