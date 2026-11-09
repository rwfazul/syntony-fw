"""Shared Redis transport, configuration and structured logging."""

import json
import logging
import os
import time
from functools import lru_cache

from .configuration import load_syntony_config as _read_configuration


@lru_cache(maxsize=1)
def load_syntony_config():
    """Freeze one validated configuration snapshot per runtime process."""
    return _read_configuration()


def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(logger, level, event, domain=None, **fields):
    payload = dict(ts=time.time(), module=logger.name, event=event, **fields)
    if domain is not None:
        payload["domain"] = domain
    getattr(logger, level.lower())(json.dumps(payload, sort_keys=True))


def get_redis_client():
    import redis

    return redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_connect_timeout=5,
    )
