"""Durable pending command delivery for one Coordinator and one Executor."""


def read_batches(client, stream, group, poll_interval):
    import redis

    try:
        client.xgroup_create(stream, group, id="0-0", mkstream=True)
    except redis.ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
    consumer = "single-worker"
    while True:
        batches = client.xreadgroup(group, consumer, {stream: "0"}, count=20)
        if not batches or not any(rows for _, rows in batches):
            batches = client.xreadgroup(
                group, consumer, {stream: ">"}, count=20, block=max(1, int(poll_interval * 1000))
            )
        for _, rows in batches:
            if rows:
                yield rows


def read_group(client, stream, group, poll_interval):
    for rows in read_batches(client, stream, group, poll_interval):
        yield from rows
