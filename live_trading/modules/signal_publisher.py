"""原子发布信号批次到共享目录 inbox/。

写入顺序（设计文档 §5.2 定稿）：
1. signal_{batch_id}.jsonl.tmp → fsync → rename 为 .jsonl
2. signal_{batch_id}.done.tmp → fsync → rename 为 .done（内容为 checksum）

消费方（QMT 内置策略）只在看到 .done 后才处理 .jsonl。
"""

import dataclasses
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout as FileLockTimeout

from live_trading.modules.signal_schema import (
    BatchHeader,
    compute_checksum,
    validate_batch,
)

logger = logging.getLogger("live_trading.signal_publisher")
AUTHORIZATION_LOCK_NAME = "OPERATOR_AUTHORIZATION.lock"
AUTHORIZATION_LOCK_TIMEOUT_SECONDS = 10


class PublishError(RuntimeError):
    """发布失败（同批次内容冲突或原子写失败）。"""


class SignalPublisher:
    def __init__(self, bridge_root):
        self.bridge_root = Path(bridge_root)
        self.inbox = self.bridge_root / "inbox"

    @property
    def authorization_domain_root(self) -> Path:
        """Return the shared root used by main and its nested probe profile."""
        if self.bridge_root.name == "pr49_probe":
            return self.bridge_root.parent
        return self.bridge_root

    @property
    def authorization_lock_path(self) -> Path:
        return (
            self.authorization_domain_root / "state" /
            AUTHORIZATION_LOCK_NAME
        )

    @contextmanager
    def authorization_gate(
        self, timeout: float = AUTHORIZATION_LOCK_TIMEOUT_SECONDS,
    ):
        """Serialize SMB publication with controlled Windows marker creation."""
        try:
            self.authorization_lock_path.parent.mkdir(
                parents=True, exist_ok=True,
            )
            lock = FileLock(str(self.authorization_lock_path))
            lock.acquire(timeout=timeout)
        except FileLockTimeout as exc:
            raise PublishError("authorization lock timeout") from exc
        except OSError as exc:
            raise PublishError("authorization lock acquisition failed") from exc
        try:
            yield
        finally:
            try:
                lock.release()
            except OSError as exc:
                raise PublishError("authorization lock release failed") from exc

    def ensure_available(self, batch_id: str) -> None:
        """Legacy strict availability check."""
        jsonl_path = self.inbox / f"signal_{batch_id}.jsonl"
        done_path = self.inbox / f"signal_{batch_id}.done"
        if jsonl_path.exists() or done_path.exists():
            raise PublishError(f"batch {batch_id} already published")

    @staticmethod
    def _render(header: BatchHeader, orders: list):
        order_lines = [order.to_json_line() for order in orders]
        header = dataclasses.replace(
            header,
            order_count=len(orders),
            checksum=compute_checksum(order_lines),
        )
        validate_batch(header, orders)
        jsonl_content = "\n".join(
            [header.to_json_line()] + order_lines
        ) + "\n"
        done_content = header.checksum + "\n"
        return header, jsonl_content, done_content

    def ensure_publishable(self, header: BatchHeader, orders: list) -> bool:
        """Return True for an exact visible retry, fail on any conflict."""
        _, jsonl_content, done_content = self._render(header, orders)
        jsonl_path = self.inbox / f"signal_{header.batch_id}.jsonl"
        done_path = self.inbox / f"signal_{header.batch_id}.done"
        if not jsonl_path.exists() and not done_path.exists():
            return False
        if (
            jsonl_path.is_file()
            and done_path.is_file()
            and jsonl_path.read_text(encoding="utf-8") == jsonl_content
            and done_path.read_text(encoding="utf-8") == done_content
        ):
            return True
        raise PublishError(
            f"batch {header.batch_id} conflicts with already published bytes"
        )

    def publish(
        self, header: BatchHeader, orders: list, *, before_exposure=None,
    ) -> Path:
        """校验并原子写出批次文件，返回 jsonl 路径。

        header 的 order_count / checksum 由本方法填充，调用方无需预填。
        ``before_exposure`` 在内部 byte preflight 之后、任何 inbox rename
        之前执行，供调用方复核外部授权门禁。
        """
        header, jsonl_content, done_content = self._render(header, orders)

        self.inbox.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.inbox / f"signal_{header.batch_id}.jsonl"
        done_path = self.inbox / f"signal_{header.batch_id}.done"
        if self.ensure_publishable(header, orders):
            logger.info("batch %s already published with exact bytes", header.batch_id)
            return jsonl_path

        if before_exposure is not None:
            before_exposure()

        self._atomic_write(jsonl_path, jsonl_content)
        self._atomic_write(done_path, done_content)

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
