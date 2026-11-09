"""Independent librados latency probe, in the first monitor container."""

import json
import time

import rados

cluster = rados.Rados(conffile="/ceph/ceph.conf")
cluster.connect()
io = cluster.open_ioctx("kv-probe")
while True:
    start = time.monotonic()
    try:
        data = io.read("probe", 1048576)
        row = dict(
            timestamp=time.time(), duration_ms=(time.monotonic() - start) * 1000, bytes=len(data)
        )
    except Exception as exc:
        row = dict(timestamp=time.time(), error=str(exc))
    print(json.dumps(dict(timestamp=time.time(), rados_probe=row)), flush=True)
    time.sleep(max(0, 1 - (time.monotonic() - start)))
