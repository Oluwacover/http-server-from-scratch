"""
RFC 9110 Finite State Machine HTTP/1.1 Streaming Parser.
Parses incoming raw TCP byte streams incrementally into HttpRequest objects,
supporting fragmented packets, pipelining, Content-Length, and Chunked Transfer-Encoding.
"""

from enum import Enum, auto
from typing import Optional, Dict, Tuple
from server.parser.http_request import HttpRequest


class ParseState(Enum):
    START = auto()
    METHOD = auto()
    URI = auto()
    VERSION = auto()
    REQUEST_LINE_CR = auto()
    REQUEST_LINE_LF = auto()
    HEADER_START = auto()
    HEADER_NAME = auto()
    HEADER_COLON = auto()
    HEADER_VALUE = auto()
    HEADER_CR = auto()
    HEADER_LF = auto()
    HEADERS_COMPLETE = auto()
    BODY_IDENTITY = auto()
    CHUNK_SIZE = auto()
    CHUNK_SIZE_CRLF = auto()
    CHUNK_DATA = auto()
    CHUNK_DATA_CRLF = auto()
    CHUNK_TRAILER = auto()
    COMPLETE = auto()
    ERROR = auto()


class HttpParserError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class HttpParser:
    """
    Incremental byte-level HTTP/1.1 parser driven by a Finite State Machine (RFC 9110).
    Can ingest arbitrary byte chunks (feed) across multiple TCP socket recv calls.
    """
    MAX_REQUEST_LINE_LEN = 8192
    MAX_HEADER_LINE_LEN = 8192
    MAX_HEADERS_COUNT = 100
    MAX_TOTAL_HEADERS_LEN = 65536
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB limit by default

    def __init__(self, client_address: Optional[tuple] = None):
        self.client_address = client_address
        self.reset()

    def reset(self):
        """Reset parser state for next request (supports HTTP Keep-Alive pipelining)."""
        self.state: ParseState = ParseState.START
        self.buffer = bytearray()
        
        self.method: str = ""
        self.target: str = ""
        self.version: str = ""
        self.headers: Dict[str, str] = {}
        self.current_header_name: str = ""
        self.current_header_val: str = ""
        self.headers_total_len: int = 0
        
        self.body: bytearray = bytearray()
        self.content_length: int = 0
        self.is_chunked: bool = False
        self.current_chunk_size: int = 0
        self.chunk_size_str: str = ""

        self.error_code: Optional[int] = None
        self.error_msg: Optional[str] = None

    def feed(self, chunk: bytes) -> Tuple[Optional[HttpRequest], int]:
        """
        Feed a raw byte buffer into the state machine.
        Returns:
            (HttpRequest, consumed_bytes) if a request is fully completed.
            (None, consumed_bytes) if more bytes are needed.
        Raises:
            HttpParserError if the stream violates RFC 9110.
        """
        i = 0
        total_len = len(chunk)

        while i < total_len:
            byte = chunk[i]
            char = chr(byte) if byte < 128 else None

            if self.state == ParseState.START:
                # RFC 9110 Section 2.2: allow empty lines (CRLF) before request line
                if char in ('\r', '\n'):
                    i += 1
                    continue
                elif char and char.isalpha():
                    self.buffer.clear()
                    self.buffer.append(byte)
                    self.state = ParseState.METHOD
                else:
                    self._fail(400, "Invalid start of HTTP request line")

            elif self.state == ParseState.METHOD:
                if char == ' ':
                    self.method = self.buffer.decode('ascii', errors='replace').strip()
                    if not self.method:
                        self._fail(400, "Empty HTTP method")
                    self.buffer.clear()
                    self.state = ParseState.URI
                elif char and (char.isalnum() or char in '-_'):
                    if len(self.buffer) > 16:
                        self._fail(501, "Method Not Implemented / too long")
                    self.buffer.append(byte)
                else:
                    self._fail(400, f"Invalid character in HTTP method: {repr(char)}")

            elif self.state == ParseState.URI:
                if char == ' ':
                    self.target = self.buffer.decode('ascii', errors='replace').strip()
                    if not self.target:
                        self._fail(400, "Empty request URI")
                    self.buffer.clear()
                    self.state = ParseState.VERSION
                elif char in ('\r', '\n'):
                    self._fail(400, "Malformed request line before HTTP version")
                else:
                    if len(self.buffer) > self.MAX_REQUEST_LINE_LEN:
                        self._fail(414, "URI Too Long")
                    self.buffer.append(byte)

            elif self.state == ParseState.VERSION:
                if char == '\r':
                    self.version = self.buffer.decode('ascii', errors='replace').strip()
                    self.buffer.clear()
                    self._validate_version()
                    self.state = ParseState.REQUEST_LINE_LF
                elif char == '\n':
                    # Bare LF tolerance
                    self.version = self.buffer.decode('ascii', errors='replace').strip()
                    self.buffer.clear()
                    self._validate_version()
                    self.state = ParseState.HEADER_START
                else:
                    if len(self.buffer) > 16:
                        self._fail(400, "Invalid HTTP version string")
                    self.buffer.append(byte)

            elif self.state == ParseState.REQUEST_LINE_LF:
                if char == '\n':
                    self.state = ParseState.HEADER_START
                else:
                    self._fail(400, "Expected LF after CR in request line")

            elif self.state == ParseState.HEADER_START:
                if char == '\r':
                    # Empty line (\r\n) => End of headers!
                    self.state = ParseState.HEADER_CR
                elif char == '\n':
                    # Bare LF empty line
                    self._prepare_body_transition()
                elif char and char.isascii() and (char.isalnum() or char in '-_'):
                    self.buffer.clear()
                    self.buffer.append(byte)
                    self.state = ParseState.HEADER_NAME
                else:
                    self._fail(400, f"Invalid character starting header: {repr(char)}")

            elif self.state == ParseState.HEADER_NAME:
                if char == ':':
                    self.current_header_name = self.buffer.decode('ascii', errors='replace').strip()
                    if not self.current_header_name:
                        self._fail(400, "Empty header name")
                    self.buffer.clear()
                    self.state = ParseState.HEADER_COLON
                elif char in (' ', '\t', '\r', '\n'):
                    # RFC 9110: No whitespace allowed between header field name and colon
                    self._fail(400, "Whitespace in header name or before colon")
                else:
                    if len(self.buffer) > self.MAX_HEADER_LINE_LEN:
                        self._fail(431, "Request Header Fields Too Large")
                    self.buffer.append(byte)

            elif self.state == ParseState.HEADER_COLON:
                # Skip optional leading OWS (Optional Whitespace) after colon
                if char in (' ', '\t'):
                    i += 1
                    continue
                elif char == '\r':
                    self.current_header_val = ""
                    self.state = ParseState.HEADER_CR
                elif char == '\n':
                    self.current_header_val = ""
                    self._save_header()
                    self.state = ParseState.HEADER_START
                else:
                    self.buffer.clear()
                    self.buffer.append(byte)
                    self.state = ParseState.HEADER_VALUE

            elif self.state == ParseState.HEADER_VALUE:
                if char == '\r':
                    self.current_header_val = self.buffer.decode('latin-1', errors='replace').strip()
                    self.buffer.clear()
                    self.state = ParseState.HEADER_CR
                elif char == '\n':
                    self.current_header_val = self.buffer.decode('latin-1', errors='replace').strip()
                    self.buffer.clear()
                    self._save_header()
                    self.state = ParseState.HEADER_START
                else:
                    if len(self.buffer) > self.MAX_HEADER_LINE_LEN:
                        self._fail(431, "Request Header Fields Too Large")
                    self.buffer.append(byte)

            elif self.state == ParseState.HEADER_CR:
                if char == '\n':
                    if self.current_header_name:
                        self._save_header()
                        self.state = ParseState.HEADER_START
                    else:
                        # Headers complete!
                        self._prepare_body_transition()
                else:
                    self._fail(400, "Expected LF after CR in header")

            elif self.state == ParseState.BODY_IDENTITY:
                remaining = self.content_length - len(self.body)
                available = total_len - i
                take = min(remaining, available)
                self.body.extend(chunk[i : i + take])
                i += take
                if len(self.body) == self.content_length:
                    self.state = ParseState.COMPLETE
                    req = self._build_request()
                    return req, i
                continue  # already advanced i

            elif self.state == ParseState.CHUNK_SIZE:
                if char in ('\r', '\n', ';'):
                    try:
                        self.current_chunk_size = int(self.chunk_size_str.strip(), 16)
                    except ValueError:
                        self._fail(400, f"Invalid chunk size hex: {self.chunk_size_str}")
                    self.chunk_size_str = ""
                    if char == ';':
                        # Ignore chunk extensions
                        self.state = ParseState.CHUNK_SIZE_CRLF
                    elif char == '\r':
                        self.state = ParseState.CHUNK_SIZE_CRLF
                    else:
                        # Bare LF
                        self.state = ParseState.CHUNK_DATA if self.current_chunk_size > 0 else ParseState.CHUNK_TRAILER
                elif char and char in "0123456789abcdefABCDEF":
                    self.chunk_size_str += char
                    if len(self.chunk_size_str) > 8:
                        self._fail(400, "Chunk size hex too large")
                else:
                    self._fail(400, f"Invalid chunk size character: {repr(char)}")

            elif self.state == ParseState.CHUNK_SIZE_CRLF:
                if char == '\n':
                    if self.current_chunk_size == 0:
                        self.state = ParseState.CHUNK_TRAILER
                    else:
                        self.state = ParseState.CHUNK_DATA

            elif self.state == ParseState.CHUNK_DATA:
                remaining = self.current_chunk_size
                available = total_len - i
                take = min(remaining, available)
                self.body.extend(chunk[i : i + take])
                self.current_chunk_size -= take
                i += take
                if len(self.body) > self.MAX_BODY_SIZE:
                    self._fail(413, "Payload Too Large")
                if self.current_chunk_size == 0:
                    self.state = ParseState.CHUNK_DATA_CRLF
                continue

            elif self.state == ParseState.CHUNK_DATA_CRLF:
                if char == '\n':
                    self.state = ParseState.CHUNK_SIZE

            elif self.state == ParseState.CHUNK_TRAILER:
                # Ending trailer \r\n
                if char == '\n':
                    self.state = ParseState.COMPLETE
                    i += 1
                    req = self._build_request()
                    return req, i

            i += 1

            if self.state == ParseState.COMPLETE:
                req = self._build_request()
                return req, i

        return None, i

    def _validate_version(self):
        if self.version not in ("HTTP/1.1", "HTTP/1.0"):
            if self.version.startswith("HTTP/"):
                self._fail(505, f"HTTP Version Not Supported: {self.version}")
            else:
                self._fail(400, f"Malformed HTTP version: {self.version}")

    def _save_header(self):
        if len(self.headers) >= self.MAX_HEADERS_COUNT:
            self._fail(431, "Too many HTTP headers")
        line_len = len(self.current_header_name) + len(self.current_header_val)
        self.headers_total_len += line_len
        if self.headers_total_len > self.MAX_TOTAL_HEADERS_LEN:
            self._fail(431, "Total HTTP headers length exceeded limit")
        
        # Append or set header
        key = self.current_header_name
        val = self.current_header_val
        if key in self.headers:
            self.headers[key] = f"{self.headers[key]}, {val}"
        else:
            self.headers[key] = val
        self.current_header_name = ""
        self.current_header_val = ""

    def _prepare_body_transition(self):
        # Inspect headers for Transfer-Encoding or Content-Length
        te = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in te:
            self.is_chunked = True
            self.state = ParseState.CHUNK_SIZE
            return

        cl_str = self.headers.get("Content-Length")
        if cl_str is not None:
            if not cl_str.strip().isdigit():
                self._fail(400, "Invalid Content-Length header")
            self.content_length = int(cl_str.strip())
            if self.content_length > self.MAX_BODY_SIZE:
                self._fail(413, f"Payload Too Large ({self.content_length} bytes)")
            if self.content_length > 0:
                self.state = ParseState.BODY_IDENTITY
            else:
                self.state = ParseState.COMPLETE
        else:
            # No body expected for standard GET/HEAD/DELETE without Content-Length
            self.content_length = 0
            self.state = ParseState.COMPLETE

    def _build_request(self) -> HttpRequest:
        return HttpRequest(
            method=self.method,
            target=self.target,
            version=self.version,
            headers=self.headers,
            body=bytes(self.body),
            client_address=self.client_address,
        )

    def _fail(self, status_code: int, message: str):
        self.state = ParseState.ERROR
        self.error_code = status_code
        self.error_msg = message
        raise HttpParserError(status_code, message)
