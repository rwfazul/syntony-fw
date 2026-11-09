"""Validate metric envelopes, apply watermark admission and compose domain-local observations."""

import os
import pathlib
import time

from ..common import get_logger, get_redis_client, load_syntony_config, log_event
from .rules import compare_value, get_ruleset
from .utils import (
    emit_clock_resync_request,
    emit_late_metric,
    emit_obs_event,
    emit_watermark,
    parse_metric_event,
)

logger = get_logger("Composer")


def file_exists(path: str) -> bool:
    return pathlib.Path(path).exists()


def prune_metric_history(metric_history, cutoff_event_time):
    """Drops metric history entries older than the configured event-time TTL."""
    for metric_name, entries in list(metric_history.items()):
        metric_history[metric_name] = [
            entry for entry in entries if entry["event_time"] >= cutoff_event_time
        ]
        if not metric_history[metric_name]:
            del metric_history[metric_name]


def prune_event_history(observed_history, cutoff_event_time):
    """Drops previously emitted observed events outside the event-time horizon."""
    observed_history[:] = [
        event for event in observed_history if event["event_time"] >= cutoff_event_time
    ]


def rule_on_cooldown(rule, watermark_state, last_emitted_at):
    """Returns whether a rule is still cooling down in event-time terms."""
    cooldown_seconds = rule.get("cooldown_seconds", 0.0)
    if cooldown_seconds <= 0.0:
        return False
    event_time = watermark_state["event_time"]
    previous = last_emitted_at.get(rule["name"])
    return previous is not None and (event_time - previous) < cooldown_seconds


def match_rule(rule, metric_event, latest_metrics, metric_history, observed_history):
    """Evaluates one configured rule against the current event-time snapshot.

    The function mixes different local views depending on the rule type:

    - latest metric values for threshold-like rules
    - bounded metric history for window rules
    - bounded observed-event history for event combination and sequence rules

    All windows are evaluated in event time, not processing order.
    """
    rule_type = rule["type"]
    event_time = metric_event["event_time"]

    if rule_type == "threshold":
        metric = rule["metric"]
        if metric not in latest_metrics:
            return False
        return compare_value(latest_metrics[metric], rule["operator"], rule["value"])

    if rule_type == "all_metrics":
        conditions = rule.get("conditions", [])
        if any(condition["metric"] not in latest_metrics for condition in conditions):
            return False
        return all(
            compare_value(
                latest_metrics[condition["metric"]], condition["operator"], condition["value"]
            )
            for condition in conditions
        )

    if rule_type == "any_metrics":
        conditions = rule.get("conditions", [])
        return any(
            condition["metric"] in latest_metrics
            and compare_value(
                latest_metrics[condition["metric"]], condition["operator"], condition["value"]
            )
            for condition in conditions
        )

    if rule_type == "metric_window":
        window_start = event_time - rule.get("within_seconds", 0.0)
        relevant = [
            entry
            for entry in metric_history.get(rule["metric"], [])
            if window_start <= entry["event_time"] <= event_time
            and compare_value(entry["value"], rule["operator"], rule["value"])
        ]
        return len(relevant) >= rule.get("min_matches", 1)

    if rule_type == "event_combo_all":
        window_start = event_time - rule.get("within_seconds", 0.0)
        relevant_types = {
            event["event_type"]
            for event in sorted(observed_history, key=lambda item: item["event_time"])
            if window_start <= event["event_time"] <= event_time
        }
        return all(event_type in relevant_types for event_type in rule.get("event_types", []))

    if rule_type == "event_sequence":
        window_start = event_time - rule.get("within_seconds", 0.0)
        relevant = [
            event["event_type"]
            for event in sorted(observed_history, key=lambda item: item["event_time"])
            if window_start <= event["event_time"] <= event_time
        ]
        target = rule.get("event_types", [])
        if not target:
            return False
        cursor = 0
        for event_type in relevant:
            if event_type == target[cursor]:
                cursor += 1
                if cursor == len(target):
                    return True
        return False

    if rule_type == "external_check":
        return file_exists(rule["path"])

    raise ValueError(f"Unsupported rule type: {rule_type}")


class WatermarkManager:
    """Tracks monotonic watermark progression and lateness violations.

    `observe()` applies Syntony's acceptance rule:

    - a metric is marked late when `event_time < watermark_before`
    - it is still accepted when it falls inside `allowed_lateness`
    - otherwise it is rejected and routed to `events:late`

    Consecutive lateness violations trigger rate-limited clock-resynchronization requests.
    """

    def __init__(
        self,
        watermark_delay,
        allowed_lateness,
        resync_threshold,
        resync_cooldown_seconds,
    ):
        self.watermark_delay = watermark_delay
        self.allowed_lateness = allowed_lateness
        self.resync_threshold = resync_threshold
        self.resync_cooldown_seconds = resync_cooldown_seconds
        self.max_event_time_seen = float("-inf")
        self.watermark = float("-inf")
        self.late_violation_streak = 0
        self.last_resync_request_at = 0.0

    def observe(self, event_time, processing_time, skip_watermark_check=False):
        """Updates watermark state after one metric arrival.

        Watermark advancement uses only the largest event time seen so far.
        Processing time is retained only to compute diagnostic lateness.
        """
        watermark_before = self.watermark
        late = event_time < watermark_before
        accepted = skip_watermark_check or event_time >= (watermark_before - self.allowed_lateness)
        lateness_seconds = processing_time - event_time

        self.max_event_time_seen = max(self.max_event_time_seen, event_time)
        next_watermark = self.max_event_time_seen - self.watermark_delay
        self.watermark = max(self.watermark, next_watermark)

        if late:
            self.late_violation_streak += 1
        else:
            self.late_violation_streak = 0

        return {
            "event_time": event_time,
            "watermark_before": watermark_before,
            "watermark": self.watermark,
            "late": late,
            "accepted": accepted,
            "lateness_seconds": lateness_seconds,
            "allowed_lateness": self.allowed_lateness,
            "watermark_delay": self.watermark_delay,
            "max_event_time_seen": self.max_event_time_seen,
            "late_violation_streak": self.late_violation_streak,
            "skip_watermark_check": skip_watermark_check,
        }

    def should_request_resync(self):
        """Throttles resync requests after repeated lateness violations."""
        if self.late_violation_streak < self.resync_threshold:
            return False

        now = time.time()
        if now - self.last_resync_request_at < self.resync_cooldown_seconds:
            return False

        self.last_resync_request_at = now
        return True


def composer_loop():
    """Consumes raw metrics and emits accepted observed events.

    The Composer processes each raw metric in this order:

    1. updates watermark state
    2. is either accepted or dropped as too late
    3. updates bounded local histories when accepted
    4. is evaluated against the configured ruleset
    5. may emit one or more observed events to `events:obs`
    """
    redis_client = get_redis_client()
    domain = os.getenv("DOMAIN", "computation")
    poll_interval = float(os.getenv("POLL_INTERVAL", "0.5"))
    watermark_delay = float(
        load_syntony_config().get("timing", {}).get("watermark_delay_seconds", 0.25)
    )
    allowed_lateness = float(
        load_syntony_config().get("timing", {}).get("allowed_lateness_seconds", 0.25)
    )
    resync_threshold = int(os.getenv("RESYNC_VIOLATION_THRESHOLD", "5"))
    resync_cooldown_seconds = float(os.getenv("RESYNC_COOLDOWN_SECONDS", "30.0"))
    ruleset = get_ruleset(domain)
    config = load_syntony_config()
    composer_cfg = config.get("domains", {}).get(domain, {}).get("composer", {})
    metric_history_ttl = float(composer_cfg.get("metric_history_ttl_seconds", 30.0))
    event_history_ttl = float(composer_cfg.get("event_history_ttl_seconds", 30.0))
    watermark_manager = WatermarkManager(
        watermark_delay,
        allowed_lateness,
        resync_threshold,
        resync_cooldown_seconds,
    )
    latest_metrics = {}
    metric_history = {}
    observed_history = []
    last_emitted_at = {}

    log_event(
        logger,
        "info",
        "composer_started",
        domain=domain,
        poll_interval=poll_interval,
        watermark_delay=watermark_delay,
        allowed_lateness=allowed_lateness,
        resync_threshold=resync_threshold,
        rules=[rule["name"] for rule in ruleset],
    )
    last_id = "0-0"

    while True:
        resp = redis_client.xread(
            {f"metrics:{domain}": last_id},
            block=int(poll_interval * 1000),
            count=10,
        )

        if not resp:
            continue

        _, events = resp[0]
        for eid, event_data in events:
            last_id = eid
            try:
                metric_event = parse_metric_event(event_data)
                if metric_event["domain"] != domain:
                    raise ValueError("Metric domain does not match its input stream")
            except (KeyError, TypeError, ValueError) as error:
                log_event(
                    logger,
                    "warning",
                    "metric_ignored",
                    domain=domain,
                    record_id=eid,
                    reason=str(error),
                )
                continue
            metric_name = metric_event["metric"]["metric"]
            metric_value = metric_event["metric"]["value"]
            skip_watermark_check = metric_event.get("skip_watermark_check", False)
            watermark_state = watermark_manager.observe(
                metric_event["event_time"],
                metric_event["processing_time"],
                skip_watermark_check=skip_watermark_check,
            )
            log_event(
                logger,
                "info",
                "watermark_updated",
                domain=domain,
                metric=metric_name,
                event_time=round(metric_event["event_time"], 6),
                watermark=round(watermark_state["watermark"], 6),
                watermark_before=watermark_state["watermark_before"],
                late=watermark_state["late"],
                accepted=watermark_state["accepted"],
                skip_watermark_check=skip_watermark_check,
            )

            if not watermark_state["accepted"]:
                emit_late_metric(metric_event, redis_client, watermark_state)
                log_event(
                    logger,
                    "warning",
                    "late_metric_dropped",
                    domain=domain,
                    metric=metric_name,
                    event_time=round(metric_event["event_time"], 6),
                    watermark=round(watermark_state["watermark"], 6),
                    lateness_seconds=round(watermark_state["lateness_seconds"], 6),
                )
                if watermark_manager.should_request_resync():
                    emit_clock_resync_request(
                        redis_client,
                        domain,
                        "watermark_violation_streak",
                        {
                            "late_violation_streak": watermark_state["late_violation_streak"],
                            "watermark": watermark_state["watermark"],
                            "allowed_lateness": allowed_lateness,
                        },
                    )
                    log_event(
                        logger,
                        "warning",
                        "clock_resync_requested",
                        domain=domain,
                        metric=metric_name,
                        late_violation_streak=watermark_state["late_violation_streak"],
                        watermark=round(watermark_state["watermark"], 6),
                    )
                emit_watermark(redis_client, domain, watermark_state)
                continue

            latest_metrics[metric_name] = metric_value
            metric_history.setdefault(metric_name, []).append(
                {
                    "metric": metric_name,
                    "value": metric_value,
                    "event_time": metric_event["event_time"],
                    "processing_time": metric_event["processing_time"],
                    "metadata": metric_event.get("metadata", {}),
                }
            )
            prune_metric_history(metric_history, metric_event["event_time"] - metric_history_ttl)
            latest_metrics = {
                name: max(entries, key=lambda item: item["event_time"])["value"]
                for name, entries in metric_history.items()
            }
            prune_event_history(observed_history, metric_event["event_time"] - event_history_ttl)

            for rule in ruleset:
                if rule_on_cooldown(rule, watermark_state, last_emitted_at):
                    continue
                if not match_rule(
                    rule, metric_event, latest_metrics, metric_history, observed_history
                ):
                    continue

                emitted_event = emit_obs_event(rule, metric_event, redis_client, watermark_state)
                observed_history.append(emitted_event)
                last_emitted_at[rule["name"]] = metric_event["event_time"]
                log_event(
                    logger,
                    "info",
                    "obs_event_emitted",
                    domain=domain,
                    rule=rule["name"],
                    rule_type=rule["type"],
                    origin_metric=metric_name,
                    event_time=round(metric_event["event_time"], 6),
                    watermark=round(watermark_state["watermark"], 6),
                    late=watermark_state["late"],
                    force_process=rule.get("force_process", False),
                    skip_watermark_check=skip_watermark_check
                    or rule.get("skip_watermark_check", False),
                )

            emit_watermark(redis_client, domain, watermark_state)


if __name__ == "__main__":
    try:
        composer_loop()
    except KeyboardInterrupt:
        log_event(logger, "info", "composer_stopped")
