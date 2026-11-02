"""
Low-Level TCP Server Socket Listener.
Binds to local interface, listens for incoming TCP handshakes,
and delegates active connections to the worker ThreadPool.
"""

import socket
import select
import signal
import sys
import logging
from typing import Callable, Optional
from server.core.thread_pool import ThreadPool
from server.core.connection_handler import ConnectionHandler
from server.parser.http_request import HttpRequest
from server.parser.http_response import HttpResponse

logger = logging.getLogger("server.core.tcp_server")


class TcpServer:
    """
    Robust POSIX/Winsock raw TCP server socket manager.
    """
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        worker_threads: int = 16,
        backlog: int = 128,
        keep_alive_timeout: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.backlog = backlog
        self.keep_alive_timeout = keep_alive_timeout
        self.pool = ThreadPool(num_threads=worker_threads)
        self.router_callback: Optional[Callable[[HttpRequest], HttpResponse]] = None

        self.server_sock: Optional[socket.socket] = None
        self.running = False

    def set_router(self, callback: Callable[[HttpRequest], HttpResponse]):
        """Set the top-level HTTP router/dispatcher callback."""
        self.router_callback = callback

    def start(self):
        """
        Bind, listen, and enter the connection acceptance loop.
        """
        if not self.router_callback:
            raise RuntimeError("Cannot start TcpServer without setting a router callback.")

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Non-blocking accept with timeout allows clean loop termination
        self.server_sock.settimeout(1.0)

        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(self.backlog)
        self.running = True

        actual_port = self.server_sock.getsockname()[1]
        logger.info(f"⚡ HTTP Server listening at http://{self.host}:{actual_port} (Workers: {self.pool.num_threads})")

        # Register OS signal handlers for graceful termination
        self._register_signals()

        try:
            while self.running:
                try:
                    client_sock, client_addr = self.server_sock.accept()
                except socket.timeout:
                    # Timeout periodically triggers to check self.running flag
                    continue
                except OSError:
                    if not self.running:
                        break
                    continue

                handler = ConnectionHandler(
                    client_sock=client_sock,
                    client_address=client_addr,
                    request_router=self.router_callback,
                    keep_alive_timeout=self.keep_alive_timeout,
                )

                enqueued = self.pool.submit(handler.handle)
                if not enqueued:
                    # Thread pool rejected the connection (saturated); send 503
                    try:
                        resp = HttpResponse.error(503, "Server Busy / Connection Shed")
                        client_sock.sendall(resp.serialize(keep_alive=False))
                    except OSError:
                        pass
                    finally:
                        client_sock.close()
        finally:
            self.stop()

    def stop(self):
        """Cleanly close listening socket and drain thread pool."""
        if not self.running:
            return
        logger.info("Stopping TCP Server...")
        self.running = False
        
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass
            self.server_sock = None

        self.pool.shutdown(wait=True)
        logger.info("TCP Server stopped cleanly.")

    def _register_signals(self):
        def _handle_sig(signum, frame):
            logger.info(f"Received exit signal ({signum}). Initiating shutdown...")
            self.running = False

        try:
            signal.signal(signal.SIGINT, _handle_sig)
            signal.signal(signal.SIGTERM, _handle_sig)
        except (ValueError, AttributeError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
