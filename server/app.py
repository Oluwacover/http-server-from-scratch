"""
HttpServerApp - High-Level Application Facade.
Unites TCP networking, thread pooling, routing, static assets, and middleware
into an intuitive, modern developer interface.
"""

import logging
import time
from typing import Callable, Optional
from server.parser.http_request import HttpRequest
from server.parser.http_response import HttpResponse
from server.core.tcp_server import TcpServer
from server.routing.router import Router, HandlerFunc, MiddlewareFunc
from server.routing.static_files import StaticFileHandler

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("server.app")


class HttpServerApp:
    """
    Main Application entrypoint for the HTTP Server.
    """
    def __init__(
        self,
        worker_threads: int = 16,
        keep_alive_timeout: float = 5.0,
    ):
        self.router = Router()
        self.worker_threads = worker_threads
        self.keep_alive_timeout = keep_alive_timeout
        self.static_handlers: list[StaticFileHandler] = []
        self.tcp_server: Optional[TcpServer] = None

        # Add default timing and access logger middleware
        self.use(self._timing_middleware)

    def get(self, path: str):
        """Register GET handler."""
        return self.router.get(path)

    def post(self, path: str):
        """Register POST handler."""
        return self.router.post(path)

    def put(self, path: str):
        """Register PUT handler."""
        return self.router.put(path)

    def delete(self, path: str):
        """Register DELETE handler."""
        return self.router.delete(path)

    def route(self, method: str, path: str, handler: HandlerFunc):
        """Directly register route."""
        self.router.add_route(method, path, handler)

    def use(self, middleware: MiddlewareFunc):
        """Register middleware."""
        self.router.use(middleware)

    def mount_static(self, url_prefix: str, directory_path: str):
        """Serve static files from directory at designated URL prefix."""
        handler = StaticFileHandler(directory=directory_path, url_prefix=url_prefix)
        self.static_handlers.append(handler)
        # Catch-all route for the prefix
        self.router.add_route("*", f"{url_prefix}/{{filepath}}", handler.handle)
        logger.info(f"Mounted static directory '{directory_path}' at '{url_prefix}'")

    def enable_cors(self, allow_origin: str = "*", allow_methods: str = "*", allow_headers: str = "*"):
        """Enable Cross-Origin Resource Sharing (CORS) middleware."""
        def cors_middleware(request: HttpRequest, next_handler: Callable[[HttpRequest], HttpResponse]) -> HttpResponse:
            if request.method == "OPTIONS":
                resp = HttpResponse(status_code=204)
            else:
                resp = next_handler(request)
            resp.set_header("Access-Control-Allow-Origin", allow_origin)
            resp.set_header("Access-Control-Allow-Methods", allow_methods)
            resp.set_header("Access-Control-Allow-Headers", allow_headers)
            return resp
        self.use(cors_middleware)

    def dispatch(self, request: HttpRequest) -> HttpResponse:
        """Top-level dispatch called by ConnectionHandler."""
        return self.router.dispatch(request)

    def run(self, host: str = "127.0.0.1", port: int = 8080):
        """
        Start TCP listener and run event loop.
        """
        self.tcp_server = TcpServer(
            host=host,
            port=port,
            worker_threads=self.worker_threads,
            keep_alive_timeout=self.keep_alive_timeout,
        )
        self.tcp_server.set_router(self.dispatch)
        self.tcp_server.start()

    def _timing_middleware(self, request: HttpRequest, next_handler: Callable[[HttpRequest], HttpResponse]) -> HttpResponse:
        t0 = time.perf_counter()
        response = next_handler(request)
        dt = (time.perf_counter() - t0) * 1000
        response.set_header("X-Response-Time-Ms", f"{dt:.2f}")
        return response
