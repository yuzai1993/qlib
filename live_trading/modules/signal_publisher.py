"""原子发布信号批次到共享目录 inbox/。

写入顺序（设计文档 §5.2 定稿）：
1. signal_{batch_id}.jsonl.tmp → fsync → rename 为 .jsonl
2. signal_{batch_id}.done.tmp → fsync → rename 为 .done（内容为 checksum）

消费方（QMT 内置策略）只在看到 .done 后才处理 .jsonl。
"""

import dataclasses
import logging
import os
from pathlib import Path

from live_trading.modules.signal_schema import (
    BatchHeader,
    compute_checksum,
    validate_batch,
)

logger = logging.getLogger("live_trading.signal_publisher")


class PublishError(RuntimeError):
    """发布失败（重复批次、空批次等）。"""


class SignalPublisher:
    def __init__(self, bridge_root):
        self.bridge_root = Path(bridge_root)
        self.inbox = self.bridge_root / "inbox"

    def ensure_available(self, batch_id: str) -> None:
        """Fail before any durable ledger write when a batch is already visible."""
        jsonl_path = self.inbox / f"signal_{batch_id}.jsonl"
        done_path = self.inbox / f"signal_{batch_id}.done"
        if jsonl_path.exists() or done_path.exists():
            raise PublishError(f"batch {batch_id} already published")

    def publish(self, header: BatchHeader, orders: list) -> Path:
        """校验并原子写出批次文件，返回 jsonl 路径。

        header 的 order_count / checksum 由本方法填充，调用方无需预填。
        """
        if not orders:
            raise PublishError(f"batch {header.batch_id}: empty order list")

        order_lines = [o.to_json_line() for o in orders]
        header = dataclasses.replace(
            header,
            order_count=len(orders),
            checksum=compute_checksum(order_lines),
        )
        validate_batch(header, orders)

        self.inbox.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.inbox / f"signal_{header.batch_id}.jsonl"
        done_path = self.inbox / f"signal_{header.batch_id}.done"
        self.ensure_available(header.batch_id)

        self._atomic_write(jsonl_path, "\n".join([header.to_json_line()] + order_lines) + "\n")
        self._atomic_write(done_path, header.checksum + "\n")

        logger.info(
            "published batch %s: %d orders, mode=%s -> %s",
            header.batch_id, len(orders), header.mode, jsonl_path,
        )
        return jsonl_path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
