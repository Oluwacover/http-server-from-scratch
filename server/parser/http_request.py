"""
RFC 9110 Compliant HTTP Request Data Structure.
Represents an incoming HTTP request with parsed method, URI, headers, and body.
"""

from typing import Dict, Optional, Any
import json
from urllib.parse import parse_qs, urlsplit


class CaseInsensitiveDict(dict):
    """
    A dictionary with case-insensitive string keys as required by RFC 9110 Section 5.1.
    Headers in HTTP are case-insensitive.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._key_map: Dict[str, str] = {k.lower(): k for k in self.keys()}

    def __setitem__(self, key: str, value: str):
        lower = key.lower()
        if lower in self._key_map:
            old_key = self._key_map[lower]
            super().__delitem__(old_key)
        self._key_map[lower] = key
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> str:
        lower = key.lower()
        if lower in self._key_map:
            return super().__getitem__(self._key_map[lower])
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            return key.lower() in self._key_map
        return False

    def get(self, key: str, default: Any = None) -> Any:
        lower = key.lower()
        if lower in self._key_map:
            return super().__getitem__(self._key_map[lower])
        return default


class HttpRequest:
    """
    Encapsulates an incoming HTTP/1.1 request.
    """
    def __init__(
        self,
        method: str,
        target: str,
        version: str = "HTTP/1.1",
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
        client_address: Optional[tuple] = None,
    ):
        self.method: str = method.upper()
        self.target: str = target
        self.version: str = version
        self.headers: CaseInsensitiveDict = CaseInsensitiveDict(headers or {})
        self.body: bytes = body
        self.client_address = client_address

        # Parse target URI into path and query
        split_result = urlsplit(target)
        self.path: str = split_result.path or "/"
        self.query_string: str = split_result.query or ""
        self._query_params: Optional[Dict[str, list]] = None

        # Route matching parameters (e.g. /users/{id})
        self.path_params: Dict[str, str] = {}

    @property
    def query_params(self) -> Dict[str, list]:
        if self._query_params is None:
            self._query_params = parse_qs(self.query_string)
        return self._query_params

    def query(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Convenience method to retrieve single query parameter value."""
        values = self.query_params.get(key)
        if values:
            return values[0]
        return default

    @property
    def text(self) -> str:
        """Decode body bytes to UTF-8 text."""
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse body as JSON."""
        return json.loads(self.text)

    @property
    def content_length(self) -> int:
        """Returns Content-Length header as int, or 0 if missing."""
        val = self.headers.get("Content-Length")
        if val is not None and val.isdigit():
            return int(val)
        return 0

    @property
    def is_keep_alive(self) -> bool:
        """
        RFC 9110 Section 9.6: In HTTP/1.1, connections are persistent by default
        unless 'Connection: close' is explicitly declared.
        In HTTP/1.0, connections close unless 'Connection: keep-alive' is present.
        """
        connection_header = self.headers.get("Connection", "").lower()
        if self.version == "HTTP/1.1":
            return connection_header != "close"
        elif self.version == "HTTP/1.0":
            return connection_header == "keep-alive"
        return False

    def __repr__(self) -> str:
        return f"<HttpRequest {self.method} {self.target} {self.version} body_len={len(self.body)}>"
