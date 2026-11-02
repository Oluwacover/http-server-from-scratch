"""
RFC 9110 Compliant HTTP Response Builder.
Constructs valid HTTP/1.1 response frames including status line, standard headers,
date stamps, and body payload serialization.
"""

from typing import Dict, Optional, Any
from email.utils import formatdate
import json

# RFC 9110 standard HTTP status codes and canonical reason phrases
STATUS_PHRASES: Dict[int, str] = {
    100: "Continue",
    101: "Switching Protocols",
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    411: "Length Required",
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    505: "HTTP Version Not Supported",
}


class HttpResponse:
    """
    HTTP Response Builder that serializes into wire-format HTTP/1.1 byte streams.
    """
    def __init__(
        self,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
        reason_phrase: Optional[str] = None,
        version: str = "HTTP/1.1",
    ):
        self.status_code: int = status_code
        self.reason_phrase: str = reason_phrase or STATUS_PHRASES.get(status_code, "Unknown")
        self.version: str = version
        self.headers: Dict[str, str] = headers.copy() if headers else {}
        self.body: bytes = body

        # Apply default standard headers
        if "Server" not in self.headers:
            self.headers["Server"] = "Python-RFC9110-Server/1.0"
        if "Date" not in self.headers:
            self.headers["Date"] = formatdate(timeval=None, localtime=False, usegmt=True)

    def set_header(self, key: str, value: str) -> "HttpResponse":
        """Set a single response header (chainable)."""
        self.headers[key] = value
        return self

    def set_body(self, body: bytes, content_type: Optional[str] = None) -> "HttpResponse":
        """Set raw bytes body and optional Content-Type."""
        self.body = body
        if content_type:
            self.headers["Content-Type"] = content_type
        return self

    def serialize(self, keep_alive: bool = True) -> bytes:
        """
        Serialize response to RFC 9110 compliant bytes ready to write to socket.
        """
        # Set Connection header based on keep_alive state
        self.headers["Connection"] = "keep-alive" if keep_alive else "close"

        # Determine if Content-Length should be omitted
        # RFC 9110 Section 6.4.1: 1xx, 204, and 304 MUST NOT include a content-length or body
        no_body_statuses = {204, 304} | set(range(100, 200))
        
        if self.status_code in no_body_statuses:
            self.body = b""
            self.headers.pop("Content-Length", None)
            self.headers.pop("Content-Type", None)
        else:
            self.headers["Content-Length"] = str(len(self.body))

        # Status Line
        status_line = f"{self.version} {self.status_code} {self.reason_phrase}\r\n"
        
        # Headers Block
        header_lines = [f"{k}: {v}\r\n" for k, v in self.headers.items()]
        
        # Combine Status Line + Headers + CRLF + Body
        return (status_line + "".join(header_lines) + "\r\n").encode("iso-8859-1") + self.body

    # --- Convenience Factory Methods ---

    @classmethod
    def text(cls, text: str, status_code: int = 200, content_type: str = "text/plain; charset=utf-8") -> "HttpResponse":
        body = text.encode("utf-8")
        resp = cls(status_code=status_code, body=body)
        resp.headers["Content-Type"] = content_type
        return resp

    @classmethod
    def html(cls, html: str, status_code: int = 200) -> "HttpResponse":
        return cls.text(html, status_code=status_code, content_type="text/html; charset=utf-8")

    @classmethod
    def json(cls, data: Any, status_code: int = 200) -> "HttpResponse":
        body = json.dumps(data, indent=2).encode("utf-8")
        resp = cls(status_code=status_code, body=body)
        resp.headers["Content-Type"] = "application/json; charset=utf-8"
        return resp

    @classmethod
    def raw(cls, data: bytes, content_type: str = "application/octet-stream", status_code: int = 200) -> "HttpResponse":
        resp = cls(status_code=status_code, body=data)
        resp.headers["Content-Type"] = content_type
        return resp

    @classmethod
    def redirect(cls, location: str, status_code: int = 302) -> "HttpResponse":
        resp = cls(status_code=status_code, body=b"")
        resp.headers["Location"] = location
        return resp

    @classmethod
    def error(cls, status_code: int, message: Optional[str] = None) -> "HttpResponse":
        phrase = STATUS_PHRASES.get(status_code, "Error")
        desc = message or phrase
        content = (
            f"<!DOCTYPE html><html><head><title>{status_code} {phrase}</title>"
            f"<style>body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#f8fafc;"
            f"display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}"
            f".box{{background:#1e293b;padding:2.5rem;border-radius:12px;box-shadow:0 10px 25px rgba(0,0,0,0.5);"
            f"border:1px solid #334155;text-align:center;max-width:480px;}}"
            f"h1{{font-size:3rem;margin:0;color:#f43f5e;}}p{{color:#94a3b8;margin-top:0.5rem;font-size:1.1rem;}}</style></head>"
            f"<body><div class=\"box\"><h1>{status_code}</h1><h2>{phrase}</h2><p>{desc}</p></div></body></html>"
        )
        return cls.html(content, status_code=status_code)
