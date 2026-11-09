"""Compile domain-local metric and event composition rules."""

from ..common import load_syntony_config


def compare_value(value, operator_name, expected):
    if operator_name == "gt":
        return value > expected
    if operator_name == "gte":
        return value >= expected
    if operator_name == "lt":
        return value < expected
    if operator_name == "lte":
        return value <= expected
    if operator_name == "eq":
        return value == expected
    if operator_name == "neq":
        return value != expected
    raise ValueError(f"Unsupported operator: {operator_name}")


def _normalize_rule_type(rule_type):
    aliases = {
        "all": "all_metrics",
        "any": "any_metrics",
    }
    return aliases.get(rule_type, rule_type)


def _compile_rule(rule_cfg):
    rule_type = _normalize_rule_type(rule_cfg.get("type", "threshold"))
    compiled = {
        "name": rule_cfg["name"],
        "type": rule_type,
        "priority": rule_cfg.get("priority", 5),
        "force_process": rule_cfg.get("force_process", False),
        "skip_watermark_check": rule_cfg.get("skip_watermark_check", False),
        "cooldown_seconds": float(rule_cfg.get("cooldown_seconds", 0.0)),
        "metadata": rule_cfg.get("metadata", {}),
    }

    if rule_type == "threshold":
        compiled["metric"] = rule_cfg["metric"]
        compiled["operator"] = rule_cfg["operator"]
        compiled["value"] = rule_cfg["value"]
        return compiled

    if rule_type in ("all_metrics", "any_metrics"):
        compiled["conditions"] = rule_cfg.get("conditions", [])
        return compiled

    if rule_type == "metric_window":
        compiled["metric"] = rule_cfg["metric"]
        compiled["operator"] = rule_cfg["operator"]
        compiled["value"] = rule_cfg["value"]
        compiled["within_seconds"] = float(rule_cfg.get("within_seconds", 0.0))
        compiled["min_matches"] = int(rule_cfg.get("min_matches", 1))
        return compiled

    if rule_type in ("event_combo_all", "event_sequence"):
        compiled["event_types"] = rule_cfg.get("event_types", [])
        compiled["within_seconds"] = float(rule_cfg.get("within_seconds", 0.0))
        return compiled

    if rule_type == "external_check":
        compiled["path"] = rule_cfg["path"]
        return compiled

    raise ValueError(f"Unsupported rule type: {rule_type}")


def get_ruleset(domain):
    config = load_syntony_config()
    composer_cfg = config.get("domains", {}).get(domain, {}).get("composer", {})
    raw_rules = composer_cfg.get("rules", [])
    return [_compile_rule(rule_cfg) for rule_cfg in raw_rules]
