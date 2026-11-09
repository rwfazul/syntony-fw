"""Versioned profile activation at a CEP control-round boundary."""

import hashlib
import json
import math
import time

ACTIVE_PROFILE = "syntony:policy:active"
PROFILE_REQUESTS = "control:profile_requests"
PROFILE_ACKS = "syntony:policy:activation_acks"
PROFILE_AUDIT = "control:policy_audit"


def profile_matches(client, envelope):
    """Check the generation carried by a coordination event."""
    raw = client.get(ACTIVE_PROFILE)
    if raw is None:
        return "policy_version" not in envelope
    active = json.loads(raw)
    return all(
        envelope.get(field) == active[field]
        for field in ("policy_profile", "policy_version", "composition_hash")
    )


class PolicyProfiles:
    """Select catalog patterns and persist the active profile with the Round Table."""

    def __init__(self, config, patterns, saved):
        cep = config["cep"]
        self.catalog = {pattern["name"]: pattern for pattern in patterns}
        self.definitions = cep["policy_profiles"]
        self.common = self.catalog.keys() - {
            name for names in self.definitions.values() for name in names
        }
        self.source_patterns = cep["patterns"]
        self.mappings = config["coordinator"]["policy_mappings"]
        self.state = saved.get("active_profile") or self.describe(cep["initial_policy_profile"], 1)
        self.pending = saved.get("profile_requests", [])
        self.acknowledged = set()
        patterns[:] = self.select(self.state["policy_profile"])

    def select(self, profile):
        enabled = self.common | set(self.definitions[profile])
        return [pattern for name, pattern in self.catalog.items() if name in enabled]

    def describe(self, profile, version):
        names = {pattern["name"] for pattern in self.select(profile)}
        composition = {
            "patterns": [pattern for pattern in self.source_patterns if pattern["name"] in names],
            "mappings": {name: self.mappings[name] for name in names if name in self.mappings},
        }
        return {
            "policy_profile": profile,
            "policy_version": version,
            "composition_hash": hashlib.sha256(
                json.dumps(composition, sort_keys=True).encode()
            ).hexdigest(),
        }

    def enqueue(self, request):
        deadline = request.get("deadline")
        if (
            isinstance(request.get("id"), str)
            and request["id"]
            and isinstance(request.get("target_profile"), str)
            and type(request.get("expected_version")) is int
            and type(deadline) in (int, float)
            and math.isfinite(deadline)
        ):
            self.pending.append(request)

    def before_round(self, client, batch, patterns, round_table, round_index, round_start):
        """Activate at most one requested profile and acknowledge requests once."""
        while self.pending:
            request = self.pending.pop(0)
            identity = request["id"]
            if identity in self.acknowledged or client.hexists(PROFILE_ACKS, identity):
                continue
            target = request["target_profile"]
            valid = (
                target in self.definitions
                and target != self.state["policy_profile"]
                and request["expected_version"] == self.state["policy_version"]
                and time.time() <= request["deadline"]
            )
            if valid:
                previous = self.state["policy_profile"]
                self.state = self.describe(target, self.state["policy_version"] + 1)
                self.state.update(
                    activation_round=round_index,
                    cutover_event_time=round_start,
                    previous_profile=previous,
                )
                patterns[:] = self.select(target)
                for index in list(round_table):
                    if index < round_index:
                        del round_table[index]
            ack = {
                **self.state,
                "verified": valid,
                "activation_id": identity,
                "coordination_event_id": request.get("coordination_event_id"),
                "type": "policy_activated" if valid else "policy_activation_rejected",
            }
            batch.hset(PROFILE_ACKS, identity, json.dumps(ack))
            batch.xadd(PROFILE_AUDIT, {"data": json.dumps(ack)})
            self.acknowledged.add(identity)
            if valid:
                break
        return self.state


def request_activation(client, action, deadline):
    """Submit the admitted action and wait for confirmation from the CEP."""
    identity = action["action_id"]
    params = action.get("params", {})
    request = {
        "id": identity,
        "target_profile": params.get("target_profile"),
        "expected_version": action.get("policy_version"),
        "deadline": deadline,
        "coordination_event_id": action.get("coordination_event_id"),
    }
    if (
        not isinstance(request["target_profile"], str)
        or type(request["expected_version"]) is not int
    ):
        return {"success": False, "status": "invalid_profile_request", "mode": "activate_profile"}
    if not client.hexists(PROFILE_ACKS, identity) and time.time() < deadline:
        client.xadd(PROFILE_REQUESTS, {"data": json.dumps(request)})
    while True:
        raw = client.hget(PROFILE_ACKS, identity)
        if raw is not None:
            ack = json.loads(raw)
            return {
                "success": ack["verified"],
                "status": ack["type"],
                "mode": "activate_profile",
                "stdout": raw,
            }
        if time.time() >= deadline:
            return {"success": False, "status": "timeout", "mode": "activate_profile"}
        time.sleep(0.05)
