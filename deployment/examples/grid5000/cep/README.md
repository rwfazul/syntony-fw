# CEP component integration on one Grid node

This profile packages the correlation stage for an integration that already
provides metric acquisition and local event composition. Use it to run CEP
patterns against those observation streams on a reserved node. For a complete
pipeline with acquisition, coordination and execution, follow the
[Syntony one-node example](../syntony/README.md).

## Component connections

The profile starts the CEP image and Redis. Existing Monitors and Composers
supply observations through `events:obs` and domain progress through
`clock:watermark`. The CEP emits coordination events to `events:coord`.
Connect a Coordinator and Executor to the same Redis to apply actions and
publish execution feedback, or inspect the output when validating correlation
patterns independently.

Configure patterns and required domains before preparing the bundle through the
[common guide](../README.md) with `--profile cep`. Every required domain must
supply watermark progress for the CEP to close stable rounds.

## Network and stream access

Containers on the selected node can join `syntony_default` and connect to
`redis://redis:6379/0`. This profile exposes Redis within the Docker network.
For components running on other hosts, configure private, authenticated Redis
connectivity in the deployment.

Publish observation envelopes before their corresponding watermark progress.
Follow the [event contracts](../../../../docs/event-contracts.md) for payloads,
stream ordering and feedback. Inspect `clock:tick` for closed control rounds
and `events:coord` for matched patterns with
`docker compose exec redis redis-cli` on the selected node.
