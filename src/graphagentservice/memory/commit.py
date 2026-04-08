"""异步记忆写入队列 + 后台 worker。

职责：
  - 提供非阻塞的 ``enqueue`` 接口供 graph 节点调用
  - 后台 asyncio Task 从队列消费并调用 ``mem0.add``
  - 幂等键去重，防止同一轮对话重复写入
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from graphagentservice.common.logging import context_extra

_logger = logging.getLogger(__name__)


class MemoryCommitWorker:
    """异步记忆提交 worker。

    用法::

        worker = MemoryCommitWorker(memory=mem0_instance)
        await worker.start()
        await worker.enqueue("user-1", messages, idempotency_key="s1:r1")
        # ...
        await worker.stop()
    """

    def __init__(
        self,
        *,
        memory: Any,
        queue_maxsize: int = 256,
        worker_count: int = 2,
    ) -> None:
        self._memory = memory
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_count = worker_count
        self._workers: list[asyncio.Task[None]] = []
        self._seen_keys: set[str] = set()
        self._running = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台 worker。"""
        if self._running:
            return
        self._running = True
        for i in range(self._worker_count):
            task = asyncio.create_task(self._worker_loop(name=f"mem-commit-{i}"))
            self._workers.append(task)
        _logger.info(
            "MemoryCommitWorker started  workers=%d  queue_maxsize=%d",
            self._worker_count,
            self._queue.maxsize,
            extra=context_extra(
                event="memory_worker_started",
                status="started",
                workerCount=self._worker_count,
                queueMaxsize=self._queue.maxsize,
            ),
        )

    async def stop(self) -> None:
        """停止后台 worker（等待队列排空）。"""
        self._running = False
        # 放入哨兵让 worker 退出
        for _ in self._workers:
            await self._queue.put({})
        for task in self._workers:
            task.cancel()
        self._workers.clear()
        _logger.info(
            "MemoryCommitWorker stopped",
            extra=context_extra(event="memory_worker_stopped", status="stopped"),
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        user_id: str,
        messages: list[dict[str, str]],
        idempotency_key: str,
    ) -> None:
        """将一轮对话入队等待异步写入。

        Args:
            user_id: Mem0 用户标识
            messages: 消息列表 ``[{"role":"user","content":"..."}, ...]``
            idempotency_key: 幂等键（如 ``session_id:request_id``）
        """
        if idempotency_key in self._seen_keys:
            _logger.info(
                "Duplicate memory commit skipped",
                extra=context_extra(
                    event="memory_commit_duplicate_skipped",
                    status="skipped",
                    userId=user_id,
                ),
            )
            return
        self._seen_keys.add(idempotency_key)
        try:
            self._queue.put_nowait({
                "user_id": user_id,
                "messages": messages,
            })
            _logger.info(
                "Memory commit enqueued",
                extra=context_extra(
                    event="memory_commit_enqueued",
                    status="queued",
                    userId=user_id,
                    messageCount=len(messages),
                ),
            )
        except asyncio.QueueFull:
            _logger.warning(
                "Memory commit queue full",
                extra=context_extra(
                    event="memory_commit_queue_full",
                    status="dropped",
                    userId=user_id,
                ),
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _worker_loop(self, *, name: str) -> None:
        """后台消费循环。"""
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if not item:
                break

            try:
                self._memory.add(
                    item["messages"],
                    user_id=item["user_id"],
                )
                _logger.debug(
                    "Memory committed  user=%s  msgs=%d",
                    item["user_id"],
                    len(item["messages"]),
                    extra=context_extra(
                        event="memory_commit_completed",
                        status="completed",
                        userId=item["user_id"],
                        messageCount=len(item["messages"]),
                    ),
                )
            except Exception:
                _logger.exception(
                    "Memory commit failed  user=%s",
                    item.get("user_id", "?"),
                    extra=context_extra(
                        event="memory_commit_failed",
                        status="failed",
                        userId=item.get("user_id", "?"),
                    ),
                )
