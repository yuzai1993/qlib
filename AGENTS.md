# AGENTS.md

## Cursor Cloud specific instructions

Qlib is a pure-Python quantitative-research library (`pyqlib`). There is no long-running
server or GUI; you "run the app" by executing quant workflows/experiments from the terminal
(e.g. `qrun <config>.yaml` or `python qlib/cli/run.py <config>.yaml`) and by running the test
suite. Standard install/lint/test/build commands live in the `Makefile` and `pyproject.toml`
(`[project.optional-dependencies]`); refer to those rather than duplicating them.

The environment snapshot already has all dependencies installed (via `make dev`), the Cython
extensions compiled, and sample data downloaded. The notes below cover only the non-obvious
gotchas discovered during setup.

### Environment gotchas (already applied in the snapshot)

- The `Makefile` invokes `python`, but the base image only ships `python3`. A
  `/usr/bin/python -> python3` symlink is in place. Don't assume a bare `python` on a fresh box.
- System Python is PEP 668 "externally managed". `pip` is configured with
  `global.break-system-packages=true` (in `~/.config/pip/pip.conf`) so `pip install` works. All
  packages install into the user site (`~/.local`), not system site.
- Console scripts (`black`, `flake8`, `pylint`, `mypy`, `nbqa`, `qrun`, `jupyter`) live in
  `~/.local/bin`. That dir is added to `PATH` in `~/.bashrc`. Interactive shells get it; if you
  run a non-login/non-interactive shell, either use `python -m <tool>` or prepend
  `~/.local/bin` to `PATH` yourself.
- `apt` must be forced to IPv4 (`sudo apt-get -o Acquire::ForceIPv4=true ...`); `archive.ubuntu.com`
  only resolves to IPv6 here and hangs otherwise. `security.ubuntu.com` may still be unreachable,
  which is fine.

### Running workflows / experiments

- `MLFLOW_ALLOW_FILE_STORE=true` MUST be set (exported in `~/.bashrc`). The installed mlflow
  (3.x) rejects the filesystem tracking backend that Qlib uses by default; without this env var
  `qrun`/workflows crash with an `MlflowException` about "maintenance mode".
- Hello-world / smoke run (needs the cn sample data below):
  `qrun examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
  It trains LightGBM on Alpha158, reports signal IC/ICIR, and runs a backtest with portfolio
  analysis.

### Data (downloaded into the snapshot; not tracked by git)

Workflows and most tests need data that is NOT in the repo. It is downloaded to
`~/.qlib/qlib_data/cn_data` and `tests/.data/rl`. If missing, re-download:

- Daily cn sample data: `python scripts/get_data.py qlib_data --name qlib_data_simple --target_dir ~/.qlib/qlib_data/cn_data --interval 1d --region cn`
- RL test data: `python scripts/get_data.py download_data --file_name rl_data.zip --target_dir tests/.data/rl`

### Testing

- Run from the `tests/` dir: `cd tests && python -m pytest . -m "not slow"`. The `rl/` tests
  require the RL data above.
- `dependency_tests/test_mlflow.py::test_creating_client` is a timing assertion (client creation
  < 10ms) that is flaky under parallel CPU load; it passes when run in isolation. CI retries the
  suite up to 3x for this reason. Treat an isolated pass as green.
- `make black` may report reformat diffs purely due to the installed Black version being newer
  than the version the repo was formatted with (`.pre-commit-config.yaml` pins Black 23.7.0);
  `flake8` is clean. This is version drift, not a code defect.
