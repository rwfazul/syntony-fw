"""Public APIs, executable catalog extensions and reusable deployment assets."""

import importlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_runtime import MemoryStore

from runtime import load_syntony_config
from runtime.cep import compile_pattern
from runtime.internal_state import get_execution_state

ROOT = Path(__file__).resolve().parents[1]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DistributionTests(unittest.TestCase):
    def test_public_interfaces_without_starting_daemons(self):
        for role, symbol in {
            "monitor": "collect",
            "composer": "WatermarkManager",
            "cep": "compile_pattern",
            "coordinator": "check_constraints",
            "executor": "process_executor_command",
        }.items():
            package = importlib.import_module("runtime." + role)
            self.assertIn(symbol, package.__all__)
            self.assertTrue(callable(getattr(package, symbol)))
        self.assertEqual(get_execution_state(MemoryStore()), {})

    def test_extended_catalog_has_os_domain_and_effective_guard(self):
        config = load_syntony_config(ROOT / "config/service-resilience-host-guard.json")
        self.assertIn("operating_system", config["cep"]["watermark_domains"])
        spec = next(
            p
            for p in config["cep"]["patterns"]
            if p["name"] == "guard_restart_during_host_pressure"
        )
        pattern = compile_pattern(spec)
        event = dict(domain="operating_system", event_type="host_load_high", tick=1)
        self.assertTrue(pattern["match"]([event], {1: [event]}, 1))
        self.assertFalse(pattern["match"]([], {1: []}, 1))
        self.assertEqual(pattern["blocked_action_types"], ["RESTART_LOCAL_SERVICE"])

    def test_host_guard_release_requires_a_round_without_high_load(self):
        config = load_syntony_config(ROOT / "config/service-resilience-host-guard.json")
        release = compile_pattern(
            next(p for p in config["cep"]["patterns"] if p["coord_type"] == "E_unblock")
        )
        normal = dict(domain="operating_system", event_type="host_load_normal", tick=1)
        high = dict(domain="operating_system", event_type="host_load_high", tick=1)
        self.assertTrue(release["match"]([normal], {1: [normal]}, 1))
        self.assertFalse(release["match"]([normal, high], {1: [normal, high]}, 1))

    def test_observation_only_instances_validate_through_cli(self):
        for path in sorted((ROOT / "config/examples").glob("*.json")):
            result = subprocess.run(
                [sys.executable, "-m", "runtime", "validate", "--config", str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            report = json.loads(result.stdout)
            self.assertTrue(report["valid"])
            self.assertEqual(report["patterns"], [])

    def test_compose_builds_module_dockerfiles(self):
        config = json.loads((ROOT / "deployment/examples/local/compose.json").read_text())
        override = json.loads(
            (ROOT / "deployment/examples/local/host-guard.compose.json").read_text()
        )
        services = config["services"]
        services.update(
            {
                name: service
                for name, service in override["services"].items()
                if name not in services
            }
        )
        builders = {}
        for service in services.values():
            if "build" in service:
                self.assertNotIn(service["image"], builders)
                builders[service["image"]] = service["build"]["dockerfile"]
        for name, service in services.items():
            if name == "redis":
                continue
            role = name.split("_")[0] if name not in ("gateway", "dependency") else "example"
            dockerfile = ROOT / builders[service["image"]]
            self.assertTrue(dockerfile.is_file())
            self.assertIn("-" + role + ":", service["image"])
            if role != "example":
                self.assertIn('"' + role + '"', dockerfile.read_text())
                self.assertTrue((ROOT / "runtime" / role / "requirements.txt").is_file())

    def test_bundle_includes_operational_assets_and_excludes_private_outputs(self):
        module = load_file("public_bundler", ROOT / "scripts/bundle.py")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.tar.gz"
            manifest = module.bundle(path)
            with tarfile.open(path) as archive:
                names = set(archive.getnames())
            for required in [
                "LICENSE",
                "docker-compose.yml",
                "runtime/cep/Dockerfile",
                "deployment/examples/grid5000/ceph/cluster.py",
                "docs/extending-the-catalog.md",
                "docs/state-and-policies.md",
                "config/README.md",
                "scripts/README.md",
                "tests/README.md",
                "deployment/README.md",
                "deployment/examples/monitors/probe.py",
            ]:
                self.assertIn(required, names)
                self.assertIn(required, manifest)
            self.assertTrue(path.with_suffix(".gz.sha256").exists())
            self.assertFalse(
                any(
                    name.startswith(
                        (
                            "artifacts/",
                            "output/",
                            "generated/",
                            "tools/",
                            ".local/",
                            ".ruff_cache/",
                            ".pytest_cache/",
                        )
                    )
                    for name in names
                )
            )


class CephDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.prepare = load_file(
            "public_ceph_prepare", ROOT / "deployment/examples/grid5000/ceph/prepare.py"
        )
        self.nodes = [f"gros-{i}.nancy.grid5000.fr" for i in range(1, 7)]
        self.ips = [f"192.0.2.{i}" for i in range(1, 7)]

    def test_allocation_rejects_wrong_node_count_or_cluster(self):
        self.prepare.validate_allocation(self.nodes, "nancy", "gros")
        with self.assertRaises(ValueError):
            self.prepare.validate_allocation(self.nodes[:-1], "nancy", "gros")
        with self.assertRaises(ValueError):
            self.prepare.validate_allocation(self.nodes, "nantes", "ecotype")

    def test_ceph_topology_and_syntony_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            for layout in ("collocated", "distributed"):
                out = Path(directory) / layout
                inv = self.prepare.generate(self.nodes, self.ips, out, layout=layout)
                self.assertEqual(inv["replication"], 3)
                self.assertEqual(inv["storage"], self.nodes[3:])
                self.assertEqual(
                    len(set(inv["syntony_assignments"].values())),
                    1 if layout == "collocated" else 6,
                )
                self.assertIn("osd pool default size = 3", (out / "ceph.conf").read_text())
                for host in self.nodes:
                    self.assertTrue(json.loads((out / (host + ".json")).read_text())["services"])
                for host in inv["syntony_hosts"]:
                    config = json.loads((out / ("syntony-" + host + ".json")).read_text())
                    for name, service in config["services"].items():
                        self.assertEqual(service["network_mode"], "host")
                        self.assertNotIn("build", service)
                        self.assertNotIn("ports", service)
                        if name != "redis":
                            self.assertEqual(
                                service["environment"]["REDIS_URL"], "redis://192.0.2.1:6379/0"
                            )
                            self.assertNotIn("{module}", service["image"])
                    self.assertNotIn("tools/ceph_", json.dumps(config))

    def test_role_images_cannot_overwrite_one_another(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.prepare.generate(
                    self.nodes, self.ips, Path(directory) / "bad", image="same:dev"
                )
