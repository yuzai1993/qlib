# 实盘 v4 真阶梯 · 计划四：切换手册

> **For agentic workers:** 本计划**不是**普通功能开发。任务 1–3 可在本会话用
> superpowers:executing-plans 批量落地；任务 4 起每一笔真金白银或现网调度变更都必须
> **停下来等用户点名确认当日交易日与动作**。禁止对任务 4–11 派发无人值守 subagent。
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已经在计划一至三落地的「全A + v4 五种子 + 真阶梯 k3×h5 + 盘后固定价格 +
抵销」切成**唯一**活动实盘系统，替换 `csi1000_b6m_b2s_postclose_real`，并在建仓期
用观测与回退条件兜住成交率风险。

**Architecture:** 先修一处现网对不上的授权几何（盘后实例要坐到主 root，
`PR49_LIVE_OK_` 也要跟过去），再按交易日做：资格/终态探测 → 旧通道清零星一手 →
停探针、装新运行时 → 新账本按券商现金起账 → 切 cron。文档只在切换成为事实之后改写。

**Tech Stack:** 现有实盘栈（Mac cron / launchd、Windows QMT 内置 Python 3.6、SMB
`/Volumes/qmt_bridge` ↔ `D:\qmt_bridge`、SQLite 账本、pytest）。不新增第三方依赖。

## Global Constraints

- **禁止**创建、删除、改写任何授权 marker（`LIVE_OK_*` / `PR49_LIVE_OK_*` / `.intent.*`）。
  marker 是不可逆授权事实；回退只能停实例、留证据。
- **禁止**在用户未点名当日交易日的情况下跑 `LIVE_TRADING_CONFIRM=YES` 的发布。
- **禁止**对任务 4–8 使用 `--publish`、PowerShell 授权脚本、QMT `passorder`，除非用户
  当条消息明确写出交易日和动作。
- 账户号、`TUSHARE_TOKEN`、`QMT_REAL_ACCOUNT_ID` 不得写入 Git、计划正文或提交信息。
- 本计划**不改回测**、不重跑 BT v4、不改 CSI1000 研究轨道定义。
- 授权前缀**不改名**：生产盘后授权仍叫 `PR49_LIVE_OK_`（spec 第 5 节「不重命名」）。
  变的是**文件落在哪个 state 目录**。
- 旧账本 `live_trading/data/csi1000_b6m_b2s_postclose_real.db` **冻结作历史**，不迁移
  持仓、不迁移现金。新账本是
  `live_trading/data/alla_v4_ladder_k3h5_postclose_real.db`。
- 在新账本第一次被 `LiveRecorder` 碰到之前，`alla_v4_ladder_k3h5_postclose_real.yaml`
  的 `opening_cash` 必须已经改成券商可用资金。占位值 `1000000.0` 被写入后无法再 seed
  （`opening_cash cannot seed an already-used live ledger`）。
- 任何会构造 `LiveRecorder(新配置)` 的脚本（`run_publish_signals` / `run_import_fills` /
  `run_monitor` / `request_account_snapshot`）在任务 8 完成前都**不准**拿新配置跑。
- 运行测试固定用 `/opt/anaconda3/envs/qlib/bin/python -m pytest`。
- 每个任务独立提交；提交信息英文，`fix:` / `feat:` / `docs:`。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。

## 写计划时核对过、实施时不必重查的事实

| 事实 | 位置 |
|---|---|
| 盘后实例的 `_authorization_path` 是 `{BRIDGE_ROOT}/state/{prefix}{date}` | `qmt_signal_bridge.py:629-631` |
| `_validate_profile_roots` **强制** `AFTER_HOURS` 的 `BRIDGE_ROOT` 必须是 `{other}/pr49_probe` | `qmt_signal_bridge.py:558-574` |
| 因此「主实例改编译为盘后、`BRIDGE_ROOT=D:\qmt_bridge`」在当前代码下会在 `init` 直接炸 | 同上 |
| PS1 把 `AFTER_HOURS` 的 own marker 写到 `D:\qmt_bridge\pr49_probe\state\PR49_LIVE_OK_` | `New-OperatorAuthorizationMarker.ps1:117-120` |
| 主实例若坐在 `D:\qmt_bridge`，它找的是 `D:\qmt_bridge\state\PR49_LIVE_OK_` | 两处对不上，**不改必切失败** |
| 快照采集根写死：`AFTER_HOURS → D:\qmt_bridge\pr49_probe` | `operator_probe.QMT_PROFILE_BRIDGE_ROOTS` |
| `LiveRecorder` 只在库空且无 batches/fills/positions 时写入 `opening_cash` | `fill_importer.py:85-112` |
| 新配置 `opening_cash` 现为占位 `1000000.0` | `alla_v4_ladder_k3h5_postclose_real.yaml:12` |
| 监控页 `_profile_name` 对非探针策略写死 `CLOSE_AUCTION` | `web/api.py:46-49` |
| wrapper / crontab / launchd 默认仍是 `csi1000_b6m_b2s_postclose_real` | `run_*_cron.sh`、`crontab.csi1000_postclose.example`、plist |
| `report` 在 `update` 失败时整段跳过；成交率与收盘价对账那天不会跑 | 计划三已知缺口 |

## 文件地图

| 文件 | 职责 |
|---|---|
| `live_trading/qmt_strategy/qmt_signal_bridge.py` | 允许盘后实例坐主 root；双侧都查 sibling 前缀 |
| `live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1` | 两个前缀都落在主 `state/` |
| `live_trading/modules/operator_probe.py` | `AFTER_HOURS` 快照采集根改到主 root |
| `live_trading/web/api.py` | 执行通道从配置读，不再写死集合竞价 |
| `live_trading/qmt_strategy/qmt_observe_security.py` | 只观测、下不了单的 QMT 脚本 |
| `live_trading/scripts/cutover_preflight.py` | 切换日 Mac 侧只读预检（不碰新账本） |
| `live_trading/run_*_cron.sh`、crontab 示例、launchd plist | 默认 config id |
| `alla_v4_ladder_k3h5_postclose_real.yaml` | 只在起账那一刻改 `opening_cash` |
| `live_trading/README.md` 等 | 切换成为事实后改写 |

## 交易日历（不要在同一天把清仓和盘后首单叠在一起）

| 日 | 做什么 | 对应任务 |
|---|---|---|
| T-n（本会话即可） | 授权几何、监控页、观测脚本、预检脚本 | 1–3 |
| T-2 交易日盘中 | 抽查 10 只票的盘后资格字段 | 4 |
| T-1 交易日 14:59–15:01 | 探测 `timetag` 是否可用 | 5 |
| T-1 交易日 14:57 通道 | 旧配置清空零星一手（若已空仓则跳过） | 6 |
| T 日 15:31 之后、次日 16:00 之前 | 停旧实例、装新运行时、起账、切 cron、改文档 | 7–10 |
| T+1 起连续 5 个交易日 | 建仓期人工核对 | 11 |

T 日不要发真阶梯单：那天只改管路。第一笔 `PR49_LIVE_OK_` 出现在 T+1。

---

### Task 1: 盘后授权跟实例走，不再锁在 pr49_probe

不改这一处，spec 第 5 步「主实例改编译为 `AFTER_HOURS_FIXED_PRICE`」会在 `init` 被
`_validate_profile_roots` 拒绝；即便绕过，PS1 写下的 marker 也不在实例读取的目录。
前缀名字保持 `PR49_LIVE_OK_`。

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:558-574`、`:643-644`
- Modify: `live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1:108-125`
- Modify: `live_trading/modules/operator_probe.py:51-54`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`
- Test: `tests/live_trading/test_operator_probe.py`
- Test: `tests/live_trading/test_repository_boundaries.py`

**Interfaces:**
- Consumes: 现有 `_canonical_bridge_root` / `_authorization_path` / `_other_authorization_path`
- Produces: `_validate_profile_roots()` 接受两种合法配对（嵌套探针 **或** 父 root +
  嵌套 sibling）；`_other_profile_authorized(trade_date)` 在 **两个** state 目录都查
  sibling 前缀；`QMT_PROFILE_BRIDGE_ROOTS["AFTER_HOURS_FIXED_PRICE"] == r"D:\\qmt_bridge"`

- [ ] **Step 1: 写失败测试——盘后实例可以坐在主 root**

在 `tests/live_trading/test_qmt_bridge_logic.py` 追加：

```python
def test_after_hours_may_sit_on_the_main_root(tmp_path, bridge):
    main_root = tmp_path / "main"
    probe_root = main_root / "pr49_probe"
    main_root.mkdir()
    probe_root.mkdir()
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", main_root, probe_root,
    )
    assert bridge._authorization_path("2026-08-25").endswith(
        os.path.join("main", "state", "PR49_LIVE_OK_2026-08-25")
    )


def test_a_close_auction_marker_on_the_same_root_blocks_after_hours(
    tmp_path, bridge,
):
    main_root = tmp_path / "main"
    probe_root = main_root / "pr49_probe"
    main_root.mkdir()
    probe_root.mkdir()
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", main_root, probe_root,
    )
    marker = main_root / "state" / "LIVE_OK_2026-08-25"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("authorized\n", encoding="ascii")
    assert bridge._other_profile_authorized("2026-08-25") is True


def test_legacy_after_hours_on_the_nested_probe_root_still_boots(
    tmp_path, bridge,
):
    """计划三之前的单测配对必须继续能 init，避免一次切两套几何。"""
    current, other = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", current, other,
    )
```

- [ ] **Step 2: 跑测试，确认前两个失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py::test_after_hours_may_sit_on_the_main_root tests/live_trading/test_qmt_bridge_logic.py::test_a_close_auction_marker_on_the_same_root_blocks_after_hours -v`

Expected: FAIL。第一条应是 `profile roots must be an exact main/pr49_probe direct pair`。

- [ ] **Step 3: 放宽 root 配对，并让 sibling 前缀在两侧都查**

把 `qmt_signal_bridge.py` 的 `_validate_profile_roots` 整段换成：

```python
def _validate_profile_roots():
    current_root = _canonical_bridge_root(BRIDGE_ROOT)
    other_root = _canonical_bridge_root(OTHER_BRIDGE_ROOT)
    nested_from_current = os.path.normcase(
        os.path.join(current_root, "pr49_probe")
    )
    nested_from_other = os.path.normcase(
        os.path.join(other_root, "pr49_probe")
    )
    pair_nested = current_root == nested_from_other
    pair_parent = other_root == nested_from_current
    if not (pair_nested or pair_parent):
        raise ValueError(
            "profile roots must be an exact main/pr49_probe direct pair"
        )
```

把 `_other_profile_authorized` 换成：

```python
def _other_profile_authorized(trade_date):
    prefix = _profile_settings()["other_authorization_prefix"]
    names = (prefix + trade_date,)
    candidates = [
        os.path.join(OTHER_BRIDGE_ROOT, "state", name) for name in names
    ] + [
        os.path.join(BRIDGE_ROOT, "state", name) for name in names
    ]
    return any(os.path.isfile(path) for path in candidates)
```

- [ ] **Step 4: PS1 两个前缀都落在主 state/**

`New-OperatorAuthorizationMarker.ps1` 第 108–125 行改成：

```powershell
  if ($Profile -eq "CLOSE_AUCTION") {
    $CutoffText = "$TradeDate 14:57:05"
    $OwnMarker = [System.IO.Path]::Combine(
      $StateRoot, "LIVE_OK_$TradeDate"
    )
    $OtherMarker = [System.IO.Path]::Combine(
      $StateRoot, "PR49_LIVE_OK_$TradeDate"
    )
  }
  else {
    $CutoffText = "$TradeDate 15:05:00"
    $OwnMarker = [System.IO.Path]::Combine(
      $StateRoot, "PR49_LIVE_OK_$TradeDate"
    )
    $OtherMarker = [System.IO.Path]::Combine(
      $StateRoot, "LIVE_OK_$TradeDate"
    )
  }
```

在 `test_repository_boundaries.py` 的 `test_windows_marker_creator_uses_shared_lock_and_rechecks_inside_it` 末尾追加（不要删现有断言）：

```python
    assert 'Combine(\n      $StateRoot, "PR49_LIVE_OK_$TradeDate"' in text
    assert 'Combine(\n      $BridgeRoot, "pr49_probe", "state", "PR49_LIVE_OK_$TradeDate"' not in text
```

PowerShell 原文是多行 `Combine(`，用文件里的真实空白对齐；若黑判断太脆，改成：

```python
    assert '$StateRoot, "PR49_LIVE_OK_$TradeDate"' in text
    assert text.count("PR49_LIVE_OK_$TradeDate") == 2  # own + other 各一次
    assert 'pr49_probe", "state", "PR49_LIVE_OK_' not in text
```

- [ ] **Step 5: 快照采集根改到主 root**

`operator_probe.py`：

```python
QMT_PROFILE_BRIDGE_ROOTS = {
    "CLOSE_AUCTION": r"D:\qmt_bridge",
    "AFTER_HOURS_FIXED_PRICE": r"D:\qmt_bridge",
}
```

跑 `tests/live_trading/test_operator_probe.py`，把所有断言
`collector_bridge_root` 以 `pr49_probe` 结尾、且对象是 **AFTER_HOURS 快照**（不是
`OPERATOR_PROBE` 下单 inbox）的期望改成 `D:\qmt_bridge`。探针策略自己的
`bridge_root: .../pr49_probe` **不要动**——那是下单根，不是快照采集根。

`test_main_sell_publish_rejects_any_same_day_authorization_marker` 的三条相对路径
保持原样（主 state 与嵌套 state 都拒，失败关闭）。

- [ ] **Step 6: 跑相关测试并提交**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py \
  tests/live_trading/test_operator_probe.py \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_fill_importer.py -q
```

Expected: PASS。

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1 \
        live_trading/modules/operator_probe.py \
        tests/live_trading/test_qmt_bridge_logic.py \
        tests/live_trading/test_operator_probe.py \
        tests/live_trading/test_repository_boundaries.py \
        tests/live_trading/test_fill_importer.py
git commit -m "fix: let the after-hours instance live on the main bridge root"
```

Windows 上 `D:\qmt_bridge\tools\` 的 PS1 副本**先不要覆盖**——任务 7 装机时再拷。
仓库里的新脚本若被提前拷到 Windows，正在跑的探针会找不到 marker。

---

### Task 2: 监控页按配置显示执行通道

装机后若仍显示 `CLOSE_AUCTION`，建仓期会按错通道核单。

**Files:**
- Modify: `live_trading/web/api.py:46-49`、`:119-121`
- Test: `tests/live_trading/test_monitor_web_api.py`

**Interfaces:**
- Consumes: `config["live"]["execution_session"]`、`config["live"]["strategy_id"]`
- Produces: `_profile_name(strategy_id) -> str`；当前策略用配置值，探针固定
  `AFTER_HOURS_FIXED_PRICE`，历史 CSI1000 主策略在「正在看探针配置」时仍报
  `CLOSE_AUCTION`

- [ ] **Step 1: 写失败测试**

在 `test_monitor_web_api.py` 追加。不要改现有「探针配置下 CSI1000 行是
CLOSE_AUCTION」那条。overview 读执行通道不依赖持仓，空账本即可：

```python
def test_overview_reads_the_execution_session_from_the_loaded_config(tmp_path):
    db = tmp_path / "live.db"
    LiveRecorder(str(db), opening_cash=100_000.0)
    app = create_app({
        "live": {
            "bridge_root": str(tmp_path / "bridge"),
            "strategy_id": "alla_v4_ladder_k3h5_postclose_real",
            "execution_session": "AFTER_HOURS_FIXED_PRICE",
            "default_mode": "LIVE",
        },
        "monitor": {"benchmark_name": "中证全指"},
        "storage": {"db_path": str(db)},
    }, Path("/"))
    data = TestClient(app).get("/api/overview").json()
    assert data["strategy_id"] == "alla_v4_ladder_k3h5_postclose_real"
    assert data["execution_profile"] == "AFTER_HOURS_FIXED_PRICE"
    main_row = next(
        row for row in data["strategy_statuses"]
        if row["strategy_id"] == "alla_v4_ladder_k3h5_postclose_real"
    )
    assert main_row["execution_profile"] == "AFTER_HOURS_FIXED_PRICE"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_monitor_web_api.py::test_overview_reads_the_execution_session_from_the_loaded_config -v`

Expected: FAIL，`execution_profile == "CLOSE_AUCTION"`。

- [ ] **Step 3: 实现**

```python
def _profile_name(strategy_id: str) -> str:
    if strategy_id == PROBE_STRATEGY_ID:
        return PROBE_PROFILE
    current_id = config["live"].get("strategy_id", "")
    if strategy_id == current_id:
        return config["live"].get("execution_session") or "CLOSE_AUCTION"
    return "CLOSE_AUCTION"
```

`strategy_statuses` 第一行的 `"execution_profile": "CLOSE_AUCTION"` 改成
`_profile_name(main_strategy_id)`。

**不要**改 `MAIN_REAL_STRATEGY_ID`。它只在 `kind: OPERATOR_PROBE` 下用来找「被暂停的
主策略」。改成阶梯会让一份过期探针配置有能力暂停新主策略。常量留在 CSI1000，在定义
处加注释说明这是历史配对、探针退役后是惰性代码。

- [ ] **Step 4: 跑测试并提交**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_monitor_web_api.py -q`

Expected: PASS，含原有探针 overview 用例。

```bash
git add live_trading/web/api.py tests/live_trading/test_monitor_web_api.py
git commit -m "fix: show the loaded execution session on the monitor overview"
```

---

### Task 3: 只观测的 QMT 脚本 + Mac 预检

任务 4、5 需要在 Windows 上读 `get_instrument_detail` 和 tick，但仓库里现成的
`qmt_pr49_debug.py` 会 `passorder`。另写一个**没有下单符号**的脚本。Mac 预检脚本
负责在起账前拦住「误碰新账本」。

**Files:**
- Create: `live_trading/qmt_strategy/qmt_observe_security.py`
- Create: `live_trading/scripts/cutover_preflight.py`
- Test: `tests/live_trading/test_qmt_observe_security.py`
- Test: `tests/live_trading/test_cutover_preflight.py`

**Interfaces:**
- Consumes: QMT `ContextInfo.get_instrument_detail` / `get_full_tick`（Windows）；
  仓库内 yaml / openssl / parity（Mac）
- Produces:
  - `observe_dump_path() -> str` 默认 `D:\qmt_bridge\observe\observe_YYYYMMDD.jsonl`
  - `record_observation(kind, payload) -> None` 追加一行 JSON
  - `eligible_from_detail(detail) -> bool | None` 复用 bridge 同一套字段别名
  - `cutover_preflight(project_root: Path) -> dict` 只读，**禁止** import
    `LiveRecorder`

- [ ] **Step 1: 写失败测试（观测解析 + 预检拒碰新库）**

`tests/live_trading/test_qmt_observe_security.py`：

```python
from live_trading.qmt_strategy.qmt_observe_security import (
    eligible_from_detail, close_is_final,
)

def test_true_after_hours_flag_is_eligible():
    assert eligible_from_detail({"IsAfterHoursTrading": True}) is True
    assert eligible_from_detail({"AfterHoursTrading": 1}) is True
    assert eligible_from_detail({"FixedPriceTrading": "yes"}) is True

def test_missing_flag_is_unknown_not_false():
    assert eligible_from_detail({"InstrumentID": "688001.SH"}) is None

def test_timetag_at_or_after_fifteen_is_final():
    assert close_is_final({"timetag": "20260825 15:00:00"}) is True
    assert close_is_final({"timetag": "20260825 14:59:59"}) is False
    assert close_is_final({"lastPrice": 10.0}) is None
```

`tests/live_trading/test_cutover_preflight.py`：

```python
import textwrap
from pathlib import Path

from live_trading.scripts.cutover_preflight import cutover_preflight

CONFIG_REL = Path("live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml")
DB_REL = Path("live_trading/data/alla_v4_ladder_k3h5_postclose_real.db")


def _write_yaml(root: Path, opening_cash: float) -> None:
    path = root / CONFIG_REL
    path.parent.mkdir(parents=True)
    path.write_text(textwrap.dedent(f"""\
        account:
          opening_cash: {opening_cash}
          opening_value_adjustment: 0.0
        storage:
          db_path: "{DB_REL.as_posix()}"
        model:
          members: []
        live:
          strategy_id: alla_v4_ladder_k3h5_postclose_real
    """), encoding="utf-8")


def test_preflight_does_not_create_the_new_ledger(tmp_path):
    _write_yaml(tmp_path, 1_000_000.0)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["new_ledger_exists"] is False
    assert not (tmp_path / DB_REL).exists()


def test_preflight_flags_placeholder_opening_cash(tmp_path):
    _write_yaml(tmp_path, 1_000_000.0)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["opening_cash_is_placeholder"] is True


def test_preflight_clears_placeholder_after_cash_is_written(tmp_path):
    _write_yaml(tmp_path, 123_456.78)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["opening_cash_is_placeholder"] is False
```

`cutover_preflight(project_root, skip_parity=False, skip_sha=False)` 用
`yaml.safe_load` 读配置，**不得** import `LiveRecorder`。`skip_*` 只给单测用；命令行
默认两项都跑。

- [ ] **Step 2: 跑测试，确认失败**

Expected: `ModuleNotFoundError` 或 import 失败。

- [ ] **Step 3: 实现观测脚本**

`qmt_observe_security.py` 必须：

- 文件头 `#coding:gbk`
- **全文不出现** `passorder` / `order_stock` / `buy` / `sell` 这些下单 API 名（用
  测试锁住）
- `CODES` 默认含主板 / 科创 / 创业 / 深市主板各至少一只，例如
  `600000.SH`、`688001.SH`、`300750.SZ`、`000001.SZ`、`601318.SH` 以及再补五只
  由操作者改本地副本；仓库默认值只用于语法与解析测试
- `init` 注册 1 秒定时器；`handlebar` 对每只票写一行
  `{ts, code, detail_raw, after_hours_eligible, tick_raw, close_is_final}`
- 复用与 bridge 相同的字段别名（`IsAfterHoursTrading` / `AfterHoursTrading` /
  `FixedPriceTrading`；`timetag` / `m_strTime` / `time`）
- 写到 `D:\qmt_bridge\observe\`，不写 inbox、不写 state

`eligible_from_detail` / `close_is_final` 做成模块顶层纯函数，Mac 单测直接 import。
QMT 侧 `init` 只调这两个函数。若 QMT 的 import 路径麻烦，把纯函数放在文件顶部、
`init` 之下，测试用 `importlib` 按路径加载（与 `test_qmt_bridge_logic.py` 相同）。

- [ ] **Step 4: 实现预检脚本**

`cutover_preflight(project_root)` 返回并打印：

| 键 | 通过标准 |
|---|---|
| `parity_ok` | `check_backtest_parity` 对 `alla_v4_ladder_k3h5_postclose_real` 返回 0 |
| `sha_ok` | 五个 member 的 `openssl dgst -sha256` 与 yaml 一致 |
| `new_ledger_exists` | 必须是 `False`（任务 8 之前） |
| `opening_cash_is_placeholder` | yaml `opening_cash == 1000000.0` 时为 True；任务 8 之前允许 True，任务 8 之后必须 False |
| `old_cron_token` | crontab 示例仍含旧 id 时为 True（任务 9 之前） |

parity 检查用子进程调用已有
`live_trading/scripts/check_backtest_parity.py --config alla_v4_ladder_k3h5_postclose_real`，
不要在预检里重新实现门禁。

- [ ] **Step 5: 锁住「观测脚本下不了单」**

```python
def test_observe_script_has_no_order_verbs():
    text = Path("live_trading/qmt_strategy/qmt_observe_security.py").read_text()
    for verb in ("passorder", "order_stock", "opentradestock"):
        assert verb not in text.lower()
```

- [ ] **Step 6: 跑测试并提交**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_observe_security.py \
  tests/live_trading/test_cutover_preflight.py -q
```

```bash
git add live_trading/qmt_strategy/qmt_observe_security.py \
        live_trading/scripts/cutover_preflight.py \
        tests/live_trading/test_qmt_observe_security.py \
        tests/live_trading/test_cutover_preflight.py
git commit -m "feat: add orderless QMT observation and a cutover preflight"
```

---

### Task 4: 生产账户盘后资格字段（人工 · Windows）

fail-closed：字段缺失则**所有**盘后单都会 `SECURITY_ELIGIBILITY_ERROR`。一手探针只测过
`600000.SH`。

**Files:** 无仓库改动，除非要把观测 jsonl 摘录写进本计划「实施记录」段。

- [ ] **Step 1: 停住，向用户确认交易日**

输出以下原文，等用户回复交易日之后才能继续：

```
任务 4 需要你在 Windows QMT 上导入 qmt_observe_security.py（独立策略，不绑交易）。
不会下单。请回复：1) 观测交易日；2) 本地副本里最终的 10 只代码（必须含 SH688* 与 SZ30*）。
```

- [ ] **Step 2: 用户在 QMT 导入并启动观测策略，跑满至少一轮定时器**

把仓库脚本复制到独立策略，**不要**覆盖主 bridge 或探针。`CODES` 改成用户指定的 10 只。

- [ ] **Step 3: 从 Mac 读回 jsonl 并判定**

```bash
python -c "import json,sys,collections
p='/Volumes/qmt_bridge/observe/observe_YYYYMMDD.jsonl'
rows=[json.loads(l) for l in open(p)]
last={}
for r in rows: last[r['code']]=r
for c,r in last.items():
    print(c, r.get('after_hours_eligible'), sorted((r.get('detail_raw') or {}).keys())[:8])
"
```

通过标准：10 只的 `after_hours_eligible is True`。任一只是 `None` 或 `False` → **停止
切换**，不要进入任务 7。

- [ ] **Step 4: 把结论写进本计划文末「实施记录」并提交**

格式：日期、10 只代码、每只 True/False/None、原始字段名（不要账户号）。

```bash
git add docs/superpowers/plans/2026-08-24-live-v4-plan4-cutover-runbook.md
git commit -m "docs: record after-hours eligibility probe on the production account"
```

---

### Task 5: 收盘价终态信号（人工 · 必须卡 14:59–15:01）

决定自适应提交是否真能早于 15:01。代码路径已在计划二落地：全员 `None` 则固定 15:01。

- [ ] **Step 1: 停住，确认观测交易日（须为交易日，且 QMT 保持连接到 15:01）**

- [ ] **Step 2: 14:59:50 前启动同一观测策略，收到 15:01:10 再停**

- [ ] **Step 3: 判定**

对每只票按时间排序 `close_is_final`：

- 若存在从 `False`/`None` 翻成 `True`、且翻转时刻 ≥ 15:00:00：终态信号可用，自适应生效
- 若全程 `None`：固定 15:01，次日 `NETTING_CLOSE_MISMATCH` 是接受该路径的前提（已在计划三）
- 若 15:00 之后仍长时间 `False`：记录，仍走 15:01 兜底，不要为了抢队列改代码

- [ ] **Step 4: 写实施记录并提交**

```bash
git commit -m "docs: record whether QMT exposes a usable close-finality timetag"
```

---

### Task 6: 用旧通道清空零星一手（人工 · 真钱）

旧账本不迁到新账本。账户必须空仓，新阶梯才能从 0 爬到 5 层。

- [ ] **Step 1: 盘点，不要先下单**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_b6m_b2s_postclose_real \
  --for-config csi1000_b6m_b2s_postclose_real \
  --trade-date "$(date +%F)" --prepare
```

只 `--prepare`。把账本 `positions` 与即将发布的快照对用户列出：代码、股数。
**空仓则本任务整段跳过**，在实施记录写「已空仓，未发卖单」。

- [ ] **Step 2: 停住。用户逐只点名卖出**

沿用 README「主策略 SELL 验收」：`set_execution_state.py --state PAUSED` →
`run_operator_probe.py` / `override_main_signal.py` 发 SELL → 用户当日确认 →
Windows 创建 **`LIVE_OK_`（集合竞价前缀，主 state）** → 14:57 成交 → import。
一次一只。禁止为了赶时间改成盘后卖。

旧主策略当天必须保持可运行的 `CLOSE_AUCTION` 实例。不要先做任务 7。

- [ ] **Step 3: 通过标准**

券商快照持仓为空；旧账本 `positions` 为空。旧账本此后不再写入。

- [ ] **Step 4: 恢复 PAUSED 为长期暂停（不要 ACTIVE）**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config csi1000_b6m_b2s_postclose_real \
  --state PAUSED --reason 'retired; replaced by alla_v4_ladder_k3h5_postclose_real'
```

CSI1000 配置从这一刻起不应再被发布。cron 仍可能在任务 9 之前触发一次 16:00——任务 9
必须在下一个 16:00 之前完成，或当天把 crontab 先注释掉。

---

### Task 7: 退役探针、渲染并装主实例为盘后

- [ ] **Step 1: 停住。确认任务 4 通过、任务 6 空仓、此刻无在途委托**

- [ ] **Step 2: Windows — 停两个 QMT 策略实例（主 + 探针），不要卸载目录**

`D:\qmt_bridge\pr49_probe\` 目录保留。双 root 配对还在，只是探针进程不再启动。

- [ ] **Step 3: 把仓库新 PS1 拷到 `D:\qmt_bridge\tools\`，核对 SHA256 与仓库一致**

- [ ] **Step 4: Mac 渲染生产运行时（默认值就是生产形态）**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/render_qmt_runtime.py \
  --main-source live_trading/qmt_strategy/qmt_signal_bridge.py \
  --pr49-source live_trading/qmt_strategy/qmt_pr49_debug.py \
  --output-dir /tmp/qmt_runtime_v4 \
  --expected-cash <任务8将写入的同一现金，先用 QMT UI 读数>
```

打开 `/tmp/qmt_runtime_v4/qmt_signal_bridge.py` 人工核对恰好一次：

```
EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"
ENABLE_LADDER_NETTING = True
MAX_ORDER_QUANTITY = 0
BRIDGE_ROOT = r"D:\qmt_bridge"
OTHER_BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"
```

`ACCOUNT_ID` / `REAL_EXPECTED_INITIAL_CASH` 只在 Windows 本地副本填。

- [ ] **Step 5: 将渲染产物贴进「主策略」QMT 实例，编译，启动**

不要启动探针实例。持久日志必须出现 `RUNTIME_CONFIG` 与 `TIMER_REGISTERED`。逐项核对：

| 项 | 值 |
|---|---|
| `qmt_price_type` | 49 |
| `submit_after` | 15:00:05 |
| `timer_start` | 14:59:55 |
| `cancel_at` | 15:28:00 |
| `finalize_at` | 15:30:00 |
| `snapshot_after` | 15:31:00 |
| `authorization_prefix` | `PR49_LIVE_OK_` |
| `max_order_quantity` | 0 |
| `enable_ladder_netting` | True |

不一致 → 停实例，不建 marker。

- [ ] **Step 6: 确认当日主 state 没有 `LIVE_OK_`，也还没有 `PR49_LIVE_OK_`（T 日不交易）**

```bash
find /Volumes/qmt_bridge/state /Volumes/qmt_bridge/pr49_probe/state \
  \( -name "LIVE_OK_$(date +%F)" -o -name "PR49_LIVE_OK_$(date +%F)" \) -print
```

预期：无输出。有输出 → 停，不要删，先查谁写的。

---

### Task 8: 新账本按券商现金起账

- [ ] **Step 1: 用旧配置或 QMT UI 读可用资金，写到纸面上（不要用新配置碰 LiveRecorder）**

快照采集可以继续走旧配置（任务 1 之后 AFTER_HOURS 快照也会进主 inbox，主实例已在跑盘后
profile，可以回快照）。**不要** `--for-config alla_v4_ladder_k3h5_postclose_real`。

- [ ] **Step 2: 改 yaml 的 `opening_cash` 为该数字，`opening_value_adjustment` 保持 0**

只改这两个账户字段。提交这条时提交信息写 `chore: seed ladder opening cash from broker snapshot`，
**不要**把数字写进 commit message 之外的聊天记录复制到公共位置。

- [ ] **Step 3: 跑预检，确认占位标志已灭、新库仍不存在**

```bash
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/cutover_preflight.py
```

- [ ] **Step 4: 第一次用新配置构造 LiveRecorder（import --dry 或一次只读 get_cash）**

写一个**单行**脚本文件再跑（禁止 heredoc 调 Qlib）。例如扩展 `cutover_preflight.py --seed-check`：
构造 `LiveRecorder` 后 `get_cash()` 等于 yaml，且 `positions == []`，`cohort_layers` 为空。

若这一步之前新库已经存在且现金是 `1000000.0`：**停止**。不要 DELETE 账本充数；人工打开
SQLite 看是否已被误用。误 seed 且无业务行时，用户明确同意后才能删文件重来。

- [ ] **Step 5: 提交 yaml**

```bash
git add live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml
git commit -m "chore: seed the ladder ledger opening cash from the broker"
```

---

### Task 9: 切 cron / launchd / wrapper 默认 id

**Files:**
- Modify: `live_trading/run_postclose_cron.sh` 及所有 `CONFIG_ID="${1:-...csi1000...}"` 的 wrapper
- Modify: `live_trading/run_web_service.sh`
- Modify: `live_trading/crontab.csi1000_postclose.example`（文件名保持，避免多余重命名）
- Modify: `live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`
- Test: `tests/live_trading/test_operational_wrappers.py`

默认 id 与现网 crontab 必须**同一提交、同一操作窗口**落地。只改 Git、不改机器，16:00
仍会跑旧系统；只改机器、不改 Git，文档与 wrapper 会把人带回旧 id。

- [ ] **Step 1: 先改测试期望**

`test_wrappers_are_configurable_and_default_to_real_system`：断言字符串改为
`alla_v4_ladder_k3h5_postclose_real`。

`test_crontab_uses_one_durable_scheduler_entry`：命令改为

```
0 16 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_scheduler_cron.sh alla_v4_ladder_k3h5_postclose_real
```

launchd 测试的 ProgramArguments / 日志文件名同步改。README 里的旧 wrapper 示例不在本
任务改（任务 10）。

- [ ] **Step 2: 跑测试，确认失败**

- [ ] **Step 3: 改 wrapper 默认、crontab 示例、plist**

每个 wrapper 的默认只替换 token，不改锁逻辑、不改确认环境变量。plist 日志文件名改成
`alla_v4_ladder_k3h5_postclose_real_web_service.{stdout,stderr}.log`。

- [ ] **Step 4: 跑测试**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operational_wrappers.py -q
```

- [ ] **Step 5: 停住。用户改现网**

```bash
# 用户执行，agent 只打印不要代跑 crontab -e
crontab -l
launchctl unload ~/Library/LaunchAgents/com.yuxianqi.qlib-live-monitor.plist
# 拷新 plist 后
launchctl load ~/Library/LaunchAgents/com.yuxianqi.qlib-live-monitor.plist
```

`~/.qlib_live_env` 的 `LIVE_CONFIG_ID` 改成 `alla_v4_ladder_k3h5_postclose_real`。
`crontab -l` 不得再出现 `csi1000_b6m_b2s_postclose_real`。

- [ ] **Step 6: 提交**

```bash
git commit -m "chore: point live wrappers and launchd at the ladder config"
```

---

### Task 10: 文档改写成新事实

只在任务 7–9 已完成后写。写早了就是假事实。

**Files:**
- Modify: `live_trading/README.md`（活动系统 / 固定契约 / 受控晋级 / 调度示例）
- Modify: `live_trading/qmt_strategy/README_QMT.md`（主副本示例改为盘后生产形态；探针段改「已退役」）
- Modify: `live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md`（文首加退役横幅，**保留**
  `test_pr49_operator_checklist_is_a_controlled_repository_artifact` 要求的全部 token）
- Modify: `AGENTS.md` 第 1 条：补「全A 实盘配置为 `alla_v4_ladder_k3h5_postclose_real`」
- Modify: `backtest/EXPERIMENT_STANDARD.md` 第 1.4 节：补一句「2026-08-XX 用户批准将该
  基线切到实盘」，**不要**改 CSI1000 研究基线定义
- Modify: `backtest/experiments/LESSONS.md`：先记切换事实与「成交率 / 抵销省费待建仓期
  满 5 日 / 满月后再补数字」。禁止编数字。

固定契约表必须改成：

| 项目 | 值 |
|---|---|
| 活动配置 | `alla_v4_ladder_k3h5_postclose_real` |
| 对照配置 | `backtest/configs/alla_v4_ladder_k3h5_parity.yaml` |
| 股票池 / benchmark | 全A 四重过滤 / `SH000985` |
| 策略 | `CohortLadderStrategy` topk=3 horizon=5 risk_degree=0.90 |
| 模型 | v4 五种子日截面 z-score 等权 |
| 账本 | `live_trading/data/alla_v4_ladder_k3h5_postclose_real.db` |
| 执行 | 盘后固定价格 `prType=49`，15:00:05 起试 / 15:01 兜底 |
| 授权 marker | **`PR49_LIVE_OK_YYYY-MM-DD`，位于主 root `state/`**（不是 `pr49_probe/state`） |
| 一手闸 | 已关闭（`MAX_ORDER_QUANTITY = 0`） |
| 抵销 | bridge 提交时刻，用当日收盘价 |

必须写明的三件事：

1. `PR49_LIVE_OK_` 现在是**生产**授权名，不是探针名。
2. 前次一手探针只验证了管路，**成交能力未验证**。
3. `report` 依赖 `update`；`update` 失败则收盘价对账与成交率检查当天不跑，建仓期必须
   人工确认 `report` 执行了。

README_QMT 主副本示例改为任务 7 的生产值；探针副本整段标「已退役，禁止同日启动」。
`OTHER_BRIDGE_ROOT` 仍指向 `D:\qmt_bridge\pr49_probe`（配对约束），但该实例不得运行。

- [ ] **Step 1: 改文档**
- [ ] **Step 2: 跑边界测试，确认清单 token 还在**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -q
```

- [ ] **Step 3: 提交**

```bash
git commit -m "docs: record the live cutover onto the all-A v4 ladder"
```

---

### Task 11: 建仓期 5 个交易日人工核对

第 N 日结束时应有 N 个分层、至多 `3N` 个仓位（封板/停牌导致层变薄则更少）。第 5 日
集齐 5 层。

每日 16:00 流水线结束后核这张表，写进当天实施记录，**缺一项就停次日发布**：

| 检查 | 命令 / 看法 | 失败则 |
|---|---|---|
| `report` 真的跑了 | `.scheduler/<id>/<date>/` 里有 report 收据 | 先补跑 `run_monitor_cron.sh report`，再看对账 |
| 入场名字 | preview / 批次 BUY 腿 vs 当日分数 top3 | 允许因不顺延少成交，不允许静默换成第 4 名还当成功 |
| 分层账龄严格递增 | `cohort_layers.buy_trade_date` | 停发布 |
| `LADDER_NET` 算术 | bridge 事件：`intended` / `netted` / `net` | 对不上就停 |
| 买入加权成交率 | 日报；硬地板 0.50、软地板 0.80×3 日 | 已是 CRIT；触发则暂停发布，评估切回 `CLOSE_AUCTION` |
| `NETTING_CLOSE_MISMATCH` | 次日 report | CRIT 则当日层按错价定量，停下来对账 |
| 授权文件位置 | `/Volumes/qmt_bridge/state/PR49_LIVE_OK_<date>` 存在；`pr49_probe/state` 下没有新的当日 marker | 有双份 → 停两个实例 |

回退（第 8 节）：停止实例，保留全部 marker 与日志。切回集合竞价时抵销必须改回
「Mac 估算 + 已知偏差」，因为 14:57 没有确定收盘价——那是另一次变更，不在本计划偷偷做。

5 日结束后把实测成交率区间补进 `LESSONS.md`（只写看见的数字）。

---

## 完成标准

- [ ] `AFTER_HOURS` 主实例能在 `D:\qmt_bridge` 启动；`PR49_LIVE_OK_` 创建于主 `state/`
- [ ] 探针 QMT 实例不再运行；同日不得再有 `LIVE_OK_`
- [ ] 新账本现金 = 起账时券商可用资金，分层从空开始
- [ ] `crontab -l` 与 launchd 只调度 `alla_v4_ladder_k3h5_postclose_real`
- [ ] 监控 overview 在加载新配置时显示 `AFTER_HOURS_FIXED_PRICE`
- [ ] 资格探测 10/10 `True`；终态探测结论已记录
- [ ] README / README_QMT / AGENTS / 规范 1.4 描述的是新系统
- [ ] 建仓期清单被实际用过，不是空表
- [ ] `pytest tests/live_trading/ -q` 全绿

## 明确不做

- 不把 `operator_probe.MAIN_STRATEGY_ID` 改成阶梯（防止过期探针配置暂停新主策略）
- 不重命名 `PR49_LIVE_OK_`
- 不删除 `pr49_probe` 目录
- 不在本计划做集合竞价回退的代码
- 不编造成交率或省费数字

## 实施记录

### 2026-08-24 · 任务 1–3（Mac，零实盘）

- 任务 1 已提交：`d1c7be0a` 盘后实例可坐主 root；`PR49_LIVE_OK_` 与 `LIVE_OK_` 都落在主 `state/`；快照采集根 `AFTER_HOURS → D:\qmt_bridge`。Windows 上 `D:\qmt_bridge\tools\` 的 PS1 **还是旧副本**，任务 7 装机时再拷。
- 任务 2 已提交：`fecdeade` 监控 overview 读配置的 `execution_session`。三处 `MAIN_STRATEGY_ID` 仍指向 CSI1000。
- 任务 3 已提交：`qmt_observe_security.py`（全文无下单 API）+ `cutover_preflight.py`。
- 全套 `pytest tests/live_trading/ -q` → **1088 passed**。
- **预检在本机跑过，有一个任务 8 必须先处理的事实：**
  `live_trading/data/alla_v4_ladder_k3h5_postclose_real.db` **已经存在**（gitignored），
  `account_state.cash = 1000000.0`（yaml 占位值），batches / fills / positions /
  cohort_* 行数全是 0。这是计划一/二 dry-run 误 seed 的。任务 8 起账前必须先删这个
  空库再按券商现金重建——用户明确同意后才能删。

（任务 4、5、6、11 往这里追加。没有记录等于没做。）
