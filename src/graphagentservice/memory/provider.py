"""Mem0 全局单例工厂 + 生命周期管理。

职责：
  - 根据全局配置 ``memory`` 段创建 Mem0 ``Memory`` 实例
  - 管理 ``MemoryCommitWorker`` 后台任务
  - 提供 ``startup`` / ``shutdown`` 生命周期钩子
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from graphagentservice.common.logging import context_extra

if TYPE_CHECKING:
    from dynaconf.utils.boxing import DynaBox

_logger = logging.getLogger(__name__)


class MemoryProvider:
    """全局记忆服务提供者（单例）。"""

    def __init__(
        self,
        settings: DynaBox,
        *,
        llm_api_key: str = "",
        llm_base_url: str = "",
    ) -> None:
        self._settings = settings
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._memory: Any | None = None
        self._commit_worker: Any | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """应用启动时调用。"""
        enabled = self._settings.get("enabled", False)
        if not enabled:
            _logger.info(
                "Memory provider disabled",
                extra=context_extra(event="memory_provider_disabled", status="disabled"),
            )
            return

        self._memory = self._create_memory()
        _logger.info(
            "Memory provider initialized",
            extra=context_extra(event="memory_provider_initialized", status="initialized"),
        )

        from graphagentservice.memory.commit import MemoryCommitWorker

        commit_cfg = self._settings.get("commit", {})
        self._commit_worker = MemoryCommitWorker(
            memory=self._memory,
            queue_maxsize=int(commit_cfg.get("queue_maxsize", 256)),
            worker_count=int(commit_cfg.get("worker_count", 2)),
        )
        await self._commit_worker.start()
        _logger.info(
            "Memory commit worker started",
            extra=context_extra(event="memory_worker_started", status="started"),
        )

    async def shutdown(self) -> None:
        """应用关闭时调用。"""
        if self._commit_worker is not None:
            await self._commit_worker.stop()
            _logger.info(
                "Memory commit worker stopped",
                extra=context_extra(event="memory_worker_stopped", status="stopped"),
            )
        self._memory = None
        self._commit_worker = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def memory(self) -> Any | None:
        """返回 Mem0 ``Memory`` 实例（可能为 None）。"""
        return self._memory

    @property
    def commit_worker(self) -> Any | None:
        """返回 ``MemoryCommitWorker`` 实例（可能为 None）。"""
        return self._commit_worker

    @property
    def enabled(self) -> bool:
        return self._memory is not None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _create_memory(self) -> Any:
        """根据配置创建 Mem0 Memory 实例。"""
        import os

        from mem0 import Memory
        from mem0.configs.base import (
            EmbedderConfig,
            LlmConfig,
            MemoryConfig,
            VectorStoreConfig,
        )

        api_key = self._llm_api_key
        base_url = self._llm_base_url

        if not api_key:
            raise ValueError(
                "Memory enabled but no LLM API key provided. "
                "Pass llm_api_key when constructing MemoryProvider."
            )

        # 设置 Mem0 依赖的环境变量
        os.environ["OPENAI_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url

        llm_cfg = self._settings.get("llm", {})
        llm_config = LlmConfig(
            provider=str(llm_cfg.get("provider", "openai")),
            config={
                "model": str(llm_cfg.get("model", "gpt-4o-mini")),
                "temperature": float(llm_cfg.get("temperature", 0.1)),
                "max_tokens": int(llm_cfg.get("max_tokens", 1500)),
            },
        )

        embedder_cfg = self._settings.get("embedder", {})
        embedder_config = EmbedderConfig(
            provider=str(embedder_cfg.get("provider", "openai")),
            config={
                "model": str(embedder_cfg.get("model", "text-embedding-3-small")),
            },
        )

        vs_cfg = self._settings.get("vector_store", {})
        vector_store_config = VectorStoreConfig(
            provider=str(vs_cfg.get("provider", "milvus")),
            config={
                "collection_name": str(vs_cfg.get("collection_name", "mem0")),
                "embedding_model_dims": int(vs_cfg.get("embedding_model_dims", 1536)),
                "url": str(vs_cfg.get("url", "")),
                "token": str(vs_cfg.get("token", "")),
                "metric_type": str(vs_cfg.get("metric_type", "L2")),
                "db_name": str(vs_cfg.get("db_name", "default")),
            },
        )

        memory_config = MemoryConfig(
            llm=llm_config,
            embedder=embedder_config,
            vector_store=vector_store_config,
        )
        return Memory(memory_config)
