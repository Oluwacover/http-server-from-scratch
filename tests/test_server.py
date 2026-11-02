"""
End-to-End Integration tests for HttpServerApp over real TCP sockets.
"""

import socket
import threading
import time
import json
import pytest
from server.app import HttpServerApp
from server.parser.http_response import HttpResponse


@pytest.fixture(scope="module")
def server():
    app = HttpServerApp(worker_threads=4)

    @app.get("/ping")
    def ping_handler(req):
        return HttpResponse.text("pong")

    @app.get("/hello/{name}")
    def hello_handler(req):
        name = req.path_params.get("name", "stranger")
        return HttpResponse.json({"message": f"Hello, {name}!"})

    @app.post("/echo")
    def echo_handler(req):
        data = req.json()
        return HttpResponse.json({"echoed": data})

    # Pick port 18080 for testing
    port = 18080
    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port),
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.3)  # Give server socket time to bind and listen

    yield "127.0.0.1", port

    if app.tcp_server:
        app.tcp_server.stop()


def test_e2e_ping_get(server):
    host, port = server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.sendall(b"GET /ping HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")

    resp_data = sock.recv(4096).decode("utf-8")
    sock.close()

    assert "HTTP/1.1 200 OK" in resp_data
    assert "pong" in resp_data


def test_e2e_path_parameter(server):
    host, port = server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.sendall(b"GET /hello/Alice HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")

    resp_data = sock.recv(4096).decode("utf-8")
    sock.close()

    assert "HTTP/1.1 200 OK" in resp_data
    assert '"message": "Hello, Alice!"' in resp_data


def test_e2e_post_echo(server):
    host, port = server
    payload = json.dumps({"test_key": "custom_val"}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    req = (
        f"POST /echo HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii") + payload
    sock.sendall(req)

    resp_data = sock.recv(4096).decode("utf-8")
    sock.close()

    assert "HTTP/1.1 200 OK" in resp_data
    assert '"test_key": "custom_val"' in resp_data


def test_e2e_keep_alive_multiple_requests(server):
    """Test 3 sequential HTTP requests across a single persistent TCP socket."""
    host, port = server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))

    for i in range(3):
        sock.sendall(b"GET /ping HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
        data = sock.recv(4096).decode("utf-8")
        assert "HTTP/1.1 200 OK" in data
        assert "pong" in data

    sock.close()
