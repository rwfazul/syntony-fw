"""Test catalog validation, event-time rules, action execution and feedback envelopes."""

import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.cep.cep import (
    close_stable_rounds,
    effective_watermark,
    watermark_to_stable_round,
)
from runtime.cep.patterns import _compile_pattern
from runtime.composer.composer import WatermarkManager, match_rule
from runtime.configuration import assemble, load_syntony_config, validate
from runtime.coordinator.internal_state import (
    check_constraints,
    register_action_block,
    register_dedupe,
)
from runtime.coordinator.policies import map_to_actions
from runtime.executor.executor import apply_command_action, process_executor_command
from runtime.executor.internal_state import get_state, update_state
from runtime.monitor.monitor import collect

ROOT = Path(__file__).resolve().parents[1]


class MemoryStore:
    """Storage test double only; production runtime always uses real Redis."""

    def __init__(self):
        self.values, self.hashes, self.streams, self.lists = {}, {}, {}, {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value

    def hdel(self, name, key):
        self.hashes.get(name, {}).pop(key, None)

    def rpush(self, name, value):
        self.lists.setdefault(name, []).append(value)

    def xadd(self, name, fields):
        self.streams.setdefault(name, []).append(json.loads(fields["data"]))
        return f"{len(self.streams[name])}-0"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.config = load_syntony_config()

    def test_assembled_catalog_has_two_domains_and_policy_mappings(self):
        self.assertEqual(
            set(self.config["domains"]), {"application_service", "external_dependency"}
        )
        for pattern in self.config["cep"]["patterns"]:
            self.assertNotIn("action", pattern)
            self.assertIn(pattern["name"], self.config["coordinator"]["policy_mappings"])

    def test_unsupported_source_rejected(self):
        for source_type in ["queue", "generator", "typo"]:
            config = copy.deepcopy(self.config)
            config["domains"]["application_service"]["monitor"]["sources"][0]["type"] = source_type
            with self.assertRaises(ValueError):
                validate(config)

    def test_unknown_metric_event_action_and_domain_rejected(self):
        for field in ["metric", "event", "action", "domain"]:
            config = copy.deepcopy(self.config)
            if field == "metric":
                config["domains"]["application_service"]["composer"]["rules"][0]["metric"] = (
                    "missing"
                )
            if field == "event":
                config["cep"]["patterns"][0]["participants"][0]["event_type"] = "missing"
            if field == "action":
                config["coordinator"]["policy_mappings"]["enable_fallback_for_dependency"][0][
                    "type"
                ] = "missing"
            if field == "domain":
                config["cep"]["watermark_domains"] = ["missing"]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(config)

    def test_duplicate_pattern_and_overlong_window_rejected(self):
        config = copy.deepcopy(self.config)
        config["cep"]["patterns"] *= 2
        with self.assertRaises(ValueError):
            validate(config)
        config = copy.deepcopy(self.config)
        config["cep"]["patterns"][0]["within_ticks"] = 100
        with self.assertRaises(ValueError):
            validate(config)

    def test_same_round_pattern_validation_and_alias(self):
        for pattern_type in ("same_round_all", "same_tick_all"):
            with self.subTest(pattern_type=pattern_type):
                config = copy.deepcopy(self.config)
                pattern = config["cep"]["patterns"][0]
                pattern["type"] = pattern_type
                pattern["event_types"] = [pattern["participants"][0]["event_type"]]
                validate(config)
                pattern["event_types"] = []
                with self.assertRaisesRegex(ValueError, "requires event types"):
                    validate(config)

    def test_round_configuration_aliases_preserve_persisted_identity(self):
        config = copy.deepcopy(self.config)
        cep = config["cep"]
        cep["round"] = cep.pop("tick")
        cep["round"]["retention_rounds"] = cep["round"].pop("retention_ticks")
        cep["relative_round_origin"] = cep.pop("relative_tick_origin")
        for pattern in cep["patterns"]:
            if "within_ticks" in pattern:
                pattern["within_rounds"] = pattern.pop("within_ticks")
        self.assertEqual(validate(config), self.config)
        self.assertIn("round", config["cep"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(config))
            self.assertEqual(load_syntony_config(path), self.config)

    def test_round_alias_conflicts_and_invalid_types_rejected(self):
        for field in ("round", "retention", "window", "current"):
            config = copy.deepcopy(self.config)
            cep = config["cep"]
            pattern = cep["patterns"][0]
            if field == "round":
                cep["round"] = dict(seconds=999, retention_rounds=8)
            elif field == "retention":
                cep["tick"]["retention_rounds"] = 999
            elif field == "window":
                pattern["within_rounds"] = 999
            else:
                pattern.update(require_current_round=True, require_current_tick=False)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Conflicting"):
                validate(config)
        for field, value in (("within_rounds", 1.5), ("require_current_round", 1)):
            config = copy.deepcopy(self.config)
            pattern = config["cep"]["patterns"][0]
            pattern.pop("within_ticks", None)
            pattern[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(config)

    def test_direct_pattern_compilation_accepts_round_window(self):
        spec = dict(
            name="history",
            type="participants_all",
            participants=[dict(domain="a", event_type="x")],
            within_rounds=2,
            require_current_round=False,
            coord_type="E_trg",
        )
        pattern = _compile_pattern(spec)
        rows = [dict(domain="a", event_type="x", tick=2)]
        self.assertTrue(pattern["match"](rows, {2: rows}, tick=3))
        self.assertFalse(pattern["match"](rows, {2: rows}, tick=4))

    def test_simulation_handler_rejected(self):
        config = copy.deepcopy(self.config)
        config["executor"]["actions"]["ENABLE_FALLBACK"]["mode"] = "simulate"
        with self.assertRaises(ValueError):
            validate(config)

    def test_missing_configuration_and_include_cycles_fail(self):
        with self.assertRaises(FileNotFoundError):
            load_syntony_config("/no/config.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cycle.json"
            path.write_text('{"includes":["cycle.json"]}')
            with self.assertRaises(ValueError):
                assemble(path)

    def test_parameter_binding_and_policy_mapping(self):
        with patch.dict(os.environ, {"GATEWAY_URL": "http://example.invalid:8080"}):
            config = load_syntony_config()
        self.assertIn(
            "example.invalid",
            config["domains"]["application_service"]["monitor"]["sources"][0]["url"],
        )
        actions = map_to_actions(
            dict(policy="restart_confirmed_local_failure", domain="external_dependency", tick=3)
        )
        self.assertEqual(actions[0]["domain"], "application_service")
        self.assertEqual(actions[0]["type"], "RESTART_LOCAL_SERVICE")
        with self.assertRaises(ValueError):
            map_to_actions(dict(policy="missing"))


class TemporalTests(unittest.TestCase):
    def test_watermark_before_tolerance_monotonicity_and_bypass(self):
        wm = WatermarkManager(2, 1, 5, 30)
        self.assertTrue(wm.observe(10, 10)["accepted"])
        self.assertTrue(wm.observe(7, 11)["accepted"])
        self.assertFalse(wm.observe(6.99, 12)["accepted"])
        self.assertTrue(wm.observe(1, 13, True)["accepted"])
        self.assertEqual(wm.watermark, 8)

    def test_wait_for_all_required_domains(self):
        self.assertEqual(
            effective_watermark({"a": 10}, "participant_min", ["a", "b"]), float("-inf")
        )
        self.assertEqual(effective_watermark({"a": 10, "b": 3}, "participant_min", ["a", "b"]), 3)

    def test_round_boundary_no_repeat_and_future_event_exclusion(self):
        store = MemoryStore()
        pattern = _compile_pattern(
            dict(
                name="p",
                type="count_event",
                event_type="x",
                count=1,
                within_ticks=1,
                coord_type="E_trg",
            )
        )
        future = dict(domain="a", event_type="x", event_time=2.2, tick=2)
        last = close_stable_rounds(store, [pattern], {2: [future]}, 1, 8, 2, -1)
        self.assertEqual(last, 1)
        self.assertEqual(len(store.streams["clock:tick"]), 2)
        self.assertFalse(store.streams.get("events:coord"))
        close_stable_rounds(store, [pattern], {2: [future]}, 1, 8, 2, last)
        self.assertEqual(len(store.streams["clock:tick"]), 2)
        self.assertEqual(watermark_to_stable_round(102, 1, 100), 1)

    def test_same_round_alignment_and_legacy_alias(self):
        rows = [
            dict(domain="a", event_type="x", tick=2),
            dict(domain="a", event_type="y", tick=3),
        ]
        for pattern_type in ("same_round_all", "same_tick_all"):
            with self.subTest(pattern_type=pattern_type):
                spec = dict(
                    name="aligned",
                    type=pattern_type,
                    domain="a",
                    event_types=["x", "y"],
                    coord_type="E_sync",
                )
                pattern = _compile_pattern(spec)
                self.assertFalse(pattern["match"](rows, {2: [rows[0]], 3: [rows[1]]}, 3))
                aligned = [dict(row, tick=3) for row in rows]
                self.assertTrue(pattern["match"](aligned, {3: aligned}, 3))
                wrong_domain = [aligned[0], dict(aligned[1], domain="b")]
                self.assertFalse(pattern["match"](wrong_domain, {3: wrong_domain}, 3))

    def test_participants_and_negative_evidence(self):
        spec = dict(
            name="p",
            type="participants_all",
            participants=[dict(domain="a", event_type="x"), dict(domain="b", event_type="y")],
            forbidden_participants=[dict(domain="b", event_type="stop")],
            within_ticks=2,
            coord_type="E_sync",
        )
        pattern = _compile_pattern(spec)
        rows = [dict(domain="a", event_type="x", tick=2), dict(domain="b", event_type="y", tick=3)]
        self.assertTrue(pattern["match"](rows, {3: [rows[1]]}, 3))
        self.assertFalse(
            pattern["match"](
                rows + [dict(domain="b", event_type="stop", tick=3)], {3: [rows[1]]}, 3
            )
        )
        rows[1]["domain"] = "c"
        self.assertFalse(pattern["match"](rows, {3: [rows[1]]}, 3))

    def test_local_window_excludes_future_and_sequence_uses_event_time(self):
        event = {"event_time": 5}
        rule = dict(
            type="metric_window",
            metric="cpu",
            operator="gt",
            value=5,
            within_seconds=3,
            min_matches=1,
        )
        self.assertFalse(match_rule(rule, event, {}, {"cpu": [dict(value=9, event_time=6)]}, []))
        rule = dict(type="event_sequence", event_types=["a", "b"], within_seconds=5)
        history = [dict(event_type="b", event_time=4), dict(event_type="a", event_time=3)]
        self.assertTrue(match_rule(rule, event, {}, {}, history))


class ExecutionTests(unittest.TestCase):
    def test_real_success_failure_timeout_and_unsupported_feedback(self):
        handlers = {
            "ok": dict(mode="command", argv=[sys.executable, "-c", "print(42)"]),
            "fail": dict(mode="command", argv=[sys.executable, "-c", "raise SystemExit(3)"]),
            "timeout": dict(
                mode="command",
                argv=[sys.executable, "-c", "import time;time.sleep(5)"],
                timeout_seconds=0.05,
            ),
        }
        store = MemoryStore()
        for name, expected in [
            ("ok", "action_done"),
            ("fail", "action_fail"),
            ("timeout", "action_timeout"),
            ("missing", "action_fail"),
        ]:
            with self.subTest(name=name):
                result = process_executor_command(
                    store, dict(type=name, domain="a", action_id=name), handlers
                )
                self.assertEqual(result["feedback_type"], expected)
        self.assertEqual(len(store.streams["events:obs"]), 4)

    def test_redelivery_does_not_repeat_external_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "count"
            handler = dict(
                mode="command",
                argv=[
                    sys.executable,
                    "-c",
                    f'from pathlib import Path;p=Path({str(path)!r});p.write_text(p.read_text()+"x" if p.exists() else "x")',
                ],
            )
            store = MemoryStore()
            action = dict(type="write", domain="a", action_id="unique")
            process_executor_command(store, action, {"write": handler})
            process_executor_command(store, action, {"write": handler})
            self.assertEqual(path.read_text(), "x")

    def test_uncertain_action_not_reexecuted(self):
        store = MemoryStore()
        store.set("executor:result:id", '{"state":"running"}')
        result = process_executor_command(store, dict(type="x", domain="a", action_id="id"), {})
        self.assertEqual(result["result"]["status"], "execution_uncertain")
        self.assertEqual(result["feedback_type"], "action_fail")

    def test_command_parameters_are_arguments_not_shell(self):
        action = dict(type="x", domain="a", params={"value": "hello; $(touch /tmp/should-not-run)"})
        result = apply_command_action(
            action,
            dict(argv=[sys.executable, "-c", "import sys;print(sys.argv[1])", "{{params.value}}"]),
        )
        self.assertEqual(result["stdout"], action["params"]["value"])

    def test_state_is_domain_local(self):
        store = MemoryStore()
        update_state(store, "a", "x", "ok")
        update_state(store, "b", "y", "error")
        update_state(store, "a", "z", "ok")
        self.assertEqual(
            get_state(store),
            {"a": {"flags": {"x": "ok", "z": "ok"}}, "b": {"flags": {"y": "error"}}},
        )

    def test_feedback_uses_completion_time_not_old_trigger_round(self):
        store = MemoryStore()
        result = process_executor_command(
            store, dict(type="missing", domain="a", action_id="late-feedback", tick_end=1), {}
        )
        self.assertGreater(result["feedback_payload"]["event_time"], time.time() - 2)
        self.assertEqual(result["feedback_payload"]["trigger_tick_end"], 1)

    def test_block_scope_deadline_and_dedupe(self):
        store = MemoryStore()
        event = dict(
            domain="a",
            policy="restart_confirmed_local_failure",
            coord_type="E_sync",
            timestamp=time.time(),
        )
        register_action_block(
            {
                **event,
                "blocked_action_types": ["restart"],
                "block_scope": "domain",
                "block_ttl_seconds": 5,
            },
            store,
        )
        self.assertFalse(check_constraints(event, store, "restart"))
        self.assertTrue(check_constraints({**event, "domain": "b"}, store, "restart"))
        self.assertFalse(check_constraints({**event, "deadline": time.time() - 1}, store))
        self.assertFalse(
            check_constraints({**event, "domain": "b"}, store, "restart", action_domain="a")
        )
        register_dedupe(event, store)
        self.assertFalse(check_constraints(event, store))


class RealSourceTests(unittest.TestCase):
    def test_source_timestamp_and_numeric_command_output(self):
        source = dict(
            type="command",
            argv=[sys.executable, "-c", 'print(\'{"ts":12.5,"cpu":7}\')'],
            event_time_field="ts",
            parser=dict(metrics=[dict(metric="cpu", path="cpu")]),
        )
        self.assertEqual(collect(source), (12.5, [("cpu", 7)]))
        source["argv"] = [sys.executable, "-c", 'print("{}")']
        with self.assertRaises((TypeError, ValueError)):
            collect(source)

    def test_real_system_load(self):
        timestamp, values = collect(
            dict(type="system", parser=dict(metrics=[dict(metric="load", path="load_avg_1m")]))
        )
        self.assertGreater(timestamp, 0)
        self.assertGreaterEqual(values[0][1], 0)


if __name__ == "__main__":
    unittest.main()
