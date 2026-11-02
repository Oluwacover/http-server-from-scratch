"""
Main Application Entrypoint.
Initializes the RFC 9110 HTTP/1.1 Server with routes, static file serving, and middleware.
"""

import os
import sys
import time
from server.app import HttpServerApp
from server.parser.http_response import HttpResponse

app = HttpServerApp(worker_threads=16, keep_alive_timeout=5.0)

# Mount static asset directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount_static("/static", static_dir)
app.enable_cors()

start_timestamp = time.time()


@app.get("/")
def index_handler(request):
    """Serve the root dashboard."""
    index_file = os.path.join(static_dir, "index.html")
    with open(index_file, "r", encoding="utf-8") as f:
        return HttpResponse.html(f.read())


@app.get("/ping")
def ping_handler(request):
    """Ultra-low-latency endpoint for throughput benchmarks."""
    return HttpResponse.text("pong")


@app.get("/api/stats")
def stats_handler(request):
    """Server internal telemetry and runtime metrics."""
    pool = app.tcp_server.pool if app.tcp_server else None
    return HttpResponse.json({
        "status": "healthy",
        "protocol": "HTTP/1.1 (RFC 9110)",
        "uptime_seconds": round(time.time() - start_timestamp, 1),
        "thread_pool": {
            "worker_threads": pool.num_threads if pool else app.worker_threads,
            "active_workers": pool.active_count if pool else 0,
            "queued_tasks": pool.queue_size if pool else 0,
        },
        "features": [
            "Raw TCP POSIX/Winsock Sockets",
            "Finite State Machine Byte Parser",
            "Keep-Alive Connection Persistence",
            "Chunked Transfer-Encoding",
            "Thread Pool Concurrency",
            "Path Parameter Routing",
            "Static ETag Caching",
        ]
    })


@app.get("/api/users/{id}")
def user_handler(request):
    """Demonstrates parameterized routing /api/users/{id}."""
    user_id = request.path_params.get("id")
    return HttpResponse.json({
        "id": user_id,
        "name": f"User {user_id}",
        "role": "Engineer",
        "query_params": request.query_params,
    })


@app.post("/api/echo")
def echo_handler(request):
    """Echoes incoming JSON payload."""
    try:
        body_json = request.json()
    except Exception:
        return HttpResponse.error(400, "Invalid JSON payload in request body")
    return HttpResponse.json({
        "message": "Payload received successfully",
        "echo": body_json,
    })


@app.get("/api/slow")
def slow_handler(request):
    """Simulates I/O latency to demonstrate multi-threaded concurrency."""
    time.sleep(0.1)  # 100ms
    return HttpResponse.text("Completed after 100ms non-blocking sleep")


@app.get("/error/{code}")
def trigger_error(request):
    """Trigger standard HTTP error code response."""
    code_str = request.path_params.get("code", "400")
    code = int(code_str) if code_str.isdigit() else 400
    return HttpResponse.error(code)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"\n[+] Starting RFC 9110 HTTP/1.1 Server...")
    print(f"[+] Listening at: http://{host}:{port}/")
    print(f"[+] Press Ctrl+C to terminate cleanly.\n")
    try:
        app.run(host=host, port=port)
    except KeyboardInterrupt:
        print("\n[+] Exiting gracefully...")
