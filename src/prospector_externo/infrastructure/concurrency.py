"""
Pool de ejecución con Bulkhead para aislar tareas livianas HTTP de tareas de navegador.
Permite concurrencia controlada para fuentes completas y subtareas (como obtención de cabeceras).
"""

import asyncio
from contextlib import asynccontextmanager
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Callable, List, Any, TypeVar, Optional
from prospector_externo.domain.models import SourceConfig

T = TypeVar("T")
R = TypeVar("R")

logger = logging.getLogger("prospector.infrastructure.concurrency")


class AsyncWorkerPool:
    """
    Administrador de concurrencia con aislamiento Bulkhead.
    Separa workers para tareas HTTP livianas (Scraping estático / API)
    de workers para tareas de Navegador pesadas (Playwright headless Chromium).
    """

    def __init__(self, max_http_workers: int = 6, max_browser_workers: int = 2):
        if max_http_workers < 1 or max_browser_workers < 1:
            raise ValueError("Los límites de workers deben ser positivos")
        self.max_http_workers = max_http_workers
        self.max_browser_workers = max_browser_workers
        # El batch moderno es async: los ThreadPoolExecutor de compatibilidad
        # no regulan por sí solos las corutinas de los workflows.
        self._http_async_slots = asyncio.Semaphore(max_http_workers)
        self._browser_async_slots = asyncio.Semaphore(max_browser_workers)
        self._http_executor = ThreadPoolExecutor(
            max_workers=self.max_http_workers,
            thread_name_prefix="http-worker"
        )
        self._browser_executor = ThreadPoolExecutor(
            max_workers=self.max_browser_workers,
            thread_name_prefix="browser-worker"
        )

    @asynccontextmanager
    async def async_source_slot(self, config: SourceConfig):
        """Bulkhead efectivo de workflows async, sin enviar corutinas a threads.

        Un workflow JS ocupa una plaza de navegador durante TODO su recorrido;
        los workflows HTTP/API emplean plazas independientes. La liberación
        automática también cubre fallos y cancelación del batch.
        """
        slots = (self._browser_async_slots if config.workflow.lower() == "javascript"
                 else self._http_async_slots)
        async with slots:
            yield

    def submit_workflow(self, task_func: Callable[[SourceConfig], R], config: SourceConfig) -> Future:
        """
        Despacha la ejecución de un workflow al executor adecuado según su naturaleza (Bulkhead).
        """
        is_browser = config.workflow.lower() == "javascript"
        executor = self._browser_executor if is_browser else self._http_executor
        return executor.submit(task_func, config)

    def run_parallel(
        self,
        task_func: Callable[[T], R],
        items: List[T],
        is_browser_task: bool = False,
        max_workers: Optional[int] = None
    ) -> List[R]:
        """
        Ejecuta una lista de subtareas (ej. cabeceras HTTP) en paralelo.
        """
        if not items:
            return []

        if max_workers:
            with ThreadPoolExecutor(max_workers=max_workers) as temp_pool:
                futures = [temp_pool.submit(task_func, item) for item in items]
                results = []
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as e:
                        logger.warning(f"Error en tarea paralela: {e}")
                return results

        executor = self._browser_executor if is_browser_task else self._http_executor
        futures = [executor.submit(task_func, item) for item in items]
        results = []
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                logger.warning(f"Error en tarea paralela: {e}")
        return results

    def shutdown(self, wait: bool = True) -> None:
        """Libera los recursos y apaga los pools."""
        self._http_executor.shutdown(wait=wait)
        self._browser_executor.shutdown(wait=wait)
