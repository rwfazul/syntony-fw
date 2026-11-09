#!/usr/bin/env python3
"""HTTP dependency, supervised local process, and gateway used by C2."""

import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))
    return float(ordered[index])


def send_json(handler, status, payload):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def request_json(url, method="GET", timeout=1.0):
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError):
        return 0, {}


class DependencyState:
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "healthy"
        self.calls = 0

    def set_mode(self, mode):
        if mode not in {"healthy", "down"}:
            raise ValueError(mode)
        with self.lock:
            self.mode = mode

    def snapshot(self):
        with self.lock:
            return {"mode": self.mode, "healthy": self.mode == "healthy", "calls": self.calls}

    def data(self):
        with self.lock:
            self.calls += 1
            mode = self.mode
        if mode == "down":
            return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "dependency_unavailable"}
        return HTTPStatus.OK, {"value": socket.gethostname(), "source": "dependency"}


def dependency_handler(state):
    class DependencyHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/health":
                snapshot = state.snapshot()
                send_json(
                    self,
                    HTTPStatus.OK if snapshot["healthy"] else HTTPStatus.SERVICE_UNAVAILABLE,
                    snapshot,
                )
                return
            if path == "/data":
                status, payload = state.data()
                send_json(self, status, payload)
                return
            if path == "/state":
                send_json(self, HTTPStatus.OK, state.snapshot())
                return
            send_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/control/mode":
                send_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            mode = urllib.parse.parse_qs(parsed.query).get("mode", [""])[0]
            try:
                state.set_mode(mode)
            except ValueError:
                send_json(self, HTTPStatus.BAD_REQUEST, {"error": "invalid_mode"})
                return
            send_json(self, HTTPStatus.OK, state.snapshot())

    return DependencyHandler


def worker_handler(dependency_url):
    class WorkerHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/health":
                send_json(self, HTTPStatus.OK, {"healthy": True, "pid": os.getpid()})
                return
            if path != "/work":
                send_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            status, payload = request_json(f"{dependency_url}/data", timeout=0.8)
            if status != HTTPStatus.OK:
                send_json(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "dependency_failed", "dependency_status": status},
                )
                return
            send_json(self, HTTPStatus.OK, {"route": "primary", **payload})

    return WorkerHandler


class SupervisorState:
    def __init__(self):
        self.dependency_url = os.getenv("DEPENDENCY_URL", "http://dependency:8081").rstrip("/")
        self.worker_url = "http://127.0.0.1:" + os.getenv("WORKER_PORT", "8090")
        self.lock = threading.RLock()
        self.records = deque(maxlen=20000)
        self.arrivals = deque(maxlen=20000)
        self.fallback_enabled = False
        self.last_primary_payload = None
        self.generation = 0
        self.syntony_restart_count = 0
        self.inflight = 0
        self.worker_process = None
        self._spawn_worker()
        atexit.register(self.stop_worker)

    def _spawn_worker(self):
        env = os.environ.copy()
        env["DEPENDENCY_URL"] = self.dependency_url
        self.worker_process = subprocess.Popen(
            [sys.executable, __file__, "worker"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.worker_healthy():
                self.generation += 1
                return
            time.sleep(0.05)
        raise RuntimeError("local worker did not become healthy")

    def worker_healthy(self):
        process = self.worker_process
        if process is None or process.poll() is not None:
            return False
        status, _ = request_json(f"{self.worker_url}/health", timeout=0.2)
        return status == HTTPStatus.OK

    def dependency_healthy(self):
        status, payload = request_json(f"{self.dependency_url}/health", timeout=0.3)
        return status == HTTPStatus.OK and payload.get("healthy") is True

    def stop_worker(self):
        process = self.worker_process
        self.worker_process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def restart_worker(self, *, syntony_command):
        with self.lock:
            pre_healthy = self.worker_healthy()
            self.stop_worker()
            self._spawn_worker()
            if syntony_command:
                self.syntony_restart_count += 1
            return {
                "pre_worker_healthy": pre_healthy,
                "post_worker_healthy": self.worker_healthy(),
                "generation": self.generation,
                "syntony_restart_count": self.syntony_restart_count,
            }

    def set_fallback(self, enabled):
        with self.lock:
            self.fallback_enabled = bool(enabled)

    def set_dependency_mode(self, mode):
        query = urllib.parse.urlencode({"mode": mode})
        return request_json(f"{self.dependency_url}/control/mode?{query}", method="POST")

    def record_arrival(self):
        with self.lock:
            self.arrivals.append({"timestamp": time.time()})
            self.inflight += 1

    def record_completion(self, *, status, latency_ms, route):
        with self.lock:
            self.inflight = max(0, self.inflight - 1)
            self.records.append(
                {
                    "timestamp": time.time(),
                    "status": int(status),
                    "latency_ms": float(latency_ms),
                    "route": route,
                }
            )

    def execute(self):
        self.record_arrival()
        started = time.perf_counter()
        with self.lock:
            fallback_enabled = self.fallback_enabled
        if fallback_enabled:
            with self.lock:
                cached = dict(self.last_primary_payload) if self.last_primary_payload else None
            status = HTTPStatus.OK if cached else HTTPStatus.SERVICE_UNAVAILABLE
            payload = (
                {**cached, "route": "fallback"}
                if cached
                else {"route": "failed", "error": "no_cached_response"}
            )
        else:
            status, payload = request_json(f"{self.worker_url}/work", timeout=1.2)
            if status != HTTPStatus.OK:
                status = HTTPStatus.SERVICE_UNAVAILABLE
                payload = {"route": "failed", "error": "local_or_dependency_failure"}
            else:
                with self.lock:
                    self.last_primary_payload = dict(payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        route = payload.get("route", "failed")
        self.record_completion(status=status, latency_ms=latency_ms, route=route)
        return status, {**payload, "latency_ms": round(latency_ms, 3)}

    def snapshot(self):
        with self.lock:
            fallback_enabled = self.fallback_enabled
            generation = self.generation
            restart_count = self.syntony_restart_count
            inflight = self.inflight
        return {
            "fallback_enabled": fallback_enabled,
            "local_service_healthy": self.worker_healthy(),
            "dependency_healthy": self.dependency_healthy(),
            "generation": generation,
            "syntony_restart_count": restart_count,
            "inflight": inflight,
        }

    def metrics(self, window_seconds):
        cutoff = time.time() - window_seconds
        with self.lock:
            arrivals = [row for row in self.arrivals if row["timestamp"] >= cutoff]
            records = [row for row in self.records if row["timestamp"] >= cutoff]
        snapshot = self.snapshot()
        completed = len(records)
        successes = sum(row["status"] == HTTPStatus.OK for row in records)
        fallback = sum(row["route"] == "fallback" for row in records)
        return {
            "timestamp": time.time(),
            "window_seconds": window_seconds,
            "request_rps": round(len(arrivals) / window_seconds, 3),
            "success_rate": round(successes / completed, 4) if completed else 1.0,
            "error_rate": round((completed - successes) / completed, 4) if completed else 0.0,
            "user_p95_ms": round(percentile([row["latency_ms"] for row in records], 0.95), 3),
            "fallback_rate": round(fallback / completed, 4) if completed else 0.0,
            "fallback_enabled": int(snapshot["fallback_enabled"]),
            "local_service_healthy": int(snapshot["local_service_healthy"]),
            "dependency_healthy": int(snapshot["dependency_healthy"]),
            "syntony_restart_count": snapshot["syntony_restart_count"],
            "generation": snapshot["generation"],
            "inflight": snapshot["inflight"],
        }


def supervisor_handler(state):
    class SupervisorHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                send_json(self, HTTPStatus.OK, {"supervisor": "healthy", **state.snapshot()})
                return
            if parsed.path == "/state":
                send_json(self, HTTPStatus.OK, state.snapshot())
                return
            if parsed.path == "/metrics":
                window = float(urllib.parse.parse_qs(parsed.query).get("window", ["2"])[0])
                send_json(self, HTTPStatus.OK, state.metrics(window))
                return
            if parsed.path == "/work":
                status, payload = state.execute()
                send_json(self, status, payload)
                return
            send_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/control/fallback":
                enabled = query.get("enabled", ["0"])[0] == "1"
                state.set_fallback(enabled)
                send_json(self, HTTPStatus.OK, state.snapshot())
                return
            if parsed.path == "/control/restart-local":
                send_json(self, HTTPStatus.OK, state.restart_worker(syntony_command=True))
                return
            if parsed.path == "/inject/dependency":
                status, payload = state.set_dependency_mode(query.get("mode", [""])[0])
                send_json(self, status or HTTPStatus.BAD_GATEWAY, payload)
                return
            if parsed.path == "/inject/crash-local":
                state.stop_worker()
                send_json(self, HTTPStatus.OK, state.snapshot())
                return
            if parsed.path == "/inject/recover-local":
                if not state.worker_healthy():
                    payload = state.restart_worker(syntony_command=False)
                else:
                    payload = {"post_worker_healthy": True, "generation": state.generation}
                send_json(self, HTTPStatus.OK, payload)
                return
            send_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

    return SupervisorHandler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("dependency", "worker", "supervisor"))
    args = parser.parse_args()
    if args.role == "dependency":
        server = ReusableThreadingHTTPServer(
            ("0.0.0.0", int(os.getenv("DEPENDENCY_PORT", "8081"))),
            dependency_handler(DependencyState()),
        )
    elif args.role == "worker":
        dependency_url = os.getenv("DEPENDENCY_URL", "http://dependency:8081").rstrip("/")
        server = ReusableThreadingHTTPServer(
            ("127.0.0.1", int(os.getenv("WORKER_PORT", "8090"))), worker_handler(dependency_url)
        )
    else:
        server = ReusableThreadingHTTPServer(
            ("0.0.0.0", int(os.getenv("GATEWAY_PORT", "8080"))),
            supervisor_handler(SupervisorState()),
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
