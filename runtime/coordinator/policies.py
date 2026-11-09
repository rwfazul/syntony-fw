"""Policy-to-command mappings are declarations in the specification catalog."""

import copy

from ..common import load_syntony_config


def map_to_actions(event):
    mappings = load_syntony_config()["coordinator"]["policy_mappings"]
    if event["policy"] not in mappings:
        raise ValueError(f"Unconfigured policy: {event['policy']}")
    actions = copy.deepcopy(mappings[event["policy"]])
    for action in actions:
        action.setdefault("params", {})
        for field in (
            "force_process",
            "policy",
            "tick",
            "tick_start",
            "tick_end",
            "policy_profile",
            "policy_version",
            "composition_hash",
        ):
            if field in event:
                action[field] = event[field]
    return actions
