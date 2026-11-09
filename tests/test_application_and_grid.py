"""Real local HTTP/process checks and offline deployment descriptor checks."""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_runtime import MemoryStore

from runtime.executor.executor import process_executor_command
from runtime.monitor.monitor import collect

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.app = load_module(
            "local_application", ROOT / "deployment/examples/local/resilience_system.py"
        )
        self.dependency = ThreadingHTTPServer(
            ("127.0.0.1", 0), self.app.dependency_handler(self.app.DependencyState())
        )
        threading.Thread(target=self.dependency.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            worker_port = probe.getsockname()[1]
        self.environment = patch.dict(
            os.environ,
            {
                "DEPENDENCY_URL": f"http://127.0.0.1:{self.dependency.server_port}",
                "WORKER_PORT": str(worker_port),
            },
        )
        self.environment.start()
        try:
            self.supervisor = self.app.SupervisorState()
            self.gateway = ThreadingHTTPServer(
                ("127.0.0.1", 0), self.app.supervisor_handler(self.supervisor)
            )
            threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        except BaseException:
            self.dependency.shutdown()
            self.dependency.server_close()
            self.environment.stop()
            raise
        self.url = f"http://127.0.0.1:{self.gateway.server_port}"

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.supervisor.stop_worker()
        self.dependency.shutdown()
        self.dependency.server_close()
        self.environment.stop()

    def test_http_metrics_real_worker_restart_and_feedback(self):
        generation = self.supervisor.generation
        self.supervisor.stop_worker()
        handler = {
            "RESTART": {
                "mode": "command",
                "argv": [
                    sys.executable,
                    str(ROOT / "deployment/examples/local/control_action.py"),
                    "--gateway",
                    self.url,
                    "--action",
                    "RESTART_LOCAL_SERVICE",
                ],
                "timeout_seconds": 5,
            }
        }
        outcome = process_executor_command(
            MemoryStore(),
            dict(type="RESTART", domain="application_service", action_id="real-restart"),
            handler,
        )
        self.assertEqual(outcome["feedback_type"], "action_done")
        self.assertTrue(outcome["feedback_payload"]["verification"]["verified"])
        self.assertGreater(self.supervisor.generation, generation)
        stamp, metrics = collect(
            dict(
                type="http_json",
                url=self.url + "/metrics",
                event_time_field="timestamp",
                parser=dict(metrics=[dict(metric="healthy", path="local_service_healthy")]),
            )
        )
        self.assertEqual(metrics, [("healthy", 1)])
        self.assertGreater(stamp, 0)

    def test_http_monitor_example_reads_actual_service(self):
        env = dict(os.environ, METRICS_URL=self.url + "/metrics")
        result = subprocess.run(
            [
                sys.executable,
                "deployment/examples/monitors/probe.py",
                "--config",
                "config/examples/monitor-http.json",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        record = json.loads(result.stdout)
        self.assertEqual(record["domain"], "service_health")
        self.assertEqual(record["metrics"]["local_service_healthy"], 1)
        self.assertGreater(record["event_time"], 0)

    def test_fallback_requires_a_genuinely_cached_response(self):
        self.supervisor.set_fallback(True)
        status, payload = self.supervisor.execute()
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "no_cached_response")
        self.supervisor.set_fallback(False)
        status, primary = self.supervisor.execute()
        self.assertEqual(status, 200)
        self.supervisor.stop_worker()
        self.supervisor.set_fallback(True)
        status, cached = self.supervisor.execute()
        self.assertEqual(status, 200)
        self.assertEqual(cached["value"], primary["value"])
        self.assertEqual(cached["route"], "fallback")


class GridTests(unittest.TestCase):
    def test_grid_profiles_are_self_contained_and_have_no_ceph_dependency(self):
        folder = ROOT / "deployment/examples/grid5000"
        sys.path.insert(0, str(folder))
        try:
            deploy = load_module("grid_deploy", folder / "deploy.py")
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as directory:
            nodes = Path(directory) / "nodes"
            nodes.write_text("gros-1.nancy.grid5000.fr\n")
            for profile in ("syntony", "cep"):
                out = Path(directory) / profile
                inventory = deploy.prepare(nodes, out, "example/syntony:0.1.0", profile)
                self.assertEqual(inventory["profile"], profile)
                config = json.loads((out / "payload/compose.json").read_text())
                if profile == "cep":
                    self.assertEqual(set(config["services"]), {"redis", "cep"})
                else:
                    self.assertIn("executor", config["services"])
                for service in config["services"].values():
                    self.assertNotIn("build", service)
                self.assertTrue(
                    (out / "payload/specifications/events/service_resilience.json").exists()
                )
                self.assertNotIn("ceph", json.dumps(config))
