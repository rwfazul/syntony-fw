"""Evaluate Boolean conditions and bounded temporal obligations in stable rounds."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Expression:
    test: Callable
    horizon: int
    participants: tuple = ()
    flags: tuple = ()


def compile_expression(spec):
    """Validate a condition and collect its history and reference requirements."""
    if not isinstance(spec, dict):
        raise ValueError("An expression must be an object")
    op = spec.get("op")
    fields = {
        "event": {"op", "domain", "event_type", "where"},
        "state": {"op", "domain", "flag", "value"},
        "prev": {"op", "domain", "flag", "value"},
        "and": {"op", "args"},
        "or": {"op", "args"},
        "not": {"op", "arg"},
        "always": {"op", "arg", "rounds"},
        "eventually": {"op", "arg", "rounds"},
    }
    if not isinstance(op, str) or op not in fields or set(spec) - fields[op]:
        raise ValueError(f"Unknown expression operator or fields: {op}")
    if op in {"event", "state", "prev"}:
        name = "event_type" if op == "event" else "flag"
        if any(not isinstance(spec.get(key), str) or not spec[key] for key in ("domain", name)):
            raise ValueError(f"{op} requires a domain and {name}")
        domain = spec["domain"]
        if op == "event":
            where = spec.get("where", {})
            if not isinstance(where, dict):
                raise ValueError("Event where must be an object")

            def test(context, index):
                return any(
                    event.get("domain") == domain
                    and event.get("event_type") == spec["event_type"]
                    and all(event.get(key) == value for key, value in where.items())
                    for event in context["round_table"].get(index, [])
                )

            return Expression(test, 1, (dict(spec, op="event"),))
        expected = spec.get("value", True)
        if type(expected) is not bool:
            raise ValueError("State expressions require a boolean value")
        view = "current" if op == "state" else "previous"

        def test(context, index):
            return context["snapshots"][index][view][domain][spec["flag"]] == expected

        return Expression(test, 1, flags=((domain, spec["flag"]),))
    if op in {"and", "or"}:
        args = spec.get("args")
        if not isinstance(args, list) or not args:
            raise ValueError(f"{op} requires nonempty args")
        children = [compile_expression(arg) for arg in args]
        combine = all if op == "and" else any

        def test(context, index):
            return combine(child.test(context, index) for child in children)

        return Expression(
            test,
            max(child.horizon for child in children),
            tuple(participant for child in children for participant in child.participants),
            tuple(flag for child in children for flag in child.flags),
        )
    child = compile_expression(spec.get("arg"))
    if op == "not":
        return Expression(
            lambda context, index: not child.test(context, index),
            child.horizon,
            child.participants,
            child.flags,
        )
    rounds = spec.get("rounds")
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("Temporal expressions require positive integer rounds")
    combine = all if op == "always" else any

    def test(context, index):
        return combine(child.test(context, row) for row in range(index - rounds + 1, index + 1))

    return Expression(test, rounds + child.horizon - 1, child.participants, child.flags)


def compile_operator(pattern):
    if not isinstance(pattern.get("domain"), str) or not pattern["domain"]:
        raise ValueError("Operator patterns require a decision domain")
    if pattern.get("force_process"):
        raise ValueError("Operator patterns require stable rounds")
    if pattern["type"] == "expression":
        if any(key in pattern for key in ("when", "ensure", "maintain", "emit_on", "within_ticks")):
            raise ValueError("Expression patterns declare windows inside their expression")
        condition = compile_expression(pattern.get("expression"))
        return {"condition": condition, "expressions": [condition], "horizon": condition.horizon}
    if "expression" in pattern:
        raise ValueError("Obligations declare when, ensure and optional maintain")
    when = compile_expression(pattern.get("when"))
    ensure = compile_expression(pattern.get("ensure"))
    expressions = [when, ensure]
    maintain = None
    if "maintain" in pattern:
        maintain = compile_expression(pattern["maintain"])
        expressions.append(maintain)
    rounds = pattern.get("within_ticks")
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("An obligation requires positive integer within_rounds")
    if any(expression.horizon != 1 for expression in expressions[1:]):
        raise ValueError("Obligation ensure and maintain must describe the current round")
    emit_on = pattern.get("emit_on", "violated")
    if not isinstance(emit_on, str) or emit_on not in {"satisfied", "violated"}:
        raise ValueError("Obligation emit_on must be satisfied or violated")
    return {
        "when": when,
        "ensure": ensure,
        "maintain": maintain,
        "expressions": expressions,
        "rounds": rounds,
        "emit_on": emit_on,
        "horizon": max(rounds + 1, *(expression.horizon for expression in expressions)),
    }


class OperatorRuntime:
    """Checkpoint condition snapshots and obligations alongside the Round Table."""

    def __init__(self, saved=None, state_reader=None):
        saved = saved or {}
        self.first_round = saved.get("first_round")
        self.profile_version = saved.get("profile_version")
        self.snapshots = {int(index): value for index, value in saved.get("snapshots", {}).items()}
        self.pending = saved.get("pending", {})
        self.state_reader = state_reader

    def checkpoint(self):
        return {
            "first_round": self.first_round,
            "profile_version": self.profile_version,
            "snapshots": self.snapshots,
            "pending": self.pending,
        }

    def begin_round(self, client, patterns, index, retention, profile):
        version = profile.get("policy_version")
        if self.profile_version != version:
            for name, pending in self.pending.items():
                for obligation in pending:
                    self._audit(client, name, obligation, index, "cancelled", "profile_changed")
            self.pending = {}
            self.snapshots = {}
            self.first_round = index
            self.profile_version = version
        if self.first_round is None:
            self.first_round = index
        if any(expr.flags for pattern in patterns for expr in pattern["operator"]["expressions"]):
            if self.state_reader is None:
                raise ValueError("State expressions require a state reader")
            self.snapshots[index] = self.state_reader()
        self.snapshots = {
            row: snapshot for row, snapshot in self.snapshots.items() if row > index - retention
        }

    def _audit(self, client, name, obligation, index, status, reason):
        import json

        result = {
            "policy": name,
            "started_round": obligation["started_round"],
            "deadline_round": obligation["deadline_round"],
            "resolved_round": index,
            "status": status,
            "reason": reason,
            "trigger": obligation["anchor"],
        }
        client.xadd("events:temporal", {"data": json.dumps(result)})
        return result

    def evaluate(self, client, pattern, round_table, index, round_end):
        operator = pattern["operator"]
        context = {
            "round_table": round_table,
            "snapshots": self.snapshots,
        }

        def matches(expression):
            return index - expression.horizon + 1 >= self.first_round and expression.test(
                context, index
            )

        # A stable-round decision can be based on absence or state without an observation.
        anchor = {
            "domain": pattern["domain"],
            "event_type": "round_evaluation",
            "event_time": round_end,
            "tick": index,
        }
        if "condition" in operator:
            return [{"anchor": anchor}] if matches(operator["condition"]) else []
        pending = self.pending.setdefault(pattern["name"], [])
        ready = (
            index - max(expr.horizon for expr in operator["expressions"]) + 1 >= self.first_round
        )
        if ready and matches(operator["when"]):
            pending.append(
                {
                    "started_round": index,
                    "deadline_round": index + operator["rounds"],
                    "anchor": anchor,
                }
            )
        waiting = []
        emissions = []
        for obligation in pending:
            if matches(operator["ensure"]):
                status, reason = "satisfied", "consequent_matched"
            elif operator["maintain"] is not None and not matches(operator["maintain"]):
                status, reason = "violated", "maintenance_failed"
            elif index >= obligation["deadline_round"]:
                status, reason = "violated", "deadline_expired"
            else:
                waiting.append(obligation)
                continue
            result = self._audit(client, pattern["name"], obligation, index, status, reason)
            if status == operator["emit_on"]:
                emissions.append({"anchor": obligation["anchor"], "operator_result": result})
        self.pending[pattern["name"]] = waiting
        return emissions
