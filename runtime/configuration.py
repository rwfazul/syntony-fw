"""Local specification catalog assembly and startup validation. No hot reload."""

import copy
import json
import math
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merge(left, right):
    result = copy.deepcopy(left)
    for key, value in right.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] += copy.deepcopy(value)
        elif key in result and result[key] != value:
            raise ValueError(f"Conflicting specification field: {key}")
        else:
            result[key] = copy.deepcopy(value)
    return result


def assemble(path, ancestors=()):
    path = Path(path).resolve()
    if path in ancestors:
        raise ValueError(f"Circular specification include: {path}")
    data = json.loads(path.read_text())
    result = {}
    for relative in data.pop("includes", []):
        result = merge(result, assemble(path.parent / relative, (*ancestors, path)))
    return merge(result, data)


def positive(value, name, allow_zero=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value < 0 if allow_zero else value <= 0)
    ):
        raise ValueError(
            f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number"
        )


def positive_integer(value, name):
    positive(value, name)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")


def argument_array(value, name):
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(arg, str) for arg in value)
        or not value[0]
    ):
        raise ValueError(f"{name} must be a nonempty string argv array")


def boolean_flags(value, fields):
    for field in fields:
        if field in value and type(value[field]) is not bool:
            raise ValueError(f"{field} must be a JSON boolean")


def _legacy_round_options(options, aliases):
    """Resolve round names to the persisted configuration representation."""
    result = dict(options)
    for current, legacy in aliases:
        if current in result:
            value = result.pop(current)
            if legacy in result and (
                type(result[legacy]) is not type(value) or result[legacy] != value
            ):
                raise ValueError(f"Conflicting configuration aliases: {current} and {legacy}")
            result[legacy] = value
    return result


def normalize_pattern_rounds(pattern):
    return _legacy_round_options(
        pattern,
        (("within_rounds", "within_ticks"), ("require_current_round", "require_current_tick")),
    )


def _normalize_cep_rounds(config):
    cep = dict(config.get("cep", {}))
    for field in ("round", "tick"):
        if field in cep:
            cep[field] = _legacy_round_options(
                cep[field], (("retention_rounds", "retention_ticks"),)
            )
    cep = _legacy_round_options(
        cep, (("round", "tick"), ("relative_round_origin", "relative_tick_origin"))
    )
    if "patterns" in cep:
        cep["patterns"] = [normalize_pattern_rounds(pattern) for pattern in cep["patterns"]]
    return {**config, "cep": cep}


def validate(config):
    from .cep.patterns import _compile_pattern
    from .composer.rules import _compile_rule

    config = _normalize_cep_rounds(config)
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported configuration schema_version; expected 1")
    domains = config.get("domains", {})
    if not domains:
        raise ValueError("At least one domain is required")
    observed = {("executor", "E_fed")}
    for domain, package in domains.items():
        sources = package.get("monitor", {}).get("sources", [])
        if not any(s.get("enabled", True) for s in sources):
            raise ValueError(f"No enabled real metric sources for {domain}")
        metrics = set()
        source_names = set()
        for source in sources:
            boolean_flags(source, ("enabled",))
            if not source.get("enabled", True):
                continue
            if not source.get("name") or source["name"] in source_names:
                raise ValueError(f"Missing or duplicate source name in {domain}")
            source_names.add(source["name"])
            if source.get("type") not in {"command", "http_json", "system"}:
                raise ValueError(f"Unsupported real monitor source: {source.get('type')}")
            positive(source.get("timeout_seconds", 5), "source timeout")
            if source["type"] == "command":
                argument_array(source.get("argv"), "Command source")
            if source["type"] == "http_json" and not str(source.get("url", "")).startswith(
                ("http://", "https://")
            ):
                raise ValueError("HTTP sources require an http(s) URL")
            parser = source.get("parser", {})
            if parser.get("type", "json") != "json" or not parser.get("metrics"):
                raise ValueError("Sources require explicit JSON metric mappings")
            for mapping in parser["metrics"]:
                if (
                    not isinstance(mapping.get("metric"), str)
                    or not mapping["metric"]
                    or not isinstance(mapping.get("path"), str)
                    or not mapping["path"]
                ):
                    raise ValueError("Metric mappings require nonempty metric and path strings")
                metrics.add(mapping["metric"])
        names = set()
        for history_key in ("metric_history_ttl_seconds", "event_history_ttl_seconds"):
            positive(package.get("composer", {}).get(history_key, 30), history_key)
        rules = package.get("composer", {}).get("rules", [])
        all_names = {r["name"] for r in rules}
        for rule in rules:
            compiled = _compile_rule(rule)
            rule_type = compiled["type"]
            boolean_flags(rule, ("force_process", "skip_watermark_check"))
            conditions = rule.get("conditions", []) + ([rule] if "operator" in rule else [])
            if any(
                c.get("operator") not in {"gt", "gte", "lt", "lte", "eq", "neq"} for c in conditions
            ):
                raise ValueError("Unknown comparison operator")
            if any(
                not isinstance(c.get("value"), (int, float)) or not math.isfinite(c["value"])
                for c in conditions
            ):
                raise ValueError("Metric comparisons require finite numeric values")
            if type(rule.get("priority", 5)) is not int:
                raise ValueError("Rule priority must be an integer")
            if rule_type in {"metric_window", "event_combo_all", "event_sequence"}:
                positive(rule.get("within_seconds", 0), "local event-time window")
                history_key = (
                    "metric_history_ttl_seconds"
                    if rule_type == "metric_window"
                    else "event_history_ttl_seconds"
                )
                if rule["within_seconds"] > package.get("composer", {}).get(history_key, 30):
                    raise ValueError("Local rule window exceeds its history retention")
            if rule_type == "metric_window":
                positive_integer(rule.get("min_matches", 1), "minimum match count")
            if rule_type in {"event_combo_all", "event_sequence"} and not rule.get("event_types"):
                raise ValueError("Local event composition requires event_types")
            if rule["name"] in names:
                raise ValueError(f"Duplicate observed event in {domain}: {rule['name']}")
            names.add(rule["name"])
            observed.add((domain, rule["name"]))
            used = (
                {rule["metric"]}
                if "metric" in rule
                else {c["metric"] for c in rule.get("conditions", [])}
            )
            if not used.issubset(metrics):
                raise ValueError(f"Rule {rule['name']} references unknown metrics")
            if not set(rule.get("event_types", [])).issubset(all_names):
                raise ValueError(f"Rule {rule['name']} references unknown local events")
            if rule_type in {"all_metrics", "any_metrics"} and not rule.get("conditions"):
                raise ValueError("Metric conjunctions require conditions")
            positive(rule.get("cooldown_seconds", 0), "cooldown", True)
    cep = config.get("cep", {})
    if cep.get("watermark_policy") != "participant_min":
        raise ValueError("This distribution requires participant_min watermark policy")
    required = cep.get("watermark_domains", [])
    if not required or len(required) != len(set(required)) or not set(required).issubset(domains):
        raise ValueError("watermark_domains must name configured required domains")
    tick = cep.get("tick", {})
    positive(tick.get("seconds", 1), "control-round seconds")
    positive_integer(tick.get("retention_ticks", 8), "retention rounds")
    boolean_flags(cep, ("relative_tick_origin",))
    timing = config.get("timing", {})
    positive(timing.get("sample_interval_seconds", 0.5), "sample interval")
    for key in ("watermark_delay_seconds", "allowed_lateness_seconds"):
        positive(timing.get(key, 0.25), key, True)
    actions = config.get("executor", {}).get("actions", {})
    for name, handler in actions.items():
        mode = handler.get("mode")
        if mode == "command":
            argument_array(handler.get("argv"), f"Action {name}")
        elif mode != "activate_profile":
            raise ValueError(f"Unknown execution mode for action {name}")
        positive(handler.get("timeout_seconds", 10), "execution timeout")
    flags = config.get("state", {}).get("flags", {})
    if not isinstance(flags, dict):
        raise ValueError("state.flags must be a domain-to-flags object")
    for domain, declared in flags.items():
        if domain not in domains or not isinstance(declared, dict):
            raise ValueError("State flags require a configured domain and an object of booleans")
        if any(
            not isinstance(name, str) or not name or type(value) is not bool
            for name, value in declared.items()
        ):
            raise ValueError("State flags require nonempty names and boolean defaults")

    def check_flags(values, clear=False):
        if not isinstance(values, dict):
            raise ValueError("Flag effects and guards must be domain-qualified objects")
        for domain, entries in values.items():
            expected_type = list if clear else dict
            if not isinstance(entries, expected_type):
                raise ValueError("clear_flags requires lists; other flag fields require objects")
            if domain not in domains or any(name not in flags.get(domain, {}) for name in entries):
                raise ValueError("Reference to an undeclared state flag")
            if not clear and any(type(value) is not bool for value in entries.values()):
                raise ValueError("Flag effects and guards require boolean values")

    constraints = config.get("coordinator", {}).get("policy_constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("policy_constraints must be an object")
    classes = set()
    for policy, constraint in constraints.items():
        if not isinstance(constraint, dict) or set(constraint) - {
            "required_flags",
            "set_flags_on_dispatch",
            "exclusivity_class",
            "deadline_seconds",
        }:
            raise ValueError(f"Unknown policy constraint for {policy}")
        for field in ("required_flags", "set_flags_on_dispatch"):
            check_flags(constraint.get(field, {}))
        if "exclusivity_class" in constraint:
            name = constraint["exclusivity_class"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("exclusivity_class must be a nonempty string")
            classes.add(name)
        if "deadline_seconds" in constraint:
            positive(constraint["deadline_seconds"], "policy deadline")
    for handler in actions.values():
        for transition in ("on_success", "on_failure", "on_timeout", "on_uncertain"):
            effects = handler.get(transition, {})
            if not isinstance(effects, dict) or set(effects) - {
                "set_flags",
                "clear_flags",
                "release_exclusivity",
            }:
                raise ValueError(f"Unknown outcome effect: {transition}")
            check_flags(effects.get("set_flags", {}))
            check_flags(effects.get("clear_flags", {}), clear=True)
            for domain, names in effects.get("clear_flags", {}).items():
                if set(names) & effects.get("set_flags", {}).get(domain, {}).keys():
                    raise ValueError("An outcome cannot both set and clear the same flag")
            releases = effects.get("release_exclusivity", [])
            if not isinstance(releases, list) or any(name not in classes for name in releases):
                raise ValueError("Recovery must reference declared exclusivity classes")
            if releases and transition != "on_success":
                raise ValueError("Only a successful verified recovery may release exclusivity")
    mappings = config.get("coordinator", {}).get("policy_mappings", {})
    policies = set()

    def check_event(domain, event):
        if event == "E_fed" and domain in (None, *domains):
            return
        if not any(e == event and domain in (None, d) for d, e in observed):
            raise ValueError(f"Unknown observed event: {domain}/{event}")

    for pattern in cep.get("patterns", []):
        _compile_pattern(pattern)
        if pattern.get("type") == "count_event":
            positive_integer(pattern.get("count", 0), "event count")
        boolean_flags(pattern, ("force_process", "require_current_tick"))
        if type(pattern.get("priority", 5)) is not int:
            raise ValueError("Pattern priority must be an integer")
        if pattern.get("type") in ("same_round_all", "same_tick_all") and not pattern.get(
            "event_types"
        ):
            raise ValueError("same_round_all requires event types")
        if pattern["name"] in policies:
            raise ValueError(f"Duplicate CEP pattern: {pattern['name']}")
        policies.add(pattern["name"])
        if pattern.get("coord_type") not in {"E_trg", "E_block", "E_unblock", "E_sync"}:
            raise ValueError("Unknown coordination event type")
        if "deadline_seconds" in pattern:
            positive(pattern["deadline_seconds"], "pattern deadline")
        window = pattern.get("within_ticks", 1)
        positive_integer(window, "pattern window")
        if window > tick.get("retention_ticks", 8):
            raise ValueError("Pattern window exceeds retention horizon")
        for participant in pattern.get("participants", []) + pattern.get(
            "forbidden_participants", []
        ):
            check_event(participant.get("domain"), participant["event_type"])
            if participant.get("domain") and participant["domain"] not in required:
                raise ValueError("Every pattern participant domain must be in watermark_domains")
        if pattern.get("type") == "participants_all" and not pattern.get("participants"):
            raise ValueError("participants_all requires nonempty participants")
        for event in pattern.get("event_types", []) + pattern.get("requires_event_types", []):
            check_event(pattern.get("domain"), event)
        if pattern.get("event_type"):
            check_event(pattern.get("domain"), pattern["event_type"])
        if pattern.get("domain") and pattern["domain"] not in required:
            raise ValueError("Pattern domain must be a required watermark domain")
        if pattern.get("action"):
            raise ValueError("Declare actions in coordinator.policy_mappings, not CEP patterns")
        if pattern["coord_type"] in {"E_block", "E_unblock"}:
            guard_domain = pattern.get("block_domain", pattern.get("domain"))
            if guard_domain not in domains:
                raise ValueError("Guard events require a configured block_domain or pattern domain")
            block_id = pattern.get(
                "block_id", pattern["name"] if pattern["coord_type"] == "E_block" else None
            )
            if not isinstance(block_id, str) or not block_id.strip():
                raise ValueError("Guard events require a nonempty block_id")
            if pattern["name"] in mappings:
                raise ValueError("Guard events manage leases and cannot map to executor commands")
        if pattern["coord_type"] == "E_unblock":
            if pattern.get("blocked_action_types") or pattern.get("block_ttl_seconds") is not None:
                raise ValueError(
                    "Release events identify a guard without defining a blocking lease"
                )
        elif pattern.get("blocked_action_types"):
            if pattern["coord_type"] != "E_block":
                raise ValueError("Action-class blocking requires E_block")
            positive(pattern.get("block_ttl_seconds", 0), "block lease duration")
            if pattern.get("block_scope", "global") not in {"global", "domain"}:
                raise ValueError("block_scope must be global or domain")
            if not set(pattern["blocked_action_types"]).issubset(actions):
                raise ValueError("Unknown blocked action")
        elif pattern["coord_type"] == "E_block":
            raise ValueError("E_block requires blocked_action_types")
        elif pattern["name"] not in mappings:
            raise ValueError(f"Missing policy mapping: {pattern['name']}")
    guards = {
        (p.get("block_id", p["name"]), p.get("block_domain", p.get("domain")))
        for p in cep.get("patterns", [])
        if p["coord_type"] == "E_block"
    }
    for pattern in cep.get("patterns", []):
        if (
            pattern["coord_type"] == "E_unblock"
            and (pattern["block_id"], pattern.get("block_domain", pattern.get("domain")))
            not in guards
        ):
            raise ValueError("Release event references an unknown blocking guard")
    if any(
        policy not in mappings
        and (constraint.get("set_flags_on_dispatch") or constraint.get("exclusivity_class"))
        for policy, constraint in constraints.items()
    ):
        raise ValueError("Dispatch effects and exclusivity require an executable action mapping")
    if not set(constraints).issubset(policies):
        raise ValueError("Constraints reference unknown policy")
    if not set(mappings).issubset(policies):
        raise ValueError("Mapping references unknown policy")
    for policy, commands in mappings.items():
        if not commands:
            raise ValueError(f"Empty action mapping for {policy}")
        for command in commands:
            if command.get("type") not in actions or command.get("domain") not in domains:
                raise ValueError(f"Unresolved action or domain in policy {policy}")
    profiles = cep.get("policy_profiles", {})
    if "policy_profiles" in cep or "initial_policy_profile" in cep:
        if (
            not isinstance(profiles, dict)
            or not profiles
            or not isinstance(cep.get("initial_policy_profile"), str)
            or cep["initial_policy_profile"] not in profiles
        ):
            raise ValueError("CEP policy profiles require a declared initial profile")
        for name, selected in profiles.items():
            if (
                not name
                or not isinstance(selected, list)
                or any(
                    not isinstance(pattern, str) or pattern not in policies for pattern in selected
                )
            ):
                raise ValueError(f"Unknown pattern in policy profile {name}")
            if len(set(selected)) != len(selected):
                raise ValueError(f"Duplicate patterns in policy profile {name}")
    for policy, commands in mappings.items():
        for command in commands:
            if actions[command["type"]]["mode"] == "activate_profile" and (
                len(commands) != 1
                or not isinstance(command.get("params", {}).get("target_profile"), str)
                or command["params"]["target_profile"] not in profiles
            ):
                raise ValueError(
                    f"Policy {policy} must map to one activation of a declared profile"
                )
    for rule in config.get("coordinator", {}).get("dedupe_rules", []):
        if rule.get("scope", "domain") not in {"global", "domain"}:
            raise ValueError("Dedupe scope must be global or domain")
        if rule.get("policy") not in policies:
            raise ValueError("Dedupe rule references unknown policy")
        positive(rule.get("window_seconds", 0), "dedupe window", True)
    return config


def load_syntony_config(path=None):
    path = Path(
        path
        or os.getenv("SYNTONY_CONFIG_PATH")
        or os.getenv("FRAMEWORK_CONFIG_PATH")
        or ROOT / "config/syntony_config.json"
    )
    data = assemble(path)
    parameters = data.pop("parameters", {})

    def expand(value):
        if isinstance(value, str):

            def replace(match):
                name = match.group(1)
                if name not in parameters:
                    raise ValueError(f"Undeclared configuration parameter: {name}")
                return os.getenv(name, str(parameters[name]))

            return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", replace, value)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    return validate(expand(data))


load_framework_config = load_syntony_config
