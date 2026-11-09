# Syntony on one Grid node

Use the [common guide](../README.md) with `--profile syntony`. This profile starts all five runtime roles, Redis, two domain Monitors/Composers, and the actual HTTP service/dependency on the selected node. Each runtime role uses its corresponding module image.

The gateway binds `127.0.0.1:18081` on that node. After deployment, run the actual process-recovery demonstration from the frontend:

```bash
ssh root@NODE docker compose -p syntony -f /opt/syntony/compose.json \
  exec -T gateway python deployment/examples/local/demo.py --gateway http://127.0.0.1:8080
```

Replace NODE with the generated inventory's node. The demo sends real HTTP requests and requires an actual new worker generation. Inspect `events:coord`, `executor:commands` and `events:obs` plus service logs to trace the decision and feedback.

This compact example does not distribute Syntony components over several machines. Use the [six-node example](../ceph/README.md) with `--layout distributed` for explicit cross-node placement.
