"""信号桥接文件协议：数据对象、校验、checksum。

协议定义见 docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md §5。
JSON Lines 单文件：首行 batch_header，后续每行一个 order。
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, asdict, fields

SCHEMA_VERSION = "2.0"

VALID_SIDES = {"BUY", "SELL"}
VALID_PRICE_TYPES = {"CLOSE_AUCTION_LIMIT", "AFTER_HOURS_CLOSE"}
VALID_MODES = {"SIMULATE", "LIVE"}
VALID_ACCOUNT_ENVIRONMENTS = {"SIMULATION", "REAL"}
VALID_FILL_STATUS = {
    "ACCEPTED", "FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR",
}
TERMINAL_FILL_STATUS = {"FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR"}

TRADE_UNIT = 100
CLIENT_ORDER_ID_MAX_LEN = 24

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SchemaError(ValueError):
    """协议校验失败。"""


def _to_json_line(obj, type_name: str) -> str:
    d = {"type": type_name}
    d.update(asdict(obj))
    return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _from_dict(cls, d: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True)
class BatchHeader:
    batch_id: str
    strategy_id: str
    trade_date: str
    signal_date: str
    account_id: str
    account_type: str
    account_environment: str
    mode: str
    created_at: str
    order_count: int
    checksum: str
    schema_version: str = SCHEMA_VERSION

    def to_json_line(self) -> str:
        return _to_json_line(self, "batch_header")

    @classmethod
    def from_dict(cls, d: dict) -> "BatchHeader":
        return _from_dict(cls, d)


@dataclass(frozen=True)
class SignalOrder:
    batch_id: str
    client_order_id: str
    stock_code: str
    side: str
    quantity: int
    target_value: float
    price_type: str
    limit_price: float
    priority: int
    instrument_qlib: str
    reason: str
    # BUYs normally use 0 (or omit the legacy field).  A positive whole-lot
    # value is an immutable broker-side ceiling for explicitly authorized
    # operator orders.
    max_quantity: int = 0

    def to_json_line(self) -> str:
        # Schema v2.0 predates max_quantity.  Keep bytes (and therefore
        # checksums/durable retries) exactly stable for ordinary uncapped
        # orders; capped operator BUYs explicitly carry their authorization.
        d = {"type": "order"}
        d.update(asdict(self))
        if d.get("max_quantity") in (0, None):
            d.pop("max_quantity")
        return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict) -> "SignalOrder":
        return _from_dict(cls, d)


@dataclass(frozen=True)
class FillEvent:
    batch_id: str
    client_order_id: str
    mode: str
    stock_code: str
    side: str
    status: str
    requested_qty: int
    filled_qty: int
    avg_price: float
    qmt_order_id: str
    message: str
    ts: str
    # 被同名当日买卖抵销掉、因而没有走市场的股数（见 spec 4.4）。
    # 走独立字段而不是伪造成交额：伪造会让 apply_fill 重新计费，
    # 正好抹掉抵销省下的那 0.092%。
    netted_qty: int = 0

    def to_json_line(self) -> str:
        return _to_json_line(self, "fill_event")

    @classmethod
    def from_dict(cls, d: dict) -> "FillEvent":
        return _from_dict(cls, d)


def make_client_order_id(
    trade_date: str, batch_seq: int, order_seq: int, side: str,
) -> str:
    """生成包含批次序号的全局唯一 QMT remark（≤24 字符）。"""
    if side not in VALID_SIDES:
        raise ValueError(f"invalid side: {side!r}")
    if not (1 <= batch_seq <= 999):
        raise ValueError(f"batch_seq out of range [1, 999]: {batch_seq}")
    if not (1 <= order_seq <= 999):
        raise ValueError(f"order_seq out of range [1, 999]: {order_seq}")
    compact = trade_date.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid trade_date: {trade_date!r}")
    coid = f"{compact}{batch_seq:03d}{order_seq:03d}{side[0]}"
    assert len(coid) <= CLIENT_ORDER_ID_MAX_LEN
    return coid


def compute_checksum(order_lines: list) -> str:
    """对全部 order 行（JSON 字符串）按顺序拼接 UTF-8 字节做 sha256。"""
    h = hashlib.sha256()
    for line in order_lines:
        h.update(line.encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


def validate_order(order: SignalOrder) -> None:
    if order.side not in VALID_SIDES:
        raise SchemaError(f"invalid side: {order.side!r}")
    if order.price_type not in VALID_PRICE_TYPES:
        raise SchemaError(
            "price_type must be CLOSE_AUCTION_LIMIT or AFTER_HOURS_CLOSE: "
            f"{order.price_type!r}"
        )
    if (
        isinstance(order.limit_price, bool)
        or not isinstance(order.limit_price, (int, float))
        or not math.isfinite(float(order.limit_price))
        or float(order.limit_price) != 0.0
    ):
        raise SchemaError(
            "limit_price must be 0 for QMT close-auction resolution: "
            f"{order.limit_price!r}"
        )
    if (
        isinstance(order.target_value, bool)
        or not isinstance(order.target_value, (int, float))
        or not math.isfinite(float(order.target_value))
    ):
        raise SchemaError(f"target_value must be finite: {order.target_value!r}")
    if order.side == "BUY":
        if not isinstance(order.quantity, int) or isinstance(order.quantity, bool) or order.quantity != 0:
            raise SchemaError(f"BUY quantity must be 0 for broker sizing: {order.quantity!r}")
        if order.target_value <= 0:
            raise SchemaError(f"BUY target_value must be > 0: {order.target_value!r}")
    else:
        # 不要求整手：送股与部分成交残量必然带零股，而 A 股「不足一手只能一次性
        # 全部卖出」这条规则要看持仓才能判，schema 看不到持仓，交由下单器执行。
        if not isinstance(order.quantity, int) or isinstance(order.quantity, bool) or order.quantity <= 0:
            raise SchemaError(f"SELL quantity must be positive int: {order.quantity!r}")
        if float(order.target_value) != 0.0:
            raise SchemaError(f"SELL target_value must be 0: {order.target_value!r}")
    if order.max_quantity is None:
        max_quantity = 0
    else:
        max_quantity = order.max_quantity
    if (
        isinstance(max_quantity, bool)
        or not isinstance(max_quantity, int)
        or max_quantity < 0
        or max_quantity % TRADE_UNIT != 0
    ):
        raise SchemaError(
            f"max_quantity must be 0 or a positive whole lot: {max_quantity!r}"
        )
    if order.side == "SELL" and max_quantity != 0:
        raise SchemaError("SELL max_quantity must be 0")
    if len(order.client_order_id) > CLIENT_ORDER_ID_MAX_LEN:
        raise SchemaError(f"client_order_id too long: {order.client_order_id!r}")
    # stock_code 必须为 QMT 格式（600000.SH）
    from live_trading.modules.code_map import qmt_to_qlib
    try:
        qmt_to_qlib(order.stock_code)
    except ValueError as e:
        raise SchemaError(f"stock_code must be QMT format: {order.stock_code!r}") from e


def validate_batch(header: BatchHeader, orders: list) -> None:
    if header.schema_version != SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version must be {SCHEMA_VERSION}: {header.schema_version!r}"
        )
    if header.mode not in VALID_MODES:
        raise SchemaError(f"invalid mode: {header.mode!r}")
    if header.account_environment not in VALID_ACCOUNT_ENVIRONMENTS:
        raise SchemaError(
            "account_environment must be SIMULATION or REAL: "
            f"{header.account_environment!r}"
        )
    if header.account_environment == "REAL" and header.mode != "LIVE":
        raise SchemaError("REAL account_environment requires LIVE mode")
    if not _DATE_RE.match(header.trade_date):
        raise SchemaError(f"trade_date must be YYYY-MM-DD: {header.trade_date!r}")
    if not _DATE_RE.match(header.signal_date):
        raise SchemaError(f"signal_date must be YYYY-MM-DD: {header.signal_date!r}")
    if header.order_count != len(orders):
        raise SchemaError(
            f"order_count mismatch: header={header.order_count}, actual={len(orders)}"
        )
    seen = set()
    for order in orders:
        if order.batch_id != header.batch_id:
            raise SchemaError(
                f"order batch_id mismatch: {order.batch_id!r} != {header.batch_id!r}"
            )
        if order.client_order_id in seen:
            raise SchemaError(f"duplicate client_order_id: {order.client_order_id!r}")
        seen.add(order.client_order_id)
        validate_order(order)


def validate_fill(fill: FillEvent) -> None:
    if fill.mode not in VALID_MODES:
        raise SchemaError(f"invalid fill mode: {fill.mode!r}")
    if fill.status not in VALID_FILL_STATUS:
        raise SchemaError(f"invalid fill status: {fill.status!r}")
    if fill.side not in VALID_SIDES:
        raise SchemaError(f"invalid fill side: {fill.side!r}")
    if (
        isinstance(fill.requested_qty, bool)
        or not isinstance(fill.requested_qty, int)
        or fill.requested_qty < 0
    ):
        raise SchemaError(
            f"requested_qty must be a non-negative int: {fill.requested_qty!r}"
        )
    if (
        isinstance(fill.filled_qty, bool)
        or not isinstance(fill.filled_qty, int)
        or fill.filled_qty < 0
        or fill.filled_qty > fill.requested_qty
    ):
        raise SchemaError(
            "filled_qty must be a non-negative int no greater than "
            f"requested_qty: {fill.filled_qty!r}/{fill.requested_qty!r}"
        )
    if (
        isinstance(fill.avg_price, bool)
        or not isinstance(fill.avg_price, (int, float))
        or not math.isfinite(float(fill.avg_price))
        or fill.avg_price < 0
        or (fill.filled_qty > 0 and fill.avg_price <= 0)
    ):
        raise SchemaError(
            f"avg_price must be positive when filled_qty > 0: {fill.avg_price!r}"
        )
    from live_trading.modules.code_map import qmt_to_qlib
    try:
        qmt_to_qlib(fill.stock_code)
    except ValueError as e:
        raise SchemaError(
            f"fill stock_code must be QMT format: {fill.stock_code!r}"
        ) from e
