"""
HTTP Connection Handler.
Manages the TCP socket connection lifecycle, Keep-Alive persistence,
RFC 9110 request dispatching, and socket teardown.
"""

import socket
import time
import logging
from typing import Callable, Optional
from server.parser.state_machine import HttpParser, HttpParserError
from server.parser.http_request import HttpRequest
from server.parser.http_response import HttpResponse

logger = logging.getLogger("server.core.connection_handler")


class ConnectionHandler:
    """
    Handles a single TCP connection lifecycle with Keep-Alive persistence.
    """
    DEFAULT_KEEP_ALIVE_TIMEOUT = 5.0  # seconds
    MAX_REQUESTS_PER_CONNECTION = 100

    def __init__(
        self,
        client_sock: socket.socket,
        client_address: tuple,
        request_router: Callable[[HttpRequest], HttpResponse],
        keep_alive_timeout: float = DEFAULT_KEEP_ALIVE_TIMEOUT,
    ):
        self.client_sock = client_sock
        self.client_address = client_address
        self.router = request_router
        self.keep_alive_timeout = keep_alive_timeout
        self.requests_served = 0

    def handle(self):
        """
        Process incoming requests sequentially until Keep-Alive expires,
        the client terminates the connection, or an error occurs.
        """
        parser = HttpParser(client_address=self.client_address)
        
        # Configure socket options
        self.client_sock.settimeout(self.keep_alive_timeout)
        try:
            self.client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

        remaining_buffer = bytearray()

        try:
            while self.requests_served < self.MAX_REQUESTS_PER_CONNECTION:
                req: Optional[HttpRequest] = None

                # Process any leftover bytes from previous packet first
                if remaining_buffer:
                    chunk = bytes(remaining_buffer)
                    remaining_buffer.clear()
                    try:
                        req, consumed = parser.feed(chunk)
                        if consumed < len(chunk):
                            remaining_buffer.extend(chunk[consumed:])
                    except HttpParserError as err:
                        self._send_error_and_close(err.status_code, err.message)
                        return

                # Read from socket until a full request is parsed
                while req is None:
                    try:
                        chunk = self.client_sock.recv(4096)
                    except socket.timeout:
                        # Keep-alive timeout reached on idle socket; clean close
                        return
                    except (ConnectionResetError, BrokenPipeError):
                        return
                    except OSError as e:
                        logger.debug(f"Socket error from {self.client_address}: {e}")
                        return

                    if not chunk:
                        # Client closed socket (EOF)
                        return

                    try:
                        req, consumed = parser.feed(chunk)
                        if consumed < len(chunk):
                            remaining_buffer.extend(chunk[consumed:])
                    except HttpParserError as err:
                        self._send_error_and_close(err.status_code, err.message)
                        return

                # Request fully parsed!
                start_time = time.perf_counter()
                self.requests_served += 1
                keep_alive = req.is_keep_alive and (self.requests_served < self.MAX_REQUESTS_PER_CONNECTION)

                # Dispatch to router
                try:
                    response = self.router(req)
                except Exception as e:
                    logger.error(f"Internal server error handling {req.method} {req.path}: {e}", exc_info=True)
                    response = HttpResponse.error(500, "Internal Server Error")

                # Serialize and send
                resp_bytes = response.serialize(keep_alive=keep_alive)
                try:
                    self.client_sock.sendall(resp_bytes)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

                duration_ms = (time.perf_counter() - start_time) * 1000
                logger.info(
                    f"{self.client_address[0]}:{self.client_address[1]} - "
                    f"\"{req.method} {req.target} {req.version}\" "
                    f"{response.status_code} {len(response.body)}b - {duration_ms:.2f}ms"
                )

                if not keep_alive:
                    break

                # Prepare parser for next request on same connection
                parser.reset()

        finally:
            try:
                self.client_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.client_sock.close()

    def _send_error_and_close(self, status_code: int, message: str):
        """Construct RFC error response, flush to socket, and terminate connection."""
        response = HttpResponse.error(status_code, message)
        data = response.serialize(keep_alive=False)
        try:
            self.client_sock.sendall(data)
        except OSError:
            pass
