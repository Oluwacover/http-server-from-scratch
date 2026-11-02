"""
Unit tests for RFC 9110 Finite State Machine HTTP Parser.
Tests fragmentation, pipelining, chunked transfer encoding, and security limits.
"""

import pytest
from server.parser.state_machine import HttpParser, HttpParserError
from server.parser.http_response import HttpResponse
from server.routing.router import Router
from server.parser.http_request import HttpRequest


def test_basic_get_request():
    parser = HttpParser()
    raw = b"GET /index.html?user=alice&lang=en HTTP/1.1\r\nHost: localhost:8080\r\nUser-Agent: test\r\n\r\n"
    req, consumed = parser.feed(raw)

    assert req is not None
    assert consumed == len(raw)
    assert req.method == "GET"
    assert req.path == "/index.html"
    assert req.query_string == "user=alice&lang=en"
    assert req.query("user") == "alice"
    assert req.query("lang") == "en"
    assert req.headers.get("host") == "localhost:8080"
    assert req.headers.get("User-Agent") == "test"
    assert req.version == "HTTP/1.1"
    assert req.is_keep_alive is True


def test_post_with_content_length():
    parser = HttpParser()
    payload = b'{"status": "ok", "count": 42}'
    raw = (
        f"POST /api/data HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload

    req, consumed = parser.feed(raw)

    assert req is not None
    assert consumed == len(raw)
    assert req.method == "POST"
    assert req.path == "/api/data"
    assert req.body == payload
    assert req.json() == {"status": "ok", "count": 42}


def test_byte_by_byte_fragmentation():
    """Simulate worst-case TCP packet fragmentation (1 byte per recv)."""
    parser = HttpParser()
    payload = b"Hello, Streaming HTTP World!"
    raw = (
        f"POST /echo HTTP/1.1\r\n"
        f"Host: test\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload

    req = None
    for b in raw:
        req, _ = parser.feed(bytes([b]))
        if req is not None:
            break

    assert req is not None
    assert req.method == "POST"
    assert req.path == "/echo"
    assert req.body == payload


def test_chunked_transfer_encoding():
    """Verify parsing of Transfer-Encoding: chunked payload."""
    parser = HttpParser()
    raw = (
        b"POST /upload HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        b"4\r\n"
        b"Wiki\r\n"
        b"6\r\n"
        b"pedia \r\n"
        b"E\r\n"
        b"in \r\n\r\nchunks.\r\n"
        b"0\r\n\r\n"
    )

    req, consumed = parser.feed(raw)

    assert req is not None
    assert req.method == "POST"
    assert req.body == b"Wikipedia in \r\n\r\nchunks."


def test_pipelined_requests():
    """Verify two sequential HTTP requests packed into a single TCP packet."""
    parser = HttpParser()
    req1 = b"GET /first HTTP/1.1\r\nHost: test\r\n\r\n"
    req2 = b"GET /second HTTP/1.1\r\nHost: test\r\n\r\n"
    combined = req1 + req2

    # First request
    r1, consumed1 = parser.feed(combined)
    assert r1 is not None
    assert r1.path == "/first"
    assert consumed1 == len(req1)

    # Reset parser and feed remainder
    parser.reset()
    r2, consumed2 = parser.feed(combined[consumed1:])
    assert r2 is not None
    assert r2.path == "/second"


def test_case_insensitive_headers():
    parser = HttpParser()
    raw = b"GET / HTTP/1.1\r\nhOsT: example.com\r\nCoNtEnT-TyPe: text/plain\r\n\r\n"
    req, _ = parser.feed(raw)

    assert req.headers["host"] == "example.com"
    assert req.headers["HOST"] == "example.com"
    assert req.headers["Content-Type"] == "text/plain"
    assert req.headers.get("content-type") == "text/plain"


def test_rfc_malformed_errors():
    # Invalid method characters
    with pytest.raises(HttpParserError) as exc:
        p = HttpParser()
        p.feed(b"GET@ / HTTP/1.1\r\n\r\n")
    assert exc.value.status_code == 400

    # Invalid HTTP version
    with pytest.raises(HttpParserError) as exc:
        p = HttpParser()
        p.feed(b"GET / HTTP/9.9\r\nHost: test\r\n\r\n")
    assert exc.value.status_code == 505

    # Whitespace before colon in header (forbidden in RFC 9110)
    with pytest.raises(HttpParserError) as exc:
        p = HttpParser()
        p.feed(b"GET / HTTP/1.1\r\nHost : invalid\r\n\r\n")
    assert exc.value.status_code == 400


def test_router_405_allow_header():
    router = Router()
    router.add_route("GET", "/users", lambda req: HttpResponse.text("List users"))
    router.add_route("POST", "/users", lambda req: HttpResponse.text("Create user"))

    # Attempt DELETE on /users
    req = HttpRequest(method="DELETE", target="/users")
    resp = router.dispatch(req)

    assert resp.status_code == 405
    assert "Allow" in resp.headers
    assert "GET" in resp.headers["Allow"]
    assert "POST" in resp.headers["Allow"]
