from __future__ import annotations

import asyncio
import signal
import threading
from contextlib import asynccontextmanager
from types import FrameType
from typing import Any, Callable

from fastapi import FastAPI

from overmindagent.services.sse import SseConnectionRegistry


def create_app_lifespan(
    *,
    sse_connection_registry: SseConnectionRegistry,
) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Uvicorn 会先等待活跃连接结束，再进入 FastAPI 的 shutdown 流程，
        # 因此这里需要在进程收到停止信号时尽早关闭 SSE 连接。
        restore_signal_handlers = _install_uvicorn_signal_bridge(
            registry=sse_connection_registry,
        )
        try:
            yield
        finally:
            restore_signal_handlers()
            # 这里保留一次兜底清理，覆盖非信号触发的退出路径和测试场景。
            await sse_connection_registry.close_all()

    return lifespan


def _install_uvicorn_signal_bridge(
    *,
    registry: SseConnectionRegistry,
) -> Callable[[], None]:
    # signal handler 只能在主线程里安全替换。
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    loop = asyncio.get_running_loop()
    installed_handlers: list[tuple[signal.Signals, Any]] = []
    close_task: asyncio.Task[None] | None = None

    def schedule_close_all() -> None:
        nonlocal close_task
        if close_task is None or close_task.done():
            close_task = loop.create_task(registry.close_all())

    def wrap(
        previous_handler: Callable[[int, FrameType | None], None],
    ) -> Callable[[int, FrameType | None], None]:
        def handler(sig: int, frame: FrameType | None) -> None:
            # 先关闭 SSE 连接，再交还给 Uvicorn 原本的退出处理逻辑。
            if not loop.is_closed():
                loop.call_soon_threadsafe(schedule_close_all)
            previous_handler(sig, frame)

        return handler

    for signum in _shutdown_signals():
        previous_handler = signal.getsignal(signum)
        if not _is_uvicorn_signal_handler(previous_handler):
            continue
        signal.signal(signum, wrap(previous_handler))
        installed_handlers.append((signum, previous_handler))

    def restore() -> None:
        for signum, previous_handler in installed_handlers:
            signal.signal(signum, previous_handler)

    return restore


def _shutdown_signals() -> tuple[signal.Signals, ...]:
    signals = [signal.SIGINT]
    if hasattr(signal, "SIGTERM"):
        signals.append(signal.SIGTERM)
    return tuple(signals)


def _is_uvicorn_signal_handler(handler: Any) -> bool:
    return callable(handler) and getattr(handler, "__module__", "").startswith("uvicorn.")
