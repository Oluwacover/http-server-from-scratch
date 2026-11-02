"""
Modular OOP Thread Pool for Network Connection Processing.
Manages fixed worker threads consuming from a synchronized Task Queue,
with dynamic statistics, lifecycle hooks, and graceful shutdown handling.
"""

import threading
import queue
import logging
from typing import Callable, Optional

logger = logging.getLogger("server.core.thread_pool")


class Worker(threading.Thread):
    """
    Worker thread that continuously extracts tasks from the work queue.
    """
    def __init__(self, pool: "ThreadPool", worker_id: int):
        super().__init__(daemon=True, name=f"HttpWorker-{worker_id}")
        self.pool = pool
        self.worker_id = worker_id
        self.running = True
        self.busy = False

    def run(self):
        while self.running:
            try:
                task = self.pool.tasks.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:
                # Sentinel shutdown signal
                self.pool.tasks.task_done()
                break

            func, args, kwargs = task
            self.busy = True
            try:
                func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[Worker-{self.worker_id}] Unhandled error executing task: {e}", exc_info=True)
            finally:
                self.busy = False
                self.pool.tasks.task_done()


class ThreadPool:
    """
    Fixed-size thread pool adhering to RAII / Context Management principles.
    """
    def __init__(self, num_threads: int = 16, max_queue_size: int = 512):
        self.num_threads = num_threads
        self.tasks: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.workers: list[Worker] = []
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
        self._start_workers()

    def _start_workers(self):
        with self._lock:
            for i in range(self.num_threads):
                w = Worker(self, i + 1)
                w.start()
                self.workers.append(w)
        logger.info(f"Initialized ThreadPool with {self.num_threads} workers.")

    def submit(self, func: Callable, *args, **kwargs) -> bool:
        """
        Submit a task to the pool. Returns True if queued, False if pool is shut down or queue full.
        """
        if self._shutdown_event.is_set():
            return False
        try:
            self.tasks.put_nowait((func, args, kwargs))
            return True
        except queue.Full:
            logger.warning("ThreadPool task queue is full; dropping or shedding connection.")
            return False

    @property
    def active_count(self) -> int:
        """Count of currently active/busy worker threads."""
        return sum(1 for w in self.workers if w.busy)

    @property
    def queue_size(self) -> int:
        """Current count of waiting tasks."""
        return self.tasks.qsize()

    def shutdown(self, wait: bool = True):
        """
        Gracefully stop all worker threads.
        """
        if self._shutdown_event.is_set():
            return

        logger.info("Initiating ThreadPool graceful shutdown...")
        self._shutdown_event.set()

        # Send sentinel None to each worker
        for _ in self.workers:
            try:
                self.tasks.put_nowait(None)
            except queue.Full:
                pass

        for w in self.workers:
            w.running = False

        if wait:
            for w in self.workers:
                w.join(timeout=2.0)
            logger.info("ThreadPool shutdown complete.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=True)
