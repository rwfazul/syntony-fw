"""Poll configured metric sources and publish timestamped domain measurements."""

import json
import math
import os
import subprocess
import time
import urllib.request

from ..common import get_logger, get_redis_client, load_syntony_config, log_event
from .utils import _extract_path, publish_metric


def collect(source):
    timeout = float(source.get("timeout_seconds", 5))
    if source["type"] == "command":
        result = subprocess.run(
            source["argv"], capture_output=True, text=True, check=True, timeout=timeout
        )
        payload = json.loads(result.stdout)
    elif source["type"] == "http_json":
        with urllib.request.urlopen(source["url"], timeout=timeout) as response:
            payload = json.load(response)
    elif source["type"] == "system":
        payload = {"load_avg_1m": os.getloadavg()[0], "timestamp": time.time()}
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as handle:
                mem = {line.split(":")[0]: int(line.split()[1]) for line in handle}
            payload["memory_available_bytes"] = mem["MemAvailable"] * 1024
    else:
        raise ValueError(f"Unsupported source: {source['type']}")
    event_time = time.time()
    if source.get("event_time_field"):
        try:
            raw_time = _extract_path(payload, source["event_time_field"])
            if isinstance(raw_time, bool):
                raise ValueError("Timestamp must not be a boolean")
            event_time = float(raw_time)
        except (TypeError, ValueError) as error:
            raise ValueError("Source timestamp is missing or invalid") from error
    if not math.isfinite(event_time):
        raise ValueError("Source event time must be finite Unix seconds")
    values = []
    for mapping in source["parser"]["metrics"]:
        value = _extract_path(payload, mapping["path"])
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Missing/non-numeric metric: {mapping['path']}")
        values.append((mapping["metric"], value))
    return event_time, values


def monitor_loop():
    config = load_syntony_config()
    domain = os.environ["DOMAIN"]
    sources = config["domains"][domain]["monitor"]["sources"]
    interval = float(config.get("timing", {}).get("sample_interval_seconds", 0.5))
    redis = get_redis_client()
    logger = get_logger("Monitor")
    sequence = 0
    while True:
        start = time.monotonic()
        for source in sources:
            if not source.get("enabled", True):
                continue
            try:
                event_time, pairs = collect(source)
                for metric, value in pairs:
                    sequence += 1
                    publish_metric(
                        redis,
                        domain,
                        metric,
                        value,
                        event_time,
                        time.time(),
                        sequence,
                        metadata={"source_name": source["name"], "source_kind": source["type"]},
                    )
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                log_event(
                    logger,
                    "error",
                    "source_collection_failed",
                    domain=domain,
                    source=source["name"],
                    error=str(error),
                )
        time.sleep(max(0, interval - (time.monotonic() - start)))


if __name__ == "__main__":
    monitor_loop()
