"""Test configuration types, stable-round provenance, input validation and Grid ownership."""

import copy
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_runtime import MemoryStore

from runtime.cep import close_stable_rounds, compile_pattern
from runtime.cep.cep import initial_round_position
from runtime.common import load_syntony_config as runtime_config
from runtime.composer.utils import parse_metric_event
from runtime.configuration import load_syntony_config, validate
from runtime.monitor import collect

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationContractsTests(unittest.TestCase):
    def setUp(self):
        self.config = load_syntony_config()

    def test_reject_argv_string_fractional_window_boolean_flags_and_unknown_scope(self):
        for failure in (
            "argv",
            "window",
            "retention",
            "force",
            "scope",
            "operator",
            "empty_all",
            "local_window",
            "metric_path",
        ):
            config = copy.deepcopy(self.config)
            pattern = config["cep"]["patterns"][0]
            rule = config["domains"]["application_service"]["composer"]["rules"][0]
            if failure == "argv":
                config["executor"]["actions"]["ENABLE_FALLBACK"]["argv"] = "echo success"
            if failure == "window":
                pattern["within_ticks"] = 1.5
            if failure == "retention":
                config["cep"]["tick"]["retention_ticks"] = 2.5
            if failure == "force":
                pattern["force_process"] = "false"
            if failure == "scope":
                config["coordinator"]["dedupe_rules"][0]["scope"] = "globla"
            if failure == "operator":
                rule["operator"] = "gteq"
            if failure == "empty_all":
                rule.update(type="all", conditions=[])
            if failure == "local_window":
                rule.update(type="metric_window", within_seconds=100, min_matches=1)
            if failure == "metric_path":
                config["domains"]["application_service"]["monitor"]["sources"][0]["parser"][
                    "metrics"
                ][0].pop("path")
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                validate(config)

    def test_runtime_configuration_is_frozen_until_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(self.config))
            runtime_config.cache_clear()
            try:
                with patch.dict(os.environ, {"SYNTONY_CONFIG_PATH": str(path)}):
                    first = runtime_config()
                    changed = copy.deepcopy(self.config)
                    changed["cep"]["tick"]["seconds"] = 2
                    path.write_text(json.dumps(changed))
                    self.assertEqual(
                        runtime_config()["cep"]["tick"]["seconds"], first["cep"]["tick"]["seconds"]
                    )
                    self.assertEqual(load_syntony_config(path)["cep"]["tick"]["seconds"], 2)
            finally:
                runtime_config.cache_clear()


class TemporalContractsTests(unittest.TestCase):
    def test_historical_match_emits_the_closing_round_not_a_future_anchor(self):
        store = MemoryStore()
        pattern = compile_pattern(
            dict(
                name="p",
                type="count_event",
                event_type="x",
                within_ticks=2,
                count=1,
                coord_type="E_trg",
            )
        )
        retained = dict(domain="a", event_type="x", event_time=0.2, tick=0)
        future = dict(domain="b", event_type="future", event_time=5.2, tick=5)
        last = close_stable_rounds(
            store, [pattern], {0: [retained], 5: [future]}, 1, 8, 2, -1, fallback_event=future
        )
        self.assertEqual(last, 1)
        rows = store.streams["events:coord"]
        self.assertEqual([row["tick"] for row in rows], [0, 1])
        self.assertTrue(all(row["domain"] == "a" and row["base_event"] == "x" for row in rows))
        self.assertEqual(rows[1]["tick_end"], 2)

    def test_passthrough_provenance_uses_the_matching_domain(self):
        store = MemoryStore()
        pattern = compile_pattern(
            dict(
                name="guard",
                type="passthrough_event",
                domain="os",
                event_type="pressure",
                coord_type="E_block",
            )
        )
        rows = [
            dict(domain="os", event_type="pressure", tick=0, event_time=0.1),
            dict(domain="app", event_type="other", tick=0, event_time=0.2),
        ]
        close_stable_rounds(store, [pattern], {0: rows}, 1, 8, 1, -1)
        self.assertEqual(store.streams["events:coord"][0]["domain"], "os")

    def test_count_pattern_honors_its_explicit_domain(self):
        pattern = compile_pattern(
            dict(
                name="p",
                type="count_event",
                domain="a",
                event_type="x",
                count=1,
                coord_type="E_trg",
            )
        )
        self.assertFalse(pattern["match"]([dict(domain="b", event_type="x", tick=0)], {}, 0))

    def test_absolute_unix_timestamp_initialization_is_bounded(self):
        now = time.time()
        origin, last = initial_round_position([now, now - 0.5], 1, False)
        self.assertEqual(origin, 0)
        store = MemoryStore()
        close_stable_rounds(store, [], {}, 1, 8, now + 2, last)
        self.assertLess(len(store.streams["clock:tick"]), 5)
        origin, _ = initial_round_position([102.2, 100.2], 1, True)
        self.assertEqual(origin, 100)


class MetricEnvelopeTests(unittest.TestCase):
    def test_missing_source_timestamp_is_a_collection_error_not_type_crash(self):
        source = dict(
            type="command",
            argv=[sys.executable, "-c", "print('{\"cpu\":7}')"],
            event_time_field="ts",
            parser=dict(metrics=[dict(metric="cpu", path="cpu")]),
        )
        with self.assertRaises(ValueError):
            collect(source)

    def test_invalid_metric_envelopes_fail_without_advancing_watermarks(self):
        for data in [
            [],
            dict(
                domain="a",
                metric={"metric": "cpu", "value": 1},
                event_time=float("nan"),
                processing_time=1,
            ),
            dict(
                domain="a",
                metric={"metric": "cpu", "value": 1},
                event_time=1,
                processing_time=1,
                skip_watermark_check="false",
            ),
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_metric_event({"data": json.dumps(data)})


class GridReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = ROOT / "deployment/examples/grid5000/ceph"
        sys.path.insert(0, str(folder))
        try:
            spec = importlib.util.spec_from_file_location(
                "ceph_cluster_for_tests", folder / "cluster.py"
            )
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
        finally:
            sys.path.pop(0)

    def test_old_inventory_is_rejected_even_when_node_names_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes"
            path.write_text("gros-1.nancy.grid5000.fr")
            with patch.dict(os.environ, {"OAR_JOB_ID": "new", "OAR_NODEFILE": str(path)}):
                with self.assertRaises(ValueError):
                    self.module.verify_allocation(
                        dict(allocation_id="old", nodes=["gros-1.nancy.grid5000.fr"])
                    )
                self.module.verify_allocation(
                    dict(allocation_id="new", nodes=["gros-1.nancy.grid5000.fr"])
                )

    def test_source_sync_refuses_a_different_existing_ceph_identity(self):
        inv = dict(nodes=["gros-1.nancy.grid5000.fr"], fsid="new")
        with (
            patch.object(self.module, "ssh", return_value='{"fsid":"old"}'),
            patch.object(self.module, "command") as command,
        ):
            with self.assertRaises(ValueError):
                self.module.sync_sources(Path("/unused"), inv)
            command.assert_not_called()


class StreamProcessingAndDocumentationTests(unittest.TestCase):
    def test_cep_loop_orders_input_and_starts_at_absolute_frontier(self):
        import importlib

        engine = importlib.import_module("runtime.cep.cep")

        class StopLoop(Exception):
            pass

        class InputStore(MemoryStore):
            def __init__(self):
                super().__init__()
                self.read = False

            def pipeline(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def watch(self, *keys):
                pass

            def multi(self):
                pass

            def execute(self):
                pass

            def xread(self, *args, **kwargs):
                if self.read:
                    raise StopLoop()
                self.read = True
                return [
                    (
                        "clock:watermark",
                        [("2-0", {"data": json.dumps(dict(domain="a", watermark=101.5))})],
                    ),
                    (
                        "events:obs",
                        [
                            ("1-0", {"data": "null"}),
                            (
                                "1-1",
                                {
                                    "data": json.dumps(
                                        dict(domain="a", event_type="x", event_time=100.2)
                                    )
                                },
                            ),
                        ],
                    ),
                ]

        store = InputStore()
        pattern = compile_pattern(
            dict(name="p", type="count_event", event_type="x", count=1, coord_type="E_trg")
        )
        config = {
            "cep": {
                "watermark_policy": "participant_min",
                "watermark_domains": ["a"],
                "tick": {"seconds": 1, "retention_ticks": 8},
                "relative_tick_origin": False,
            }
        }
        close = engine.close_stable_rounds

        def guarded_close(*args, **kwargs):
            if args[5] - args[6] > 10:
                raise AssertionError("Unbounded epoch catch-up")
            return close(*args, **kwargs)

        with (
            patch.object(engine, "get_redis_client", return_value=store),
            patch.object(engine, "load_syntony_config", return_value=config),
            patch.object(engine, "get_patterns", return_value=[pattern]),
            patch.object(engine, "close_stable_rounds", side_effect=guarded_close),
            patch.object(engine, "log_event"),
        ):
            with self.assertRaises(StopLoop):
                engine.cep_loop()
        self.assertEqual([e["tick"] for e in store.streams["clock:tick"]], [100])
        self.assertEqual(store.streams["events:coord"][0]["tick"], 100)

    def test_repository_layout_headings_and_links(self):
        spec = importlib.util.spec_from_file_location(
            "repository_check", ROOT / "scripts/check_repository.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.check(ROOT), [])
