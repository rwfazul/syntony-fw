"""Decode coordination-event envelopes read from Redis Streams."""

import json


def parse_coord_event(e_raw):
    """Decode a coordination-event envelope read from Redis Streams."""
    data = json.loads(e_raw["data"])
    return data
