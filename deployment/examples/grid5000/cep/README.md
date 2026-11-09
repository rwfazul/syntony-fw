# CEP on one Grid node

Use the [common guide](../README.md) with `--profile cep`. This profile starts only the CEP module image and Redis; it does not start Monitors, Composers, Coordinator or Executor.

Configure patterns and required domains before preparing the bundle. Connect real observation/watermark producers to the Docker network `syntony_default` and `redis://redis:6379/0`. Redis is not published on the host network by this compact profile. For producers on other hosts, provide explicitly configured private/authenticated connectivity rather than assuming a Docker bridge spans machines.

Publish actual `events:obs` envelopes before their corresponding watermark progress. Follow the [event contracts](../../../../docs/event-contracts.md). Without all required domains progressing, the CEP should wait; no fabricated events or watermarks are supplied to make it appear active.

Use `docker compose exec redis redis-cli` on the selected node to inspect `clock:tick` and `events:coord`. Running containers alone do not demonstrate valid correlation. This is one CEP instance, not a distributed CEP cluster.
