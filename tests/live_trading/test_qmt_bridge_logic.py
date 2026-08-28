"""QMT 内置桥接策略的纯逻辑测试（不依赖 QMT API）。

覆盖设计文档定稿的安全关键路径：
- 过期批次（trade_date != 当日）整批 SKIPPED
- 重复批次 SKIPPED duplicate
- checksum 不符整批拒绝
- 合法批次正确认领并按 priority 排序（先卖后买）
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_schema import compute_checksum

BRIDGE_PATH = REPO_ROOT / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
BATCH_ID = "20260714_csi300_topk10_001"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qmt_signal_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BRIDGE_ROOT = str(tmp_path)
    mod._ensure_dirs()
    mod._load_processed()
    return mod



class _ScheduleContext:
    def __init__(self):
        self.calls = []

    def set_account(self, account_id):
        self.account_id = account_id

    def schedule_run(self, *args):
        self.calls.append(args)
        return 1


def _activate_profile(bridge, profile, bridge_root, other_bridge_root=None):
    del other_bridge_root
    bridge.EXECUTION_PROFILE = profile
    bridge.BRIDGE_ROOT = str(bridge_root)
    context = _ScheduleContext()
    bridge.init(context)
    return context


def _profile_roots(tmp_path, profile):
    main_root = tmp_path / "main"
    probe_root = main_root / "pr49_probe"
    if profile == "CLOSE_AUCTION":
        return main_root, probe_root
    return probe_root, main_root


































def test_qmt_cash_reservation_fees_match_live_config(bridge):
    config_path = (
        REPO_ROOT / "live_trading" / "configs" /
        "csi1000_b6m_b2s_postclose.yaml"
    )
    fees = yaml.safe_load(config_path.read_text(encoding="utf-8"))["fees"]

    assert bridge.COMMISSION_RATE == pytest.approx(fees["commission_rate"])
    assert bridge.MIN_COMMISSION == pytest.approx(fees["min_commission"])
    assert bridge.TRANSFER_FEE_RATE == pytest.approx(fees["transfer_fee_rate"])


def test_close_auction_profile_keeps_legacy_runtime_contract(bridge, tmp_path):
    main_root, probe_root = _profile_roots(tmp_path, "CLOSE_AUCTION")

    context = _activate_profile(
        bridge, "CLOSE_AUCTION", main_root, probe_root,
    )

    assert bridge._profile_settings() == {
        "signal_price_type": "CLOSE_AUCTION_LIMIT",
        "qmt_price_type": 11,
        "submit_after": "14:57:05",
        "submit_deadline": "14:57:05",
        "cancel_at": "15:00:05",
        "finalize_at": "15:00:30",
        "snapshot_after": "15:01:00",
        "sell_deadline": "14:57:05",
        "timer_start": "14:56:55",
    }
    assert bridge._expected_signal_price_type() == "CLOSE_AUCTION_LIMIT"
    assert bridge.LIMIT_PRICE_TYPE == 11
    assert bridge.SELL_DEADLINE == "14:57:05"
    assert bridge.SUBMIT_DEADLINE == "14:57:05"
    assert bridge.TRADE_START == "14:57:05"
    assert bridge.CANCEL_AT == "15:00:05"
    assert bridge.FINALIZE_AT == "15:00:30"
    assert bridge.SNAPSHOT_REFRESH_AT == "15:01:00"
    assert bridge.g.trading_enabled is True
    postclose = next(call for call in context.calls if call[4] == "qlib_postclose_poll")
    assert postclose[1].endswith("145655")


def test_after_hours_profile_activates_isolated_pr49_contract(bridge, tmp_path):
    probe_root, main_root = _profile_roots(
        tmp_path, "AFTER_HOURS_FIXED_PRICE",
    )

    context = _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", probe_root, main_root,
    )

    assert bridge._profile_settings() == {
        "signal_price_type": "AFTER_HOURS_CLOSE",
        "qmt_price_type": 49,
        "submit_after": "15:00:05",
        "submit_deadline": "15:01:00",
        "cancel_at": "15:28:00",
        "finalize_at": "15:30:00",
        "snapshot_after": "15:31:00",
        "sell_deadline": "15:09:00",
        "timer_start": "14:59:55",
    }
    assert bridge._expected_signal_price_type() == "AFTER_HOURS_CLOSE"
    assert bridge.LIMIT_PRICE_TYPE == 49
    assert bridge.SELL_DEADLINE == "15:09:00"
    assert bridge.SUBMIT_DEADLINE == "15:01:00"
    assert bridge.TRADE_START == "15:00:05"
    assert bridge.CANCEL_AT == "15:28:00"
    assert bridge.FINALIZE_AT == "15:30:00"
    assert bridge.SNAPSHOT_REFRESH_AT == "15:31:00"
    assert bridge.g.trading_enabled is True
    postclose = next(call for call in context.calls if call[4] == "qlib_postclose_poll")
    assert postclose[1].endswith("145955")


def test_invalid_execution_profile_disables_runtime_with_structured_error(
    bridge, tmp_path,
):
    bridge.EXECUTION_PROFILE = "TYPO_PROFILE"
    bridge.BRIDGE_ROOT = str(tmp_path / "current")
    context = _ScheduleContext()

    bridge.init(context)

    assert bridge.g.loaded is True
    assert bridge.g.trading_enabled is False
    assert context.calls == []
    rows = (
        Path(bridge.BRIDGE_ROOT) / "logs" /
        ("qmt_events_%s.jsonl" % bridge._today())
    ).read_text().splitlines()
    event = json.loads(rows[-1])
    assert event["event"] == "INVALID_EXECUTION_PROFILE"
    assert event["execution_profile"] == "TYPO_PROFILE"



def test_init_registers_post_close_timer_independent_of_market_bars(bridge):
    assert bridge.TRADE_START == "14:57:05"
    assert bridge.CANCEL_AT == "15:00:05"
    assert bridge.FINALIZE_AT == "15:00:30"
    assert bridge.SNAPSHOT_REFRESH_AT == "15:01:00"
    assert bridge.MAX_ORDER_QUANTITY == 100
    calls = []

    class Context:
        def set_account(self, account_id):
            self.account_id = account_id

        def schedule_run(self, *args):
            calls.append(args)
            return 1

    bridge.init(Context())

    assert len(calls) == 1
    callback, first_at, repeats, interval, name = next(
        call for call in calls if call[4] == "qlib_postclose_poll"
    )
    assert callback is bridge.timer_callback
    assert first_at.endswith("145655")
    assert repeats == -1
    assert interval.total_seconds() == bridge.POLL_SECONDS
    assert name == "qlib_postclose_poll"



def test_timer_registration_is_safe_when_qmt_invokes_overdue_timer_immediately(
    bridge, monkeypatch,
):
    advances = []
    monkeypatch.setattr(
        bridge, "_advance", lambda context: advances.append(context),
    )

    class Context:
        def schedule_run(self, callback, *args):
            callback(self)
            return 1

    context = Context()
    bridge.init(context)

    assert bridge.g.timer_registered is True
    assert advances == [context]


def _order(coid="20260714001S", side="SELL", priority=10):
    is_buy = side == "BUY"
    return {
        "type": "order", "batch_id": BATCH_ID, "client_order_id": coid,
        "stock_code": "000001.SZ", "side": side,
        "quantity": 0 if is_buy else 800,
        "target_value": 8_000.0 if is_buy else 0.0,
        "price_type": "CLOSE_AUCTION_LIMIT", "limit_price": 0.0,
        "priority": priority,
        "instrument_qlib": "SZ000001", "reason": "test",
    }


def _write_batch(
    bridge, trade_date, orders, checksum=None, batch_id=BATCH_ID,
    mode="SIMULATE", account_environment="SIMULATION", account_id="1",
):
    order_lines = [json.dumps(o, sort_keys=True, separators=(",", ":")) for o in orders]
    if checksum is None:
        checksum = compute_checksum(order_lines)
    header = {
        "type": "batch_header", "schema_version": "2.0", "batch_id": batch_id,
        "strategy_id": "s", "trade_date": trade_date, "signal_date": trade_date,
        "account_id": account_id, "account_type": "STOCK",
        "account_environment": account_environment, "mode": mode,
        "created_at": "t", "order_count": len(orders), "checksum": checksum,
    }
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    jsonl = inbox / ("signal_%s.jsonl" % batch_id)
    jsonl.write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + order_lines) + "\n")
    (inbox / ("signal_%s.done" % batch_id)).write_text(checksum + "\n")


def _read_fills(bridge, batch_id=BATCH_ID):
    p = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.jsonl" % batch_id)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _read_events(bridge):
    path = (
        Path(bridge.BRIDGE_ROOT) / "logs" /
        ("qmt_events_%s.jsonl" % bridge._today())
    )
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _assert_event_subsequence(events, expected):
    remaining = iter([row["event"] for row in events])
    for event_name in expected:
        assert any(actual == event_name for actual in remaining), (
            "missing ordered event %s" % event_name
        )


@pytest.mark.parametrize(
    "profile,price_type,current_prefix,other_prefix,now",
    [
        (
            "CLOSE_AUCTION", "CLOSE_AUCTION_LIMIT",
            "LIVE_OK_", "PR49_LIVE_OK_", "14:57:30",
        ),
        (
            "AFTER_HOURS_FIXED_PRICE", "AFTER_HOURS_CLOSE",
            "PR49_LIVE_OK_", "LIVE_OK_", "15:05:30",
        ),
    ],
)
def test_legacy_markers_do_not_block_order_submission(
    bridge, monkeypatch, tmp_path,
    profile, price_type, current_prefix, other_prefix, now,
):
    current_root, other_root = _profile_roots(tmp_path, profile)
    _activate_profile(bridge, profile, current_root, other_root)
    order = _order(coid="20260714001001S", side="SELL", priority=10)
    order["price_type"] = price_type
    _write_batch(bridge, bridge._today(), [order], mode="LIVE")
    current_marker = (
        current_root / "state" / (current_prefix + bridge._today())
    )
    other_marker = other_root / "state" / (other_prefix + bridge._today())
    current_marker.write_text("")
    other_marker.parent.mkdir(parents=True)
    other_marker.write_text("")
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: now)
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(bridge, "_get_can_use_volume", lambda *args: 100)
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)
    assert submitted





@pytest.mark.parametrize(
    "profile,wrong_price_type",
    [
        ("CLOSE_AUCTION", "AFTER_HOURS_CLOSE"),
        ("AFTER_HOURS_FIXED_PRICE", "CLOSE_AUCTION_LIMIT"),
    ],
)
def test_batch_price_type_must_match_selected_qmt_profile(
    bridge, tmp_path, profile, wrong_price_type,
):
    current_root, other_root = _profile_roots(tmp_path, profile)
    _activate_profile(
        bridge, profile, current_root, other_root,
    )
    order = _order()
    order["price_type"] = wrong_price_type
    _write_batch(bridge, bridge._today(), [order])

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert _read_fills(bridge)[0]["message"] == (
        "price_type must match execution profile: " +
        bridge._expected_signal_price_type()
    )


def test_expired_batch_skipped(bridge):
    _write_batch(bridge, "2020-01-01", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert len(fills) == 1
    assert fills[0]["status"] == "SKIPPED"
    assert "expired" in fills[0]["message"]
    # done 已写出、batch 已登记 processed
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert BATCH_ID in bridge.g.processed


def test_future_batch_left_in_inbox(bridge):
    """T-1 晚发布的次日信号不应被提前消费，留在 inbox 等到 trade_date 当天。"""
    _write_batch(bridge, "2099-12-31", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # 未认领：文件原地保留，无回执，未登记 processed
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()
    assert (inbox / ("signal_%s.done" % BATCH_ID)).exists()
    assert _read_fills(bridge) == []
    assert BATCH_ID not in bridge.g.processed


def test_future_batch_claimed_on_trade_date(bridge):
    """到了 trade_date 当天，之前留在 inbox 的批次可正常认领。"""
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is not None
    assert bridge.g.batch.batch_id() == BATCH_ID


def test_checksum_mismatch_rejected(bridge):
    _write_batch(bridge, bridge._today(), [_order()], checksum="sha256:deadbeef")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and fills[0]["status"] == "SKIPPED"
    assert "checksum" in fills[0]["message"]


def test_duplicate_batch_skipped(bridge):
    bridge._mark_processed(BATCH_ID)
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and "duplicate" in fills[0]["message"]


def test_valid_batch_claimed_sells_first(bridge):
    orders = [
        _order(coid="20260714002B", side="BUY", priority=20),
        _order(coid="20260714001S", side="SELL", priority=10),
    ]
    _write_batch(bridge, bridge._today(), orders)
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    assert [o["side"] for o in batch.orders] == ["SELL", "BUY"]
    # 文件从 inbox 认领后保留在 processing，直到批次完成才归档
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert list(inbox.glob("*")) == []
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    assert len(list(processing.glob("signal_*"))) == 2
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    assert list(archive.glob("signal_*")) == []


def test_empty_batch_finalizes_with_empty_receipt_file(bridge, monkeypatch):
    _write_batch(bridge, bridge._today(), [])
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:05")

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert bridge.g.batch is None
    fills = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.jsonl" % BATCH_ID)
    done = fills.with_suffix(".done")
    assert fills.exists() and fills.read_text() == ""
    assert done.exists()


@pytest.mark.parametrize(
    "header_change,reason",
    [
        ({"schema_version": "1.0"}, "schema_version"),
        ({"account_type": "CREDIT"}, "account_type"),
    ],
)
def test_protocol_v2_and_simulation_account_are_fail_closed(
    bridge, header_change, reason,
):
    _write_batch(bridge, bridge._today(), [_order()])
    path = Path(bridge.BRIDGE_ROOT) / "inbox" / ("signal_%s.jsonl" % BATCH_ID)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    header.update(header_change)
    path.write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + lines[1:]) + "\n"
    )

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert reason in _read_fills(bridge)[0]["message"]


def test_qmt_accepts_header_environment_and_account_mismatch(bridge):
    _write_batch(
        bridge, bridge._today(), [_order()],
        mode="LIVE", account_environment="REAL", account_id="",
    )

    bridge._claim_new_batch()

    assert bridge.g.batch is not None
    assert bridge.g.batch.header.get("account_environment") == "REAL"
    assert bridge.g.batch.header.get("account_id") == ""


def test_claim_requires_configured_account_id(bridge):
    bridge.ACCOUNT_ID = ""
    _write_batch(bridge, bridge._today(), [_order()])

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert "ACCOUNT_ID" in _read_fills(bridge)[0]["message"]


def test_claim_accepts_header_without_execution_stamps(bridge):
    order_lines = [json.dumps(_order(), sort_keys=True, separators=(",", ":"))]
    checksum = compute_checksum(order_lines)
    header = {
        "type": "batch_header", "schema_version": "2.0", "batch_id": BATCH_ID,
        "strategy_id": "s", "trade_date": bridge._today(),
        "signal_date": bridge._today(), "account_type": "STOCK",
        "created_at": "t", "order_count": 1, "checksum": checksum,
    }
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / ("signal_%s.jsonl" % BATCH_ID)).write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + order_lines) + "\n",
    )
    (inbox / ("signal_%s.done" % BATCH_ID)).write_text(checksum + "\n")

    bridge._claim_new_batch()

    assert bridge.g.batch is not None
    assert "mode" not in bridge.g.batch.header
    assert "account_id" not in bridge.g.batch.header


class _RealAccountRow:
    m_strAccountID = "8890116049"
    m_dAvailable = 1_000_000.0




def test_batch_order_count_is_bounded_before_execution(bridge):
    orders = [
        _order(coid="202607140%03dS" % index)
        for index in range(bridge.MAX_ORDERS_PER_BATCH + 1)
    ]
    _write_batch(bridge, bridge._today(), orders)

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert "maximum" in _read_fills(bridge)[0]["message"]


def test_malformed_batch_is_quarantined_without_blocking_next_batch(bridge):
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / "signal_000_bad.jsonl").write_text("{not-json}\n")
    (inbox / "signal_000_bad.done").write_text("sha256:bad\n")

    valid_id = "20260714_csi300_topk10_002"
    valid_order = _order()
    valid_order["batch_id"] = valid_id
    _write_batch(
        bridge, bridge._today(), [valid_order], batch_id=valid_id,
    )

    bridge._claim_new_batch()

    assert bridge.g.batch.batch_id() == valid_id
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    assert (archive / "signal_000_bad.jsonl").exists()
    assert (archive / "signal_000_bad.done").exists()


@pytest.mark.parametrize("case", [
    "oversize", "empty", "malformed_header", "non_object_header",
    "malformed_order",
])
def test_unreadable_claimed_batch_emits_sanitized_validation_event(
    bridge, case,
):
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    jsonl = processing / ("signal_%s.jsonl" % case)
    done = processing / ("signal_%s.done" % case)
    done.write_text("sha256:bad\n")
    if case == "oversize":
        bridge.MAX_BATCH_BYTES = 8
        jsonl.write_text("x" * 9)
    elif case == "empty":
        jsonl.write_text("")
    elif case == "malformed_header":
        jsonl.write_text("{not-json}\n")
    elif case == "non_object_header":
        jsonl.write_text("[]\n")
    else:
        jsonl.write_text(json.dumps({
            "batch_id": "bad_batch", "strategy_id": "probe",
            "trade_date": bridge._today(),
            "account_id": "8890116049",
        }) + "\n{bad-order}\n")

    assert bridge._parse_and_check(str(jsonl), str(done)) is None

    event = [row for row in _read_events(bridge)
             if row["event"] == "BATCH_VALIDATED"][-1]
    assert event["validation_passed"] is False
    assert event["jsonl_file"] == jsonl.name
    assert event["done_file"] == done.name
    assert event["rejection_reason"]
    serialized = json.dumps(event, sort_keys=True)
    assert "8890116049" not in serialized
    if case == "malformed_order":
        assert event["batch_id"] == "bad_batch"
        assert event["strategy_id"] == "probe"
        assert event["account_id_masked"] == "88******49"


def test_structurally_invalid_order_fails_closed_without_crashing(bridge):
    invalid = _order()
    invalid.pop("client_order_id")
    _write_batch(bridge, bridge._today(), [invalid])

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert BATCH_ID in bridge.g.processed
    assert _read_fills(bridge) == []


def test_archive_collision_preserves_both_batch_copies(bridge):
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    jsonl = processing / "signal_repeat.jsonl"
    done = processing / "signal_repeat.done"
    jsonl.write_text("new-jsonl")
    done.write_text("new-done")
    (archive / jsonl.name).write_text("original-jsonl")
    (archive / done.name).write_text("original-done")

    bridge._archive_processing(str(jsonl), str(done))

    assert (archive / jsonl.name).read_text() == "original-jsonl"
    assert (archive / done.name).read_text() == "original-done"
    repeats = sorted(archive.glob("signal_repeat.repeat_*"))
    assert len(repeats) == 2
    assert {path.read_text() for path in repeats} == {"new-jsonl", "new-done"}


def test_processed_batch_state_is_idempotent(bridge):
    bridge._mark_processed(BATCH_ID)
    bridge._mark_processed(BATCH_ID)

    lines = (
        Path(bridge.BRIDGE_ROOT) / "state" / "processed_batches.txt"
    ).read_text().splitlines()
    assert lines == [BATCH_ID]


def test_restart_recovers_active_processing_batch(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    batch.phase = "BUY"
    batch.phase_started = 1234.5
    batch.submitted[_order()["client_order_id"]] = True
    batch.remaining_cash = 1234.5
    batch.execution_live = True
    coid = _order()["client_order_id"]
    batch.order_evidence[coid] = {
        "query_count": 2,
        "attempt_started": 1000.0,
        "api_returned": True,
        "api_return": {
            "return_repr": "None", "return_type": "NoneType",
            "elapsed_ms": 1.5,
        },
        "order_observed": True,
        "qmt_order_ids": ["qmt-recovered-1"],
        "callback_counts": {"order": 1, "deal": 1, "error": 0},
        "last_broker_statuses": ["qmt-recovered-1:48"],
    }
    bridge._save_active_state(batch)

    bridge.g.batch = None
    bridge._recover_processing_batch()

    recovered = bridge.g.batch
    assert recovered is not None
    assert recovered.phase == "BUY"
    assert recovered.phase_started == pytest.approx(1234.5)
    assert recovered.remaining_cash == pytest.approx(1234.5)
    assert _order()["client_order_id"] in recovered.submitted
    evidence = recovered.order_evidence[coid]
    assert evidence["query_count"] == 2
    assert evidence["api_returned"] is True
    assert evidence["api_return"]["return_type"] == "NoneType"
    assert evidence["order_observed"] is True
    assert evidence["callback_counts"] == {
        "order": 1, "deal": 1, "error": 0,
    }

    bridge._get_orders_by_remark = lambda account_id: {}
    bridge._poll_status(recovered)
    assert recovered.order_evidence[coid]["query_count"] == 3
    bridge._write_fill(
        recovered, recovered.orders[0], "ERROR", 0, 0.0,
        "qmt-recovered-1",
        "QMT order observed but final status unavailable at close",
    )
    final = [row for row in _read_events(bridge)
             if row["event"] == "ORDER_FINALIZED"][-1]
    assert final["api_returned"] is True
    assert final["order_observed"] is True
    assert final["query_count"] == 3
    assert final["callback_counts"]["deal"] == 1


def test_corrupt_active_state_recovers_without_duplicate_submission(bridge):
    orders = [
        _order(coid="20260714001001S", side="SELL", priority=10),
        _order(coid="20260714001002B", side="BUY", priority=20),
    ]
    _write_batch(bridge, bridge._today(), orders, mode="LIVE")
    bridge._claim_new_batch()
    active = (
        Path(bridge.BRIDGE_ROOT) / "state" / ("active_" + BATCH_ID + ".json")
    )
    active.write_text("{broken")
    bridge.g.batch = None

    bridge._recover_processing_batch()

    recovered = bridge.g.batch
    assert recovered.execution_live is True
    assert set(recovered.submitted) == {
        "20260714001001S", "20260714001002B",
    }
    assert active.exists()
    assert list(active.parent.glob(active.name + ".corrupt_*"))


def test_restart_repairs_claim_interrupted_between_two_file_moves(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    jsonl_name = "signal_%s.jsonl" % BATCH_ID
    done_name = "signal_%s.done" % BATCH_ID
    (inbox / jsonl_name).rename(processing / jsonl_name)
    assert (inbox / done_name).exists()

    bridge._recover_processing_batch()

    assert bridge.g.batch is not None
    assert (processing / jsonl_name).exists()
    assert (processing / done_name).exists()
    assert not list(inbox.glob("signal_*"))


def test_max_affordable_quantity_includes_buy_fees(bridge):
    assert bridge._max_affordable_quantity(10000.0, 10.0, 1600) == 900
    assert bridge._max_affordable_quantity(1000.0, 10.0, 1600) == 0


def test_target_buy_quantity_respects_target_value_cash_and_fees(bridge):
    assert bridge._target_buy_quantity(10_000.0, 10.0, 8_000.0) == 800
    assert bridge._target_buy_quantity(1_000.0, 10.0, 8_000.0) == 0


def test_sell_and_buy_are_submitted_in_same_close_auction_pass(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    batch.trading_started = True
    batch.phase_started = 100.0
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)

    bridge._process_batch(_TickCtx(10.0), batch)

    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["status"] == "SKIPPED"


def test_live_execution_gate_is_frozen_at_first_trading_pass(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy], mode="LIVE")
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)
    submitted = []
    account_queries = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(
        bridge, "_get_available_cash", lambda account_id: 100_000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *args: account_queries.append(args) or [],
        raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert bridge.g.batch is None
    assert account_queries
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["message"] != "simulated"


def test_missing_live_gate_blocks_all_close_auction_orders(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy], mode="LIVE")
    gate = (
        Path(bridge.BRIDGE_ROOT) / "state" /
        ("LIVE_OK_" + bridge._today())
    )
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)
    monkeypatch.setattr(bridge, "_get_can_use_volume", lambda *args: 800)
    monkeypatch.setattr(
        bridge, "_get_available_cash", lambda account_id: 100_000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert submitted == []
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["message"] != "simulated"


def test_buy_phase_uses_one_cash_snapshot_and_reserves_between_orders(
    bridge, monkeypatch,
):
    bridge.MAX_ORDER_QUANTITY = 0
    first = _order(coid="20260714001001B", side="BUY", priority=20)
    second = _order(coid="20260714001002B", side="BUY", priority=20)
    first.update(target_value=8_000.0)
    second.update(target_value=8_000.0, stock_code="600000.SH")
    _write_batch(bridge, bridge._today(), [first, second], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")

    cash_reads = []
    monkeypatch.setattr(
        bridge, "_get_available_cash",
        lambda account_id: cash_reads.append(account_id) or 10000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []

    def fake_passorder(*args):
        submitted.append({
            "price_type": args[4], "price": args[5], "quantity": args[6],
        })

    monkeypatch.setattr(bridge, "passorder", fake_passorder, raising=False)
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0), bridge.g.batch)

    assert len(cash_reads) == 1
    assert [row["quantity"] for row in submitted] == [800, 100]
    assert all(row["price_type"] == 11 for row in submitted)
    assert all(row["price"] == 11.0 for row in submitted)


def test_immutable_buy_maximum_caps_submission_when_rollout_cap_increases(
    bridge, monkeypatch,
):
    bridge.MAX_ORDER_QUANTITY = 1_000
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order.update(max_quantity=100, target_value=8_000.0)
    _write_batch(bridge, bridge._today(), [order], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 10000.0)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )

    bridge._process_batch(_TickCtx(10.0, up_stop=11.0), bridge.g.batch)

    assert [args[6] for args in submitted] == [100]


def test_available_cash_distinguishes_empty_query_from_real_zero(
    bridge, monkeypatch,
):
    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [], raising=False,
    )
    assert bridge._get_available_cash("8881352838") is None

    class Account:
        m_strAccountID = "8881352838"
        m_dAvailable = 0.0

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [Account()],
        raising=False,
    )
    assert bridge._get_available_cash("8881352838") == 0.0


def test_buy_phase_waits_when_account_cash_unavailable(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [order], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: None)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: pytest.fail("unavailable cash must not submit"),
        raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert bridge.g.batch.submitted == {}
    assert bridge.g.batch.remaining_cash is None
    assert _read_fills(bridge) == []


def test_cash_unavailable_at_close_writes_explicit_error(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8881352838", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.phase = "BUY"
    batch.trading_started = True
    bridge.g.batch = batch
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:00:30")
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})

    bridge._force_finalize_if_near_close(object(), batch)

    fills = _read_fills(bridge)
    assert len(fills) == 1
    assert fills[0]["status"] == "ERROR"
    assert fills[0]["message"] == "account cash unavailable at close"


def test_removing_live_switch_still_cancels_already_submitted_orders(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    batch.fills[order["client_order_id"]] = {
        "status": "ACCEPTED", "filled_qty": 0, "avg_price": 0.0,
    }

    class Detail:
        m_strOrderSysID = "qmt-order-1"
        m_nOrderStatus = -1
        m_nVolumeTraded = 0
        m_dTradedPrice = 0.0

    canceled = []
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:00:10")
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {order["client_order_id"]: [Detail()]},
    )
    monkeypatch.setattr(bridge, "can_cancel_order", lambda *args: True, raising=False)
    monkeypatch.setattr(
        bridge, "cancel", lambda *args: canceled.append(args), raising=False,
    )

    # There is intentionally no LIVE_OK file: it was removed after submission.
    bridge._force_finalize_if_near_close(object(), batch)

    assert len(canceled) == 1


def test_no_new_orders_are_submitted_after_cancel_cutoff(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.phase = "BUY"
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:00:10")
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 100000.0)

    bridge._process_batch(_TickCtx(10.0), batch)

    assert submitted == []
    assert order["client_order_id"] not in batch.submitted


def test_no_done_no_claim(bridge):
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / ("signal_%s.jsonl" % BATCH_ID)).write_text("{}\n")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # jsonl 仍留在 inbox（未消费）
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()


class _TickCtx:
    """Fake ContextInfo exposing QMT tick and instrument-detail fields."""
    def __init__(
        self, last_price, ask_price=None, bid_price=None,
        up_stop=0.0, down_stop=0.0, detail_error=False,
        after_hours=True, timetag="20260731 15:00:01",
    ):
        self._last = last_price
        self._ask = [] if ask_price is None else [ask_price]
        self._bid = [] if bid_price is None else [bid_price]
        self._up_stop = up_stop
        self._down_stop = down_stop
        self._detail_error = detail_error
        self._after_hours = after_hours
        # Settled close by default: these cases are about order handling, not
        # about the finality gate, and a stale stamp would defer every one.
        self._timetag = timetag

    def get_full_tick(self, codes):
        return {
            c: {
                "lastPrice": self._last,
                "askPrice": self._ask,
                "bidPrice": self._bid,
                "timetag": self._timetag,
            }
            for c in codes
        }

    def get_instrumentdetail(self, stock_code):
        if self._detail_error:
            raise RuntimeError("instrument detail unavailable")
        detail = {
            "UpStopPrice": self._up_stop,
            "DownStopPrice": self._down_stop,
        }
        if self._after_hours is not None:
            detail["IsAfterHoursTrading"] = self._after_hours
        return detail


def test_close_auction_limit_price_uses_daily_side_limit(bridge):
    ctx = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)
    assert bridge._instrument_limit_price(ctx, "000001.SZ", "BUY") == 11.0
    assert bridge._instrument_limit_price(ctx, "000001.SZ", "SELL") == 9.0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_close_auction_limit_price_fails_closed(bridge, side):
    with pytest.raises(ValueError, match="limit price"):
        bridge._instrument_limit_price(_TickCtx(10.0), "000001.SZ", side)


def test_persistent_log_appends_text_and_jsonl(bridge):
    bridge._log_event("START", message="first")
    bridge._log_event("START", message="second")
    day = bridge._today()
    rows = (Path(bridge.BRIDGE_ROOT) / "logs" /
            ("qmt_events_%s.jsonl" % day)).read_text().splitlines()
    assert [json.loads(row)["message"] for row in rows] == ["first", "second"]
    text_log = (Path(bridge.BRIDGE_ROOT) / "logs" /
                ("qmt_bridge_%s.log" % day)).read_text()
    assert "first" in text_log and "second" in text_log


def test_successful_passorder_return_redacts_secrets_and_account(
    bridge, monkeypatch,
):
    bridge.ACCOUNT_ID = "8890116049"
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order["quantity"] = 100
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8890116049", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: {
            "token": "return-token-value",
            "api_key": "return-api-key",
            "secret_token": "return-secret-token",
            "sendkey": "return-send-key",
            "credential_value": "return-credential-value",
            "session_id": "return-session-id",
            "nested": [{
                "cookie_value": "return-cookie-value",
                "auth_credential": "return-auth-credential",
                "password_hint": "return-password-hint",
            }],
            "returned_account_id": "8890116049",
            "status": "queued",
            "order_id": "qmt-order-101",
            "timestamp": "2026-08-08T15:05:00",
            "price": 10.50,
        },
        raising=False,
    )

    assert bridge._submit(object(), batch, order, True, limit_price=11.0)

    event_text = json.dumps(_read_events(bridge), sort_keys=True)
    text_log = (Path(bridge.BRIDGE_ROOT) / "logs" /
                ("qmt_bridge_%s.log" % bridge._today())).read_text()
    active = Path(bridge._active_state_path(BATCH_ID)).read_text()
    combined = event_text + text_log + active
    assert "return-token-value" not in combined
    assert "return-api-key" not in combined
    assert "return-secret-token" not in combined
    assert "return-send-key" not in combined
    assert "return-credential-value" not in combined
    assert "return-session-id" not in combined
    assert "return-cookie-value" not in combined
    assert "return-auth-credential" not in combined
    assert "return-password-hint" not in combined
    assert "8890116049" not in combined
    assert "88******49" in combined
    assert "REDACTED" in combined
    assert "queued" in combined
    assert "qmt-order-101" in combined
    assert "2026-08-08T15:05:00" in combined
    assert "10.5" in combined


def test_successful_error_persistence_redacts_external_message(
    bridge,
):
    bridge.ACCOUNT_ID = "8890116049"
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8890116049", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    bridge.g.batch = batch

    class Args:
        userOrderId = order["client_order_id"]
        orderCode = order["stock_code"]
        opType = 23

    bridge.orderError_callback(
        object(), Args(),
        "password=hunter2 token: bearer-value account_id=8890116049",
    )

    event_text = json.dumps(_read_events(bridge), sort_keys=True)
    fills_text = (Path(bridge._fills_path(BATCH_ID))).read_text()
    text_log = (Path(bridge.BRIDGE_ROOT) / "logs" /
                ("qmt_bridge_%s.log" % bridge._today())).read_text()
    combined = event_text + fills_text + text_log
    assert "hunter2" not in combined
    assert "bearer-value" not in combined
    assert "8890116049" not in combined
    assert "88******49" in combined
    assert "REDACTED" in combined


def test_external_message_redaction_uses_active_batch_account_context(
    bridge,
):
    bridge.ACCOUNT_ID = ""
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8890116049", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    bridge.g.batch = batch

    class Args:
        userOrderId = order["client_order_id"]
        orderCode = order["stock_code"]
        opType = 23

    bridge.orderError_callback(
        object(), Args(),
        "Authorization: Bearer fake-token-123; "
        "session credential fake-session-456; "
        "session fake-session-789; credential fake-credential-789; "
        "broker rejected account 8890116049",
    )

    persisted = "\n".join([
        json.dumps(_read_events(bridge), sort_keys=True),
        Path(bridge._fills_path(BATCH_ID)).read_text(),
        (Path(bridge.BRIDGE_ROOT) / "logs" /
         ("qmt_bridge_%s.log" % bridge._today())).read_text(),
        Path(bridge._active_state_path(BATCH_ID)).read_text(),
    ])
    assert "fake-token-123" not in persisted
    assert "fake-session-456" not in persisted
    assert "fake-session-789" not in persisted
    assert "fake-credential-789" not in persisted
    assert "8890116049" not in persisted
    assert "88******49" in persisted
    assert "REDACTED" in persisted


def test_contextual_redaction_preserves_prices_times_and_order_ids(bridge):
    bridge.ACCOUNT_ID = ""
    batch = _live_batch(bridge)
    bridge.g.batch = batch
    message = "price 10.50 at 15:05:00 order 20260714001001B"

    bridge._log_event("NUMERIC_EVIDENCE", message=message)

    event = _read_events(bridge)[-1]
    assert event["message"] == message
    text_log = (Path(bridge.BRIDGE_ROOT) / "logs" /
                ("qmt_bridge_%s.log" % bridge._today())).read_text()
    assert message in text_log


def test_successful_order_persists_complete_evidence_sequence(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order["quantity"] = 100
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8890116049", "account_environment": "SIMULATION",
        "schema_version": "2.0", "strategy_id": "probe",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    bridge.g.batch = batch

    class Account:
        m_strAccountID = "8890116049"
        m_dAvailable = 9990.0
        m_dFrozenCash = 10.0

    class Position:
        m_strInstrumentID = "000001"
        m_nVolume = 300
        m_nCanUseVolume = 200
        m_nFrozenVolume = 100

    def broker_query(account_id, account_type, kind):
        return [Account()] if kind == "ACCOUNT" else [Position()]

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", broker_query, raising=False,
    )

    def passorder_after_attempt(*args):
        assert _read_events(bridge)[-1]["event"] == "PASSORDER_ATTEMPT"
        return {"queued": True}

    monkeypatch.setattr(
        bridge, "passorder", passorder_after_attempt, raising=False,
    )
    assert bridge._submit(
        _TickCtx(10.50, up_stop=11.0), batch, order, True,
        limit_price=11.0,
    )
    assert batch.fills == {}

    class Working:
        m_strRemark = order["client_order_id"]
        m_strOrderSysID = "qmt-101"
        m_nOrderStatus = 48
        m_strOrderStatus = "reported"
        m_dOrderPrice = 11.0
        m_nOrderVolume = 100
        m_nVolumeTraded = 0
        m_nVolumeCanceled = 0
        m_strErrorMsg = ""
        m_strInstrumentID = "000001"

    class Filled(Working):
        m_nOrderStatus = bridge.STATUS_SUCCEEDED
        m_strOrderStatus = "filled"
        m_nVolumeTraded = 100
        m_dTradedPrice = 10.50

    queries = iter([
        {order["client_order_id"]: [Working()]},
        {order["client_order_id"]: [Filled()]},
    ])
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark", lambda account_id: next(queries),
    )
    bridge._poll_status(batch)
    assert batch.fills[order["client_order_id"]]["status"] == "ACCEPTED"
    bridge.order_callback(object(), Working())

    class Deal:
        m_strRemark = order["client_order_id"]
        m_strOrderSysID = "qmt-101"
        m_strDealID = "deal-9"
        m_nVolume = 100
        m_dPrice = 10.50
        m_nVolumeTraded = 100

    bridge.deal_callback(object(), Deal())
    bridge._poll_status(batch)

    events = _read_events(bridge)
    _assert_event_subsequence(events, [
        "SECURITY_DETAIL", "PREORDER_SNAPSHOT", "PASSORDER_ATTEMPT",
        "PASSORDER_RETURNED", "SUBMITTED_UNCONFIRMED", "ORDER_QUERY",
        "ORDER_OBSERVED", "ORDER_STATUS_CHANGED", "ORDER_CALLBACK",
        "DEAL_CALLBACK", "ORDER_FINALIZED",
    ])
    security = next(row for row in events
                    if row["event"] == "SECURITY_DETAIL")
    assert security["raw_fields"]["up_stop_price"] == 11.0
    preorder = next(row for row in events
                    if row["event"] == "PREORDER_SNAPSHOT")
    assert preorder["market"]["official_close"] == 10.50
    assert preorder["market"]["official_close_source"] == "lastPrice"
    assert preorder["broker"]["available_cash"] == 9990.0
    assert preorder["broker"]["can_use_shares"] == 200
    assert preorder["broker"]["frozen_shares"] == 100
    assert preorder["passorder_arguments"]["account_id_masked"] == (
        "88******49"
    )
    assert "account_id" not in preorder["passorder_arguments"]
    returned = next(row for row in events
                    if row["event"] == "PASSORDER_RETURNED")
    assert returned["return_repr"] == "{'queued': True}"
    assert returned["return_type"] == "dict"
    assert returned["elapsed_ms"] >= 0
    query = next(row for row in events if row["event"] == "ORDER_QUERY")
    assert query["result_count"] == 1
    assert query["match_count"] == 1
    assert query["query_number"] == 1
    assert query["elapsed_ms_since_attempt"] >= 0
    assert query["candidates"][0]["order_id"] == "qmt-101"
    final = next(row for row in events
                 if row["event"] == "ORDER_FINALIZED")
    assert final["api_returned"] is True
    assert final["order_observed"] is True
    assert final["callback_counts"]["order"] == 1
    assert final["callback_counts"]["deal"] == 1
    assert final["fill_status"] == "FILLED"


def test_normal_passorder_return_never_observed_finishes_error(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order["quantity"] = 100
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    monkeypatch.setattr(bridge, "passorder", lambda *args: None, raising=False)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})

    assert bridge._submit(object(), batch, order, True, limit_price=11.0)
    bridge._poll_status(batch)
    bridge._poll_status(batch)
    active_state = json.loads(
        Path(bridge._active_state_path(BATCH_ID)).read_text()
    )
    assert active_state["order_evidence"][
        order["client_order_id"]
    ]["query_count"] == 2
    monkeypatch.setattr(bridge, "_now_hms", lambda: bridge.FINALIZE_AT)
    bridge._force_finalize_if_near_close(object(), batch)

    events = _read_events(bridge)
    missing = [row for row in events
               if row["event"] == "ORDER_NOT_OBSERVED"]
    assert [row["query_number"] for row in missing] == [1, 2, 3]
    assert all(row["elapsed_ms_since_attempt"] >= 0 for row in missing)
    statuses = [row.get("status") for row in events
                if row["event"] == "ORDER_STATUS_CHANGED"]
    assert "ACCEPTED" not in statuses
    fills = _read_fills(bridge)
    assert fills[-1]["status"] == "ERROR"
    assert fills[-1]["message"] == "QMT order not observed after passorder"
    final = [row for row in events if row["event"] == "ORDER_FINALIZED"][-1]
    assert final["api_returned"] is True
    assert final["order_observed"] is False
    assert final["fill_status"] == "ERROR"
    assert final["reason"] == "QMT order not observed after passorder"


def test_runtime_batch_timer_and_account_snapshot_evidence_is_sanitized(
    bridge, monkeypatch,
):
    bridge.ACCOUNT_ID = "8890116049"
    context = _ScheduleContext()
    context.qmt_version = "QMT-test-1"
    bridge.init(context)
    _write_batch(
        bridge, bridge._today(), [_order()], mode="LIVE",
        account_id="8890116049",
    )
    bridge._claim_new_batch()

    class Account:
        m_strAccountID = "8890116049"
        m_dAvailable = 123.0
        m_dFrozenCash = 4.0
        m_dBalance = 1000.0

    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *args: [Account()] if args[2] == "ACCOUNT" else [],
        raising=False,
    )
    bridge.g.batch.execution_live = True
    bridge._write_account_snapshot(bridge.g.batch)

    events = _read_events(bridge)
    runtime = next(row for row in events if row["event"] == "RUNTIME_CONFIG")
    assert runtime["account_id_masked"] == "88******49"
    assert runtime["qmt_version"] == "QMT-test-1"
    assert runtime["source_version"]
    assert runtime["source_sha256"].startswith("sha256:")
    assert runtime["execution_profile"] == "CLOSE_AUCTION"
    assert runtime["max_order_quantity"] == 100
    assert "account_id" not in runtime
    timer = next(row for row in events if row["event"] == "TIMER_REGISTERED")
    assert timer["method"] == "schedule_run"
    assert timer["registered"] is True
    assert timer["first_wakeup"].endswith("145655")
    batch_event = next(row for row in events
                       if row["event"] == "BATCH_VALIDATED")
    assert batch_event["checksum_match"] is True
    assert batch_event["order_count"] == 1
    assert batch_event["account_id_masked"] == "88******49"
    snapshot = [row for row in events
                if row["event"] == "ACCOUNT_SNAPSHOT"][-1]
    assert snapshot["available_cash"] == 123.0
    assert snapshot["frozen_cash"] == 4.0
    assert snapshot["account_id_masked"] == "88******49"
    serialized = json.dumps(events, sort_keys=True)
    assert "8890116049" not in serialized
    assert "token" not in serialized.lower()
    exported_snapshot = (Path(bridge._account_snapshot_path(BATCH_ID))).read_text()
    assert "8890116049" not in exported_snapshot
    assert "88******49" in exported_snapshot


def test_jsonl_log_write_failure_recovers_without_raising(
    bridge, monkeypatch,
):
    real_append = bridge._append_log_line
    failures = {"remaining": 1}

    def fail_json_once(name, line):
        if name.startswith("qmt_events_") and failures["remaining"]:
            failures["remaining"] -= 1
            raise OSError("disk temporarily unavailable")
        return real_append(name, line)

    monkeypatch.setattr(bridge, "_append_log_line", fail_json_once)
    bridge._log_event("FIRST_DROPPED", secret_token="must-not-be-retained")
    bridge._log_event("SECOND_PERSISTED", message="ok")

    events = _read_events(bridge)
    assert [row["event"] for row in events] == [
        "SECOND_PERSISTED", "LOG_WRITE_RECOVERED",
    ]
    recovered = events[-1]
    assert recovered["failed_event"] == "FIRST_DROPPED"
    assert recovered["failure_count"] == 1
    assert len(json.dumps(recovered)) < 1024
    assert "must-not-be-retained" not in json.dumps(recovered)


def test_passorder_return_is_not_broker_acceptance(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    monkeypatch.setattr(bridge, "passorder", lambda *args: None, raising=False)

    assert bridge._submit(object(), batch, order, True, limit_price=11.0)
    assert batch.fills == {}
    events = (Path(bridge.BRIDGE_ROOT) / "logs" /
              ("qmt_events_%s.jsonl" % bridge._today())).read_text()
    assert "SUBMITTED_UNCONFIRMED" in events


def test_order_error_callback_persists_rejection(bridge):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    bridge.g.batch = bridge.Batch(header, [order])

    class Args:
        userOrderId = order["client_order_id"]
        orderCode = order["stock_code"]
        opType = 23

    bridge.orderError_callback(object(), Args(), "broker rejected")

    assert bridge.g.batch.fills[order["client_order_id"]]["status"] == "REJECTED"
    callback = next(row for row in _read_events(bridge)
                    if row["event"] == "ORDER_ERROR_CALLBACK")
    assert callback["client_order_id"] == order["client_order_id"]
    assert callback["error_message"] == "broker rejected"


@pytest.mark.parametrize("callback_kind,event_name", [
    ("order", "ORDER_CALLBACK"),
    ("deal", "DEAL_CALLBACK"),
    ("error", "ORDER_ERROR_CALLBACK"),
])
def test_wrong_remark_same_symbol_callback_never_associates(
    bridge, callback_kind, event_name,
):
    order = _order(coid="canonical-client-order", side="BUY", priority=20)
    order["quantity"] = 100
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    bridge.g.batch = batch

    class Callback:
        userOrderId = "foreign-client-order"
        m_strRemark = "foreign-client-order"
        orderCode = order["stock_code"]
        m_strInstrumentID = "000001"
        m_strOrderSysID = "foreign-qmt-id"
        m_nOrderStatus = 48
        m_nOrderVolume = 100
        m_nVolumeTraded = 100
        m_nVolume = 100
        m_dOrderPrice = 10.0
        m_dPrice = 10.0

    if callback_kind == "order":
        bridge.order_callback(object(), Callback())
    elif callback_kind == "deal":
        bridge.deal_callback(object(), Callback())
    else:
        bridge.orderError_callback(object(), Callback(), "foreign rejection")

    assert batch.order_evidence == {}
    assert batch.submitted == {}
    assert batch.fills == {}
    callback = [row for row in _read_events(bridge)
                if row["event"] == event_name][-1]
    assert callback["associated"] is False
    assert callback["batch_id"] == ""
    assert callback["client_order_id"] == ""
    assert callback["raw_remark"] == "foreign-client-order"


def test_callback_first_real_id_is_accepted_and_not_finalized_unobserved(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order["quantity"] = 100
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    batch.execution_live = True
    bridge.g.batch = batch
    monkeypatch.setattr(bridge, "passorder", lambda *args: None, raising=False)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    assert bridge._submit(object(), batch, order, True, limit_price=11.0)

    class Callback:
        m_strRemark = order["client_order_id"]
        m_strInstrumentID = "000001"
        m_strOrderSysID = "callback-qmt-1"
        m_nOrderStatus = 48
        m_nOrderVolume = 100
        m_nVolumeTraded = 0
        m_dOrderPrice = 11.0

    bridge.order_callback(object(), Callback())
    assert batch.order_evidence[order["client_order_id"]][
        "order_observed"
    ] is True
    assert batch.fills[order["client_order_id"]]["status"] == "ACCEPTED"

    bridge._poll_status(batch)
    monkeypatch.setattr(bridge, "_now_hms", lambda: bridge.FINALIZE_AT)
    bridge._force_finalize_if_near_close(object(), batch)

    fills = _read_fills(bridge)
    assert fills[-1]["status"] == "ERROR"
    assert fills[-1]["message"] == (
        "QMT order observed but final status unavailable at close"
    )
    assert all(fill["message"] != "QMT order not observed after passorder"
               for fill in fills)
    final = [row for row in _read_events(bridge)
             if row["event"] == "ORDER_FINALIZED"][-1]
    assert final["order_observed"] is True
    assert final["qmt_order_ids"] == ["callback-qmt-1"]


def test_init_binds_configured_account_for_callbacks(bridge):
    bridge.ACCOUNT_ID = "8890116049"

    class Context:
        def __init__(self):
            self.bound = []

        def set_account(self, account_id):
            self.bound.append(account_id)

        def schedule_run(self, *args):
            return 1

    context = Context()
    bridge.init(context)
    assert context.bound == ["8890116049"]


def test_official_close_uses_last_price_without_book_slippage(bridge):
    ctx = _TickCtx(10.50, ask_price=10.99, bid_price=9.01)

    assert bridge._official_close(ctx, "000001.SZ") == 10.50


@pytest.mark.parametrize("last_price", [0.0, float("nan")])
def test_official_close_fails_closed_without_positive_last_price(
    bridge, last_price,
):
    assert bridge._official_close(_TickCtx(last_price), "000001.SZ") == 0.0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_fixed_price_requires_positive_official_close_before_api(
    bridge, monkeypatch, tmp_path, side,
):
    probe_root, main_root = _profile_roots(
        tmp_path, "AFTER_HOURS_FIXED_PRICE",
    )
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE",
        probe_root, main_root,
    )
    order = _order(coid="20260714001001" + side[0], side=side)
    order["price_type"] = "AFTER_HOURS_CLOSE"
    order["quantity"] = 100
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: pytest.fail("invalid close reference reached passorder"),
        raising=False,
    )

    assert bridge._submit(_TickCtx(0.0), batch, order, True) is False
    assert batch.submitted == {order["client_order_id"]: True}
    assert batch.fills[order["client_order_id"]]["status"] == "ERROR"
    assert batch.fills[order["client_order_id"]]["message"] == (
        "official close unavailable"
    )


def test_fixed_price_passes_zero_and_logs_positive_close_reference_before_api(
    bridge, monkeypatch, tmp_path,
):
    probe_root, main_root = _profile_roots(
        tmp_path, "AFTER_HOURS_FIXED_PRICE",
    )
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE",
        probe_root, main_root,
    )
    order = _order(coid="20260714001001S", side="SELL")
    order.update(price_type="AFTER_HOURS_CLOSE", quantity=100)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    calls = []

    def passorder_after_state(*args):
        active = (
            Path(bridge.BRIDGE_ROOT) / "state" /
            ("active_" + BATCH_ID + ".json")
        )
        assert json.loads(active.read_text())["submitted"] == [
            order["client_order_id"],
        ]
        calls.append(args)

    monkeypatch.setattr(bridge, "passorder", passorder_after_state, raising=False)

    assert bridge._submit(_TickCtx(10.50), batch, order, True)

    assert len(calls) == 1
    assert calls[0][4] == 49
    assert calls[0][5] == 0.0
    events = [
        json.loads(row) for row in (
            Path(bridge.BRIDGE_ROOT) / "logs" /
            ("qmt_events_%s.jsonl" % bridge._today())
        ).read_text().splitlines()
    ]
    submitted = [
        row for row in events if row["event"] == "SUBMITTED_UNCONFIRMED"
    ]
    assert submitted[0]["official_close_reference"] == 10.50
    assert submitted[0]["limit_price"] == 0.0


@pytest.mark.parametrize("after_hours", [False, None])
def test_fixed_price_requires_positive_security_eligibility_before_api(
    bridge, monkeypatch, tmp_path, after_hours,
):
    probe_root, main_root = _profile_roots(
        tmp_path, "AFTER_HOURS_FIXED_PRICE",
    )
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", probe_root, main_root,
    )
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order.update(price_type="AFTER_HOURS_CLOSE", quantity=100)
    batch = bridge.Batch({
        "batch_id": BATCH_ID,
        "trade_date": bridge._today(),
        "mode": "LIVE",
        "account_id": "1",
        "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: pytest.fail("ineligible security reached passorder"),
        raising=False,
    )

    assert bridge._submit(
        _TickCtx(10.50, after_hours=after_hours), batch, order, True,
    ) is False

    fill = batch.fills[order["client_order_id"]]
    assert fill["status"] == "ERROR"
    assert fill["message"] == (
        "after-hours fixed-price eligibility not confirmed"
    )
    events = _read_events(bridge)
    _assert_event_subsequence(events, [
        "SECURITY_DETAIL", "SECURITY_ELIGIBILITY_ERROR", "ORDER_FINALIZED",
    ])
    assert not any(event["event"] == "PASSORDER_ATTEMPT" for event in events)


def test_fixed_price_buy_sizes_and_reserves_at_close_but_passes_zero(
    bridge, monkeypatch, tmp_path,
):
    probe_root, main_root = _profile_roots(
        tmp_path, "AFTER_HOURS_FIXED_PRICE",
    )
    _activate_profile(
        bridge, "AFTER_HOURS_FIXED_PRICE", probe_root, main_root,
    )
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    order.update(price_type="AFTER_HOURS_CLOSE", max_quantity=100)
    _write_batch(bridge, bridge._today(), [order], mode="LIVE")
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:30")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 10000.0)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert len(submitted) == 1
    assert submitted[0][4:7] == (49, 0.0, 100)
    assert bridge.g.batch.remaining_cash == pytest.approx(
        10000.0 - bridge._estimated_buy_cost(100, 10.0)
    )


def test_simulate_batch_processes_without_qmt_api(bridge, monkeypatch):
    """SIMULATE 模式下全流程不触碰 QMT API，直接产出 simulated 回执。"""
    bridge.TRADE_START = "00:00:00"  # 允许任何时间提交
    monkeypatch.setattr(bridge, "_now_hms", lambda: "14:57:30")
    _write_batch(bridge, bridge._today(), [
        _order(coid="20260714001S", side="SELL", priority=10),
        _order(coid="20260714002B", side="BUY", priority=20),
    ])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    batch.trading_started = True
    batch.phase_started = 100.0
    monkeypatch.setattr(bridge.time, "time", lambda: 341.0)
    bridge._process_batch(_TickCtx(10.0), batch)
    # SIMULATE：两单都 SKIPPED simulated，批次终结
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills["20260714001S"]["status"] == "SKIPPED"
    assert fills["20260714001S"]["message"] == "simulated"
    assert fills["20260714002B"]["message"] == "simulated"
    assert fills["20260714002B"]["requested_qty"] == 800
    assert bridge.g.batch is None  # finalized
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert not list((Path(bridge.BRIDGE_ROOT) / "processing").glob("signal_*"))
    assert len(list((Path(bridge.BRIDGE_ROOT) / "archive").glob("signal_*"))) == 2


class _OrderDetail:
    def __init__(self, sysid, status, traded, price):
        self.m_strOrderSysID = sysid
        self.m_nOrderStatus = status
        self.m_nVolumeTraded = traded
        self.m_dTradedPrice = price


def test_summarize_remark_orders_aggregates_star_board_split(bridge):
    """科创板拆单：同 remark 多合同编号应汇总成交量/均价。"""
    details = [
        _OrderDetail("175", bridge.STATUS_SUCCEEDED, 100000, 4.14),
        _OrderDetail("176", bridge.STATUS_SUCCEEDED, 100000, 4.14),
        _OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14),
    ]
    summary = bridge._summarize_remark_orders(details, 244500)
    assert summary["fill_status"] == "FILLED"
    assert summary["traded"] == 244500
    assert summary["avg_price"] == pytest.approx(4.14)
    assert summary["qmt_order_id"] == "175,176,177"


def test_summarize_remark_orders_does_not_fill_on_single_child(bridge):
    """仅一笔子单已成且数量不足时，不得误标 FILLED。"""
    details = [_OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14)]
    summary = bridge._summarize_remark_orders(details, 244500)
    assert summary["fill_status"] == "ACCEPTED"
    assert summary["traded"] == 44500
    assert "partial" in summary["message"]


def test_poll_status_sums_split_children(bridge, monkeypatch):
    order = _order(coid="20260714001004B", side="BUY", priority=20)
    order["quantity"] = 244500
    order["stock_code"] = "688223.SH"
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {
            order["client_order_id"]: [
                _OrderDetail("175", bridge.STATUS_SUCCEEDED, 100000, 4.14),
                _OrderDetail("176", bridge.STATUS_SUCCEEDED, 100000, 4.14),
                _OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14),
            ],
        },
    )

    bridge._poll_status(batch)

    fill = batch.fills[order["client_order_id"]]
    assert fill["status"] == "FILLED"
    assert fill["filled_qty"] == 244500
    assert fill["qmt_order_id"] == "175,176,177"


def test_order_query_preserves_more_than_fifty_candidates(bridge):
    order = _order(coid="20260714001004B", side="BUY", priority=20)
    order["quantity"] = 6000
    batch = bridge.Batch({
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    details = [
        _OrderDetail("qmt-%03d" % index, -1, 0, 0.0)
        for index in range(60)
    ]

    bridge._poll_status(batch, {order["client_order_id"]: details})

    query = next(row for row in _read_events(bridge)
                 if row["event"] == "ORDER_QUERY")
    assert query["match_count"] == 60
    assert len(query["candidates"]) == 60
    assert query["candidates"][-1]["order_id"] == "qmt-059"


def test_force_finalize_cancels_all_split_children(bridge, monkeypatch):
    order = _order(coid="20260714001004B", side="BUY", priority=20)
    order["quantity"] = 244500
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    batch.fills[order["client_order_id"]] = {
        "status": "ACCEPTED", "filled_qty": 100000, "avg_price": 4.14,
    }
    canceled = []

    class Working:
        m_strOrderSysID = "175"
        m_nOrderStatus = bridge.STATUS_PART_SUCC
        m_nVolumeTraded = 100000
        m_dTradedPrice = 4.14

    class Working2:
        m_strOrderSysID = "176"
        m_nOrderStatus = -1
        m_nVolumeTraded = 0
        m_dTradedPrice = 0.0

    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:00:10")
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {
            order["client_order_id"]: [Working(), Working2()],
        },
    )
    monkeypatch.setattr(bridge, "can_cancel_order", lambda *args: True, raising=False)
    monkeypatch.setattr(
        bridge, "cancel",
        lambda order_id, *args: canceled.append(order_id), raising=False,
    )

    bridge._force_finalize_if_near_close(object(), batch)

    assert set(canceled) == {"175", "176"}


class _AccountRow:
    m_strAccountID = "8881352838"
    m_dAvailable = 123456.78
    m_dBalance = 9876543.21
    m_dInstrumentValue = 9000000.0
    m_dFrozenCash = 0.0


class _PositionRow:
    def __init__(self, symbol, exchange, volume, can_use=0, cost=1.0):
        self.m_strInstrumentID = symbol
        self.m_strExchangeID = exchange
        self.m_nVolume = volume
        self.m_nCanUseVolume = can_use
        self.m_dOpenPrice = cost
        self.m_dMarketValue = volume * cost


def _read_account_snapshot(bridge, batch_id=BATCH_ID):
    p = (Path(bridge.BRIDGE_ROOT) / "outbound" / ("account_%s.jsonl" % batch_id))
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _live_batch(bridge, batch_id=BATCH_ID):
    header = {
        "batch_id": batch_id, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8881352838", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [_order()])
    batch.execution_live = True
    return batch


def test_finalize_writes_broker_account_snapshot(bridge, monkeypatch):
    batch = _live_batch(bridge)

    def fake_query(account_id, account_type, kind):
        if kind == "ACCOUNT":
            return [_AccountRow()]
        return [
            _PositionRow("688223", "SH", 244500, cost=4.14),
            _PositionRow("300308", "SZ", 1100, can_use=1100, cost=1072.36),
            _PositionRow("600000", "", 0),  # 空持仓不入快照
        ]

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", fake_query, raising=False)

    bridge._finalize_batch(batch)

    rows = _read_account_snapshot(bridge)
    account = [r for r in rows if r["type"] == "account_snapshot"]
    positions = {r["stock_code"]: r for r in rows if r["type"] == "broker_position"}
    assert len(account) == 1
    assert account[0]["available_cash"] == pytest.approx(123456.78)
    assert set(positions) == {"688223.SH", "300308.SZ"}
    assert positions["688223.SH"]["shares"] == 244500
    assert positions["300308.SZ"]["can_use_volume"] == 1100
    # .done 与回执一致，Mac 侧只读完整文件
    assert (Path(bridge.BRIDGE_ROOT) / "outbound" /
            ("account_%s.done" % BATCH_ID)).exists()
    assert not _marker_path(bridge).exists()


def test_account_snapshot_event_preserves_more_than_fifty_positions(
    bridge, monkeypatch,
):
    batch = _live_batch(bridge)
    positions = [
        _PositionRow(str(600000 + index), "SH", 100 + index)
        for index in range(60)
    ]

    def fake_query(account_id, account_type, kind):
        return [_AccountRow()] if kind == "ACCOUNT" else positions

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", fake_query, raising=False,
    )

    bridge._write_account_snapshot(batch)

    event = [row for row in _read_events(bridge)
             if row["event"] == "ACCOUNT_SNAPSHOT"][-1]
    assert event["position_count"] == 60
    assert len(event["positions"]) == 60
    assert event["positions"][-1]["stock_code"] == "600059.SH"


def test_snapshot_failure_does_not_block_finalize(bridge, monkeypatch):
    batch = _live_batch(bridge)

    def boom(*args):
        raise RuntimeError("ACCOUNT query unavailable")

    monkeypatch.setattr(bridge, "get_trade_detail_data", boom, raising=False)

    bridge._finalize_batch(batch)

    assert batch.finalized
    assert (Path(bridge.BRIDGE_ROOT) / "outbound" /
            ("fills_%s.done" % BATCH_ID)).exists()


def test_simulate_batch_writes_no_broker_snapshot(bridge, monkeypatch):
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "SIMULATE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [_order()])
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *args: pytest.fail("SIMULATE must not query the broker"),
        raising=False,
    )

    bridge._finalize_batch(batch)

    assert _read_account_snapshot(bridge) == []
    assert not _marker_path(bridge).exists()


def _marker_path(bridge, batch_id=BATCH_ID):
    return (Path(bridge.BRIDGE_ROOT) / "state" /
            ("snapshot_refresh_%s.json" % batch_id))







@pytest.mark.parametrize("symbol,exchange,expected", [
    ("688223", "SH", "688223.SH"),
    ("300308", "SZ", "300308.SZ"),
    ("600000", "SHSE", "600000.SH"),
    ("000001", "SZSE", "000001.SZ"),
    ("688223", "", "688223.SH"),   # 交易所字段缺失时按代码前缀推断
    ("300308", "", "300308.SZ"),
    ("920001", "", "920001.SH"),
])
def test_qmt_stock_code_suffix(bridge, symbol, exchange, expected):
    assert bridge._qmt_stock_code(symbol, exchange) == expected


def _backtest_shares(target_value, close_price, factor=1.0, trade_unit=100):
    """qlib/backtest/exchange.py round_amount_by_trade_unit 的真实股数。

    回测传的是复权价，返回值也是复权口径，乘回 factor 才是真实股数；
    raw_close = adj_close / factor，所以 factor 完全约掉。这里直接用未复权价。
    """
    adjusted = target_value / (close_price * factor)
    return (adjusted * factor + 0.1) // trade_unit * trade_unit


@pytest.mark.parametrize(
    "target_value,close_price",
    [
        (60_000.0, 10.0),      # 整好 6000 股
        (60_000.0, 13.37),     # 普通零头
        (2_999.5, 10.0),       # V/C = 299.95：+0.1 抬进下一手的临界窗口
        (29_995.0, 100.0),     # 同一临界窗口，另一个价位
        (1_000_000.0, 3.01),
        (999.0, 10.0),         # 不足一手
    ],
)
def test_buy_sizing_equals_the_backtest_share_for_share(
    bridge, target_value, close_price,
):
    assert bridge._ladder_buy_shares(target_value, close_price) == int(
        _backtest_shares(target_value, close_price)
    )


def test_the_missing_epsilon_would_have_cost_a_whole_lot(bridge):
    """锁住 +0.1：丢掉它时 V/C=299.95 会算成 200 股而不是 300 股。"""
    assert bridge._ladder_buy_shares(2_999.5, 10.0) == 300
    assert int(2_999.5 / 10.0 / 100.0) * 100 == 200


@pytest.mark.parametrize(
    "stock_code,expected",
    [
        ("600000.SH", 100),
        ("000001.SZ", 100),
        ("300750.SZ", 100),
        ("688111.SH", 200),
    ],
)
def test_board_minimum_declaration_size(bridge, stock_code, expected):
    assert bridge._board_min_shares(stock_code) == expected


def test_star_market_below_two_hundred_shares_is_zeroed(bridge):
    # 科创板盘后固定价单笔买入不得少于 200 股
    assert bridge._sized_buy_shares("688111.SH", 1_500.0, 10.0) == 0
    assert bridge._sized_buy_shares("688111.SH", 2_500.0, 10.0) == 200
    # 主板同样金额照常成单
    assert bridge._sized_buy_shares("600000.SH", 1_500.0, 10.0) == 100


def test_star_market_above_the_floor_is_still_a_lot_multiple(bridge):
    # 与回测 trade_unit=100 一致：200 股门槛之上仍取 100 的整数倍
    assert bridge._sized_buy_shares("688111.SH", 35_000.0, 10.0) == 3_500


def test_missing_close_price_sizes_to_zero_never_guesses(bridge):
    assert bridge._ladder_buy_shares(60_000.0, 0.0) == 0
    assert bridge._sized_buy_shares("600000.SH", 60_000.0, 0.0) == 0


def test_odd_lot_sell_batch_is_accepted(bridge, monkeypatch, tmp_path):
    """零股来自 absorb_broker_excess 吸收的送股。阶梯到期时整层一次性卖出，
    含零股的层同样合规——bridge 不能因为不是整百就整批拒收。"""
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    order = _order(coid="20260714001001S", side="SELL", priority=10)
    order["price_type"] = "AFTER_HOURS_CLOSE"
    order["quantity"] = 120
    _write_batch(bridge, bridge._today(), [order])

    bridge._claim_new_batch()

    assert bridge.g.batch is not None
    assert bridge.g.batch.orders[0]["quantity"] == 120


@pytest.mark.parametrize(
    "sell_shares,buy_shares,side,quantity,transferred",
    [
        (300, 500, "BUY", 200, 300),    # B > S：净买
        (500, 300, "SELL", 200, 300),   # B < S：净卖
        (300, 300, None, 0, 300),       # B == S：无单，全部转记
        (300, 0, "SELL", 300, 0),       # 不在今日 top3 / 科创板被置 0
        (0, 300, "BUY", 300, 0),        # 无到期层
    ],
)
def test_netting_arithmetic(
    bridge, sell_shares, buy_shares, side, quantity, transferred,
):
    assert bridge._net_ladder_pair(sell_shares, buy_shares) == (
        side, quantity, transferred,
    )


@pytest.mark.parametrize("quantity", [0, -100, 100.5, True, None])
def test_non_positive_or_non_integer_sell_quantity_is_still_rejected(
    bridge, monkeypatch, tmp_path, quantity,
):
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    order = _order(coid="20260714001001S", side="SELL", priority=10)
    order["price_type"] = "AFTER_HOURS_CLOSE"
    order["quantity"] = quantity
    _write_batch(bridge, bridge._today(), [order])

    bridge._claim_new_batch()

    assert bridge.g.batch is None


def _ladder_batch(bridge, sell_qty, target_value, code="000001.SZ"):
    """一张同名到期卖 + 当日买的批次，用于抵销接线测试。

    sell_qty <= 0 时只放 BUY，用于「无到期层」的情形。
    """
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    for order in (sell, buy):
        order["price_type"] = "AFTER_HOURS_CLOSE"
        order["stock_code"] = code
    sell["quantity"] = sell_qty
    buy["target_value"] = target_value
    orders = [sell, buy] if sell_qty > 0 else [buy]
    _write_batch(bridge, bridge._today(), orders, mode="LIVE")
    return sell, buy


def _ladder_ticks(bridge, ctx, ticks=2):
    """跑批次直到收尾，第一个 tick 之后把时钟推过卖单截止时点。

    这些用例只关心抵销的算术与接线，不关心买单是被卖单终态触发的还是被
    截止兜底触发的。真提交的卖腿在测试里永远不会成交，所以走兜底那条路。
    monkeypatch 记的是原值，这里直接改属性不影响它 teardown 时还原。
    """
    for index in range(ticks):
        batch = bridge.g.batch
        if batch is None:
            return
        bridge._process_batch(ctx, batch)
        if index == 0:
            bridge._now_hms = lambda: "15:09:30"


def _run_after_hours(bridge, monkeypatch, tmp_path, now="15:05:30"):
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    bridge.ENABLE_LADDER_NETTING = True
    bridge.MAX_ORDER_QUANTITY = 0
    monkeypatch.setattr(bridge, "_now_hms", lambda: now)
    monkeypatch.setattr(bridge, "_get_can_use_volume", lambda *a: 100_000)
    monkeypatch.setattr(
        bridge, "_get_available_cash", lambda account_id: 10_000_000.0)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    return submitted


def _write_terminal_fill(bridge, batch, order):
    bridge._write_fill(batch, order, "FILLED", int(order["quantity"]), 10.0,
                       "q1", "filled")


def _activate_profile_only(bridge, profile):
    bridge.EXECUTION_PROFILE = profile
    bridge._activate_profile_settings()


def test_net_buy_submits_only_the_difference_and_skips_the_sell_leg(
    bridge, monkeypatch, tmp_path,
):
    # C = 10.0, V = 5000 -> B = 500; S = 300 -> net BUY 200, transferred 300
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    sides = [args[0] for args in submitted]
    assert sides == [23]                       # 23 = BUY, no SELL passorder
    assert submitted[0][6] == 200              # quantity argument
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    sell_fill = fills["20260714001001S"]
    assert sell_fill["status"] == "SKIPPED"
    assert sell_fill["netted_qty"] == 300
    assert sell_fill["filled_qty"] == 0


def test_net_sell_submits_the_residual_and_skips_the_buy_leg(
    bridge, monkeypatch, tmp_path,
):
    # C = 10.0, V = 3000 -> B = 300; S = 500 -> net SELL 200, transferred 300
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=500, target_value=3_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert [args[0] for args in submitted] == [24]     # 24 = SELL
    assert submitted[0][6] == 200
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert fills["20260714001002B"]["status"] == "SKIPPED"
    assert fills["20260714001002B"]["netted_qty"] == 300
    assert fills["20260714001002B"]["requested_qty"] == 300


def test_exact_offset_submits_nothing_and_transfers_everything(
    bridge, monkeypatch, tmp_path,
):
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=3_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert submitted == []
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert all(row["netted_qty"] == 300 for row in fills.values())
    assert bridge.g.batch is None      # 全部终态，批次已收尾


def test_receipts_carry_the_close_the_bridge_sized_on(
    bridge, monkeypatch, tmp_path,
):
    """定价证据必须随回执离开 Windows：Mac 侧的次日对账只有这一条路。

    netting_close 本来只写进 bridge 本地的 active_*.json，Mac 没有任何 reader。
    """
    _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert fills
    for row in fills.values():
        assert row["netting_close"] == 10.0


def test_intended_qty_is_the_pre_netting_ladder_target(
    bridge, monkeypatch, tmp_path,
):
    """intended_qty 是阶梯本意要的股数，与抵销无关。

    C = 10.0, V = 5000 -> B = 500；S = 300 -> net BUY 200, transferred 300。
    买腿只往市场送 200，但本意是 500——这个差别只在「市场腿」上看得见，所以要把
    它推到终态才有回执。requested_qty 顶不了成交率分母这个位置，就是因为它在这里
    是 200：拿它当分母，抵销掉的 300 股会让比率算出 250%。
    """
    _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    batch = bridge.g.batch
    assert batch is not None, "买腿还没终态，批次不该收尾"
    buy_order = next(o for o in batch.orders if o["side"] == "BUY")
    _write_terminal_fill(bridge, batch, buy_order)

    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    buy = fills["20260714001002B"]
    sell = fills["20260714001001S"]
    assert buy["requested_qty"] == 200
    assert buy["intended_qty"] == 500
    assert sell["intended_qty"] == 300


def test_a_fully_offset_pair_reports_intent_on_both_legs(
    bridge, monkeypatch, tmp_path,
):
    """完全抵销：两腿都没走市场，但本意各是 300 股，成交率必须算成 100%。"""
    _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=3_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert len(fills) == 2
    for row in fills.values():
        assert row["intended_qty"] == 300
        assert row["netted_qty"] == 300
        assert row["intended_qty"] >= row["netted_qty"]


def test_receipts_without_netting_fall_back_to_requested_intent(
    bridge, monkeypatch, tmp_path,
):
    """没抵销过的批次（如 TopkDropout）：intended 退化为 requested，close 留 0。"""
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge.ENABLE_LADDER_NETTING = False
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert submitted, "关掉抵销后两腿都该真下单"
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    for row in fills.values():
        assert row["netting_close"] == 0.0
        assert row["intended_qty"] == row["requested_qty"]


def test_odd_lot_due_amount_is_never_netted(bridge, monkeypatch, tmp_path):
    """S=120 时 net=B-S 不是整百，买入不允许非整百。该票整体走不抵销的老路，
    两腿都正常下单，代价是那一次的往返费。"""
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=120, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert sorted(args[0] for args in submitted) == [23, 24]
    assert all(row["netted_qty"] == 0 for row in _read_fills(bridge))


def test_netting_decision_is_frozen_across_a_restart(bridge, monkeypatch, tmp_path):
    """C 只在提交时刻读一次。重启后重算可能拿到不同的 B，而卖腿可能已经
    按旧决策提交过——两个不自洽的 B 会让转记股数与实际下单量对不上。"""
    _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()
    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0), ticks=1)
    assert bridge.g.batch is not None, "SELL 阶段结束批次应仍在处理中"
    frozen = [dict(o) for o in bridge.g.batch.orders]

    bridge.g.batch = None
    bridge._recover_processing_batch()
    bridge.g.batch.phase_started = 1.0
    # 价格变了也不该改变已冻结的决策
    _ladder_ticks(bridge, _TickCtx(20.0, up_stop=22.0, down_stop=18.0))

    for before, after in zip(frozen, bridge.g.batch.orders):
        assert before["netted_qty"] == after["netted_qty"]
        assert before["net_quantity"] == after["net_quantity"]
        assert before["netting_close"] == after["netting_close"]


def test_ladder_net_event_records_every_buy_with_its_close_and_read_time(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.7.1 的次日逐单对账要拿 C 与读取时刻。非重叠买单也要有，
    否则兜底路径下用错价的单子对不上账。"""
    _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    events = [e for e in _read_events(bridge) if e["event"] == "LADDER_NET"]
    assert len(events) == 1
    event = events[0]
    assert event["due_shares"] == 300
    assert event["target_value"] == 5_000.0
    assert event["official_close"] == 10.0
    assert event["official_close_read_at"]
    assert event["sized_shares"] == 500
    assert event["net_side"] == "BUY"
    assert event["net_quantity"] == 200
    assert event["transferred_shares"] == 300


def test_netting_is_off_by_default_so_close_auction_is_untouched(
    bridge, monkeypatch, tmp_path,
):
    assert bridge.ENABLE_LADDER_NETTING is False
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert sorted(args[0] for args in submitted) == [23, 24]
    assert all(row["netted_qty"] == 0 for row in _read_fills(bridge))


def test_broker_cash_still_caps_the_frozen_net_quantity(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.7.2 的职责划分：Mac 防欠配、bridge 防超买。冻结 B 之后
    券商现金封顶必须照旧生效，否则 Mac 把预算算大就会真的超买。"""
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 2_100.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    # B = 500 股，但 2100 元只够 200 股（含佣金与过户费）
    assert submitted and submitted[0][6] == 200


def test_frozen_quantity_below_affordable_floor_is_skipped_not_shrunk_to_zero(
    bridge, monkeypatch, tmp_path,
):
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 50.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(10.0, up_stop=11.0, down_stop=9.0))

    assert submitted == []
    fill = _read_fills(bridge)[0]
    assert fill["status"] == "SKIPPED"
    assert "insufficient actual cash" in fill["message"]


def test_unavailable_close_errors_the_order_and_never_guesses(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.4 边界情形最后一行：收盘价取不到就整单 ERROR，不猜价、不下单，
    该层变薄。_plan_ladder_netting 不冻结任何决策，BUY 阶段走现有 ERROR 分支。"""
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    monkeypatch.setattr(bridge, "_official_close", lambda ctx, code: 0.0)
    bridge._claim_new_batch()

    _ladder_ticks(bridge, _TickCtx(0.0))

    assert submitted == []
    fill = _read_fills(bridge)[0]
    assert fill["status"] == "ERROR"
    assert fill["message"] == "official close unavailable"


def test_buy_phase_starts_as_soon_as_every_sell_is_terminal(
    bridge, monkeypatch, tmp_path,
):
    """现有代码算出了 sells_done 却只用来打日志，于是卖单 30 秒成交也要
    干等满超时，买单错过最好的队列位置。"""
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:05:30")
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    ctx = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)

    bridge._process_batch(ctx, batch)
    # 卖单已提交并被回执标记终态
    for order in batch.orders:
        if order["side"] == "SELL":
            _write_terminal_fill(bridge, batch, order)
    bridge._process_batch(ctx, batch)

    assert batch.phase == "BUY"


def test_sell_phase_holds_while_sells_are_still_open_before_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:05:30")
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)

    assert batch.phase == "SELL"


def test_sell_timeout_is_an_absolute_clock_time_not_a_relative_duration(
    bridge, monkeypatch, tmp_path,
):
    """提交提前到 15:00:05 后，240 秒相对超时会在 15:04 前后触发——撮合
    (15:05) 还没开始、一笔卖单都不可能成交，买单于是按快照现金发出，
    spec 4.7.2 的欠配照旧。超时必须从撮合开始起算。"""
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:06:00")
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    # 相对时长早已耗尽（phase_started 在很久以前）
    monkeypatch.setattr(bridge.time, "time", lambda: batch.phase_started + 10_000)
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)
    assert batch.phase == "SELL", "15:06 还没到卖单截止，不该转 BUY"

    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:09:00")
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)
    assert batch.phase == "BUY"


def test_after_hours_sell_deadline_is_four_minutes_past_the_match_start(bridge):
    _activate_profile_only(bridge, "AFTER_HOURS_FIXED_PRICE")
    assert bridge.SELL_DEADLINE == "15:09:00"


def test_close_auction_never_waits_for_sells(bridge):
    _activate_profile_only(bridge, "CLOSE_AUCTION")
    assert bridge.SELL_DEADLINE == "14:57:05"


@pytest.mark.parametrize(
    "timetag,expected",
    [
        ("20260731 15:00:00", True),
        ("20260731 15:00:03", True),
        ("20260731 14:56:50", False),
        ("20260731 14:59:59", False),
        ("15:00:01", True),
        ("", None),
        (None, None),
        ("garbage", None),
        (20260731150000, None),
    ],
)
def test_close_finality_from_tick_timetag(bridge, timetag, expected):
    assert bridge._close_is_final({"timetag": timetag}) is expected


def test_batch_finality_requires_every_name_and_reports_unknown(bridge):
    class Ctx:
        def __init__(self, tags):
            self._tags = tags

        def get_full_tick(self, codes):
            return {c: {"timetag": self._tags[c]} for c in codes}

    batch = type("B", (), {"orders": [
        {"stock_code": "600000.SH"}, {"stock_code": "000001.SZ"},
    ]})()

    both_final = Ctx({"600000.SH": "20260731 15:00:01",
                      "000001.SZ": "20260731 15:00:02"})
    assert bridge._batch_close_is_final(both_final, batch) is True

    one_stale = Ctx({"600000.SH": "20260731 15:00:01",
                     "000001.SZ": "20260731 14:56:50"})
    assert bridge._batch_close_is_final(one_stale, batch) is False

    # 一只有信号一只没有：按未终态处理，等到兜底时点
    partial = Ctx({"600000.SH": "20260731 15:00:01", "000001.SZ": ""})
    assert bridge._batch_close_is_final(partial, batch) is False

    # 全都没有信号：QMT 不暴露该字段，退化成固定兜底
    none_at_all = Ctx({"600000.SH": "", "000001.SZ": None})
    assert bridge._batch_close_is_final(none_at_all, batch) is None


def test_after_hours_profile_attempts_from_fifteen_hundred_oh_five(bridge):
    _activate_profile_only(bridge, "AFTER_HOURS_FIXED_PRICE")
    assert bridge.TRADE_START == "15:00:05"
    assert bridge.SUBMIT_DEADLINE == "15:01:00"
    assert bridge._profile_settings()["timer_start"] == "14:59:55"


def test_close_auction_gate_never_engages(bridge):
    _activate_profile_only(bridge, "CLOSE_AUCTION")
    assert bridge.SUBMIT_DEADLINE == bridge.TRADE_START == "14:57:05"


def test_stale_close_defers_submission_until_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    """15:00:05 起试，但收盘价还没终态就不能定量——那会用 14:57 的冻结价
    算出错的股数（spec 4.7.1 的尾部风险）。"""
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:06")
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    bridge._claim_new_batch()
    batch = bridge.g.batch
    stale = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)
    monkeypatch.setattr(bridge, "_get_tick",
                        lambda ctx, code: {"lastPrice": 10.0,
                                           "timetag": "20260731 14:56:50"})
    bridge._process_batch(stale, batch)
    assert submitted == []
    assert batch.trading_started is False

    events = [e for e in _read_events(bridge) if e["event"] == "CLOSE_FINALITY_WAIT"]
    assert events

    # 到兜底时点，按现行 official_close > 0 门禁照常提交
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:01:00")
    bridge._process_batch(stale, batch)
    assert submitted


def test_final_close_submits_immediately_without_waiting_for_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:06")
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    monkeypatch.setattr(bridge, "_get_tick",
                        lambda ctx, code: {"lastPrice": 10.0,
                                           "timetag": "20260731 15:00:01"})
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)
    assert submitted


def test_market_price_evidence_records_the_timetag(bridge):
    """兜底路径的对账要靠这个字段；不采集就无法把静默错价变成 CRIT。"""
    ctx = _TickCtx(10.0)
    evidence = bridge._market_price_evidence(ctx, "600000.SH", 10.0)
    assert "timetag" in evidence["tick_fields"]




