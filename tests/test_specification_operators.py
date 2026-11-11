"""Exercise Boolean expressions, bounded obligations and stable-round recovery."""

import copy
import json
import unittest

from test_runtime import MemoryStore

from runtime.cep import OperatorRuntime, close_stable_rounds, compile_expression, compile_pattern
from runtime.configuration import load_syntony_config, validate


def event(name, domain="application_service"):
    return {"op": "event", "domain": domain, "event_type": name}


def observation(name, index, domain="application_service"):
    return dict(domain=domain, event_type=name, tick=index, event_time=index + 0.2)


def pattern(expression=None, **fields):
    spec = dict(name="operator", domain="application_service", coord_type="E_trg", **fields)
    if expression is not None:
        spec.update(type="expression", expression=expression)
    return compile_pattern(spec)


class ExpressionTests(unittest.TestCase):
    def evaluate(self, spec, rows, index, first=0, snapshots=None):
        expr = compile_expression(spec)
        if index - expr.horizon + 1 < first:
            return False
        return expr.test(dict(round_table=rows, snapshots=snapshots or {}), index)

    def test_nested_conjunction_disjunction_and_negation_are_domain_qualified(self):
        condition = {
            "op": "and",
            "args": [
                {"op": "or", "args": [event("failed"), event("degraded")]},
                {"op": "not", "arg": event("maintenance")},
            ],
        }
        rows = {0: [observation("failed", 0, "other")], 1: [observation("degraded", 1)]}
        self.assertFalse(self.evaluate(condition, rows, 0))
        self.assertTrue(self.evaluate(condition, rows, 1))
        rows[1].append(observation("maintenance", 1))
        self.assertFalse(self.evaluate(condition, rows, 1))
        self.assertFalse(self.evaluate(condition, rows, 2))

    def test_always_and_eventually_require_complete_windows_and_include_empty_rounds(self):
        condition = {"op": "always", "rounds": 3, "arg": {"op": "not", "arg": event("error")}}
        self.assertFalse(self.evaluate(condition, {}, 1))
        self.assertTrue(self.evaluate(condition, {}, 2))
        self.assertFalse(self.evaluate(condition, {1: [observation("error", 1)]}, 2))
        condition.update(op="eventually", arg=event("success"))
        self.assertTrue(self.evaluate(condition, {0: [observation("success", 0)]}, 2))
        self.assertFalse(self.evaluate(condition, {0: [observation("success", 0)]}, 3))

    def test_nested_temporal_windows_accumulate_history_requirements(self):
        condition = {
            "op": "always",
            "rounds": 2,
            "arg": {"op": "eventually", "rounds": 3, "arg": event("ok")},
        }
        self.assertEqual(compile_expression(condition).horizon, 4)
        self.assertFalse(self.evaluate(condition, {1: [observation("ok", 1)]}, 2))
        self.assertTrue(self.evaluate(condition, {1: [observation("ok", 1)]}, 3))

    def test_current_and_previous_flags_use_the_snapshot_of_the_evaluated_round(self):
        condition = {
            "op": "and",
            "args": [
                {"op": "state", "domain": "application_service", "flag": "busy", "value": False},
                {"op": "prev", "domain": "application_service", "flag": "busy", "value": True},
            ],
        }
        snapshots = {
            0: {
                "current": {"application_service": {"busy": False}},
                "previous": {"application_service": {"busy": True}},
            }
        }
        self.assertTrue(self.evaluate(condition, {}, 0, snapshots=snapshots))
        snapshots[0]["previous"]["application_service"]["busy"] = False
        self.assertFalse(self.evaluate(condition, {}, 0, snapshots=snapshots))

    def test_invalid_expression_shapes_fail_compilation(self):
        for spec in [
            None,
            {},
            {"op": []},
            {"op": "and", "args": []},
            {"op": "not"},
            {"op": "event", "domain": "a"},
            dict(event("x"), where=[]),
            {"op": "always", "rounds": 1.5, "arg": event("x")},
            {"op": "eventually", "rounds": True, "arg": event("x")},
            {"op": "state", "domain": "a", "flag": "x", "value": 1},
            {"op": "event", "domain": "a", "event_type": "x", "typo": True},
        ]:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                compile_expression(spec)


class OperatorRoundTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.runtime = OperatorRuntime()
        self.last = -1
        self.rows = {}

    def close(self, patterns, watermark, **kwargs):
        self.last = close_stable_rounds(
            self.store,
            patterns,
            self.rows,
            1,
            8,
            watermark,
            self.last,
            operators=self.runtime,
            **kwargs,
        )

    def obligation(self, **fields):
        options = dict(type="obligation", when=event("failed"), ensure=event("ok"), within_rounds=2)
        options.update(fields)
        return pattern(**options)

    def test_direct_callers_must_retain_operator_runtime_between_rounds(self):
        rule = self.obligation()
        with self.assertRaisesRegex(ValueError, "OperatorRuntime"):
            close_stable_rounds(self.store, [rule], {}, 1, 8, 1, -1)
        self.assertEqual(self.store.streams, {})

    def test_absence_emits_from_stable_round_context_without_fabricated_observations(self):
        rule = pattern({"op": "not", "arg": event("error")})
        self.close([rule], 0.99)
        self.assertNotIn("events:coord", self.store.streams)
        self.close([rule], 1)
        result = self.store.streams["events:coord"][0]
        self.assertEqual(result["base_event"], "round_evaluation")
        self.assertEqual(result["evaluation_context"], dict(source="stable_round", round=0))
        self.assertEqual(self.rows, {})
        self.close([rule], 1)
        self.assertEqual(len(self.store.streams["events:coord"]), 1)

    def test_obligation_waits_for_watermark_then_expires_in_empty_round(self):
        rule = self.obligation()
        self.rows[0] = [observation("failed", 0)]
        self.close([rule], 1)
        self.assertEqual(len(self.runtime.pending["operator"]), 1)
        self.close([rule], 2.99)
        self.assertNotIn("events:temporal", self.store.streams)
        self.close([rule], 3)
        audit = self.store.streams["events:temporal"][0]
        self.assertEqual((audit["status"], audit["reason"]), ("violated", "deadline_expired"))
        self.assertEqual((audit["started_round"], audit["resolved_round"]), (0, 2))
        self.assertEqual(len(self.store.streams["events:coord"]), 1)
        self.close([rule], 4)
        self.assertEqual(len(self.store.streams["events:temporal"]), 1)

    def test_consequent_at_deadline_is_satisfied_and_resolved_once(self):
        rule = self.obligation(emit_on="satisfied")
        self.rows.update({0: [observation("failed", 0)], 2: [observation("ok", 2)]})
        self.close([rule], 4)
        result = self.store.streams["events:coord"][0]["operator_result"]
        self.assertEqual((result["status"], result["resolved_round"]), ("satisfied", 2))
        self.assertEqual(self.runtime.pending["operator"], [])
        self.assertEqual(len(self.store.streams["events:temporal"]), 1)

    def test_until_reports_early_maintenance_violation(self):
        rule = self.obligation(maintain={"op": "not", "arg": event("error")})
        self.rows.update({0: [observation("failed", 0)], 1: [observation("error", 1)]})
        self.close([rule], 2)
        result = self.store.streams["events:temporal"][0]
        self.assertEqual((result["status"], result["reason"]), ("violated", "maintenance_failed"))

    def test_until_completion_takes_precedence_over_maintenance_in_same_round(self):
        rule = self.obligation(maintain={"op": "not", "arg": event("error")}, emit_on="satisfied")
        self.rows[0] = [observation("failed", 0), observation("ok", 0), observation("error", 0)]
        self.close([rule], 1)
        self.assertEqual(self.store.streams["events:temporal"][0]["status"], "satisfied")

    def test_overlapping_obligations_keep_distinct_start_rounds(self):
        rule = self.obligation()
        self.rows.update({0: [observation("failed", 0)], 1: [observation("failed", 1)]})
        self.close([rule], 4)
        self.assertEqual(
            [row["started_round"] for row in self.store.streams["events:temporal"]], [0, 1]
        )

    def test_checkpoint_restores_pending_obligations_and_state_history(self):
        snapshot = {
            "current": {"application_service": {"busy": False}},
            "previous": {"application_service": {"busy": True}},
        }
        self.runtime = OperatorRuntime(state_reader=lambda: copy.deepcopy(snapshot))
        rule = self.obligation(
            ensure={"op": "state", "domain": "application_service", "flag": "busy"}
        )
        self.rows[0] = [observation("failed", 0)]
        self.close([rule], 1)
        saved = json.loads(json.dumps(self.runtime.checkpoint()))
        self.runtime = OperatorRuntime(saved, state_reader=lambda: copy.deepcopy(snapshot))
        snapshot["current"]["application_service"]["busy"] = True
        self.close([rule], 2)
        self.assertEqual(self.store.streams["events:temporal"][0]["status"], "satisfied")
        self.assertFalse(self.runtime.snapshots[0]["current"]["application_service"]["busy"])
        self.assertTrue(self.runtime.snapshots[1]["current"]["application_service"]["busy"])

    def test_profile_cutover_cancels_pending_obligations_and_resets_history(self):
        rule = self.obligation()
        self.rows[0] = [observation("failed", 0)]
        self.close([rule], 1, before_round=lambda *_: {"policy_version": 1})
        self.close([rule], 2, before_round=lambda *_: {"policy_version": 2})
        self.assertEqual(self.store.streams["events:temporal"][0]["status"], "cancelled")
        self.assertEqual(self.runtime.pending["operator"], [])
        self.assertEqual(self.runtime.first_round, 1)
        self.assertNotIn("events:coord", self.store.streams)


class OperatorCatalogTests(unittest.TestCase):
    def config_with(self, **fields):
        config = load_syntony_config()
        name = config["cep"]["patterns"][0]["name"]
        config["cep"]["patterns"][0] = dict(
            name=name, domain="application_service", coord_type="E_trg", **fields
        )
        return config

    def test_nested_event_and_flag_references_are_validated(self):
        condition = {
            "op": "and",
            "args": [
                event("application_degraded"),
                {
                    "op": "state",
                    "domain": "application_service",
                    "flag": "service_status_unknown",
                    "value": False,
                },
            ],
        }
        config = self.config_with(type="expression", expression=condition)
        validate(config)
        condition["args"][0]["event_type"] = "missing"
        with self.assertRaisesRegex(ValueError, "Unknown observed event"):
            validate(config)
        condition["args"][0]["event_type"] = "application_degraded"
        condition["args"][1]["flag"] = "missing"
        with self.assertRaisesRegex(ValueError, "undeclared state flag"):
            validate(config)

    def test_operator_retention_and_force_processing_are_checked(self):
        config = self.config_with(
            type="expression",
            expression={"op": "always", "rounds": 9, "arg": event("application_degraded")},
        )
        with self.assertRaisesRegex(ValueError, "retention"):
            validate(config)
        config["cep"]["patterns"][0]["expression"]["rounds"] = 2
        config["cep"]["patterns"][0]["force_process"] = True
        with self.assertRaisesRegex(ValueError, "stable rounds"):
            validate(config)

    def test_obligation_consequents_cannot_be_satisfied_by_pre_trigger_history(self):
        config = self.config_with(
            type="obligation",
            when=event("application_degraded"),
            within_rounds=3,
            ensure={"op": "eventually", "rounds": 2, "arg": event("local_service_available")},
        )
        with self.assertRaisesRegex(ValueError, "current round"):
            validate(config)

    def test_obligation_horizon_includes_its_trigger_round(self):
        config = self.config_with(
            type="obligation",
            when=event("application_degraded"),
            ensure=event("local_service_available"),
            within_rounds=8,
        )
        with self.assertRaisesRegex(ValueError, "retention"):
            validate(config)
        config["cep"]["patterns"][0]["within_rounds"] = 7
        validate(config)
