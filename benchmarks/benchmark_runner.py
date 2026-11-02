"""
High-Performance Concurrent HTTP Benchmark Client.
Simulates concurrent clients firing Keep-Alive HTTP requests over raw TCP sockets,
computing throughput, latency distributions (p50, p90, p95, p99), and success rates.
"""

import socket
import time
import statistics
import concurrent.futures
import json
import argparse
from typing import List, Dict, Any


def run_worker_task(
    host: str,
    port: int,
    path: str,
    requests_per_worker: int,
    keep_alive: bool = True,
) -> List[float]:
    """
    Execute requests sequentially over a persistent socket connection (Keep-Alive).
    Returns list of response latencies in milliseconds.
    """
    latencies: List[float] = []
    req_bytes = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n".encode("ascii")

    sock: socket.socket | None = None

    for _ in range(requests_per_worker):
        t0 = time.perf_counter()
        try:
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(3.0)
                sock.connect((host, port))

            sock.sendall(req_bytes)

            # Read response headers + body
            resp = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp.extend(chunk)
                if b"\r\n\r\n" in resp:
                    # For benchmark GET /ping with known small length, header complete is enough
                    break

            latencies.append((time.perf_counter() - t0) * 1000.0)

            if not keep_alive:
                sock.close()
                sock = None

        except Exception:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None

    if sock:
        try:
            sock.close()
        except OSError:
            pass

    return latencies


def run_benchmark(
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/ping",
    concurrency: int = 20,
    total_requests: int = 5000,
    keep_alive: bool = True,
) -> Dict[str, Any]:
    """
    Run multi-threaded concurrent benchmark.
    """
    requests_per_worker = total_requests // concurrency
    total_planned = requests_per_worker * concurrency

    print(f"\n========================================================")
    print(f"[*] Starting Benchmark against http://{host}:{port}{path}")
    print(f"   Concurrency: {concurrency} workers")
    print(f"   Total Requests: {total_planned} ({requests_per_worker}/worker)")
    print(f"   Keep-Alive: {keep_alive}")
    print(f"========================================================")

    start_wall_time = time.perf_counter()

    all_latencies: List[float] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(run_worker_task, host, port, path, requests_per_worker, keep_alive)
            for _ in range(concurrency)
        ]
        for f in concurrent.futures.as_completed(futures):
            all_latencies.extend(f.result())

    total_duration = time.perf_counter() - start_wall_time
    successful_reqs = len(all_latencies)
    rps = successful_reqs / total_duration if total_duration > 0 else 0

    if not all_latencies:
        print("[!] All requests failed.")
        return {}

    all_latencies.sort()
    p50 = statistics.median(all_latencies)
    p90 = all_latencies[int(len(all_latencies) * 0.90)]
    p95 = all_latencies[int(len(all_latencies) * 0.95)]
    p99 = all_latencies[int(len(all_latencies) * 0.99)]
    mean_lat = statistics.mean(all_latencies)
    min_lat = all_latencies[0]
    max_lat = all_latencies[-1]

    results = {
        "concurrency": concurrency,
        "total_requests": total_planned,
        "successful_requests": successful_reqs,
        "total_duration_sec": round(total_duration, 4),
        "requests_per_sec": round(rps, 2),
        "latency_mean_ms": round(mean_lat, 2),
        "latency_p50_ms": round(p50, 2),
        "latency_p90_ms": round(p90, 2),
        "latency_p95_ms": round(p95, 2),
        "latency_p99_ms": round(p99, 2),
        "latency_min_ms": round(min_lat, 2),
        "latency_max_ms": round(max_lat, 2),
    }

    print("\n[+] Benchmark Results:")
    print(f"   Throughput:          {rps:,.2f} req/sec")
    print(f"   Completed in:        {total_duration:.3f} seconds")
    print(f"   Success Rate:        {successful_reqs}/{total_planned} ({successful_reqs/total_planned*100:.1f}%)")
    print(f"   Latency Mean:        {mean_lat:.2f} ms")
    print(f"   Latency P50:         {p50:.2f} ms")
    print(f"   Latency P90:         {p90:.2f} ms")
    print(f"   Latency P95:         {p95:.2f} ms")
    print(f"   Latency P99:         {p99:.2f} ms")
    print(f"   Latency Min / Max:   {min_lat:.2f} ms / {max_lat:.2f} ms")
    print("========================================================\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concurrent HTTP Benchmark Tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--path", default="/ping")
    parser.add_argument("-c", "--concurrency", type=int, default=20)
    parser.add_argument("-n", "--requests", type=int, default=3000)
    parser.add_argument("--no-keep-alive", action="store_true")
    parser.add_argument("--output", default="benchmarks/results.json")

    args = parser.parse_args()
    res = run_benchmark(
        host=args.host,
        port=args.port,
        path=args.path,
        concurrency=args.concurrency,
        total_requests=args.requests,
        keep_alive=not args.no_keep_alive,
    )

    if res and args.output:
        with open(args.output, "w") as f:
            json.dump(res, f, indent=2)
        print(f"Results saved to {args.output}")
