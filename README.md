# Syntony

Syntony is an event-driven control framework for adaptive reconfiguration across domains in data-intensive systems. It correlates observations from multiple domains in event time and uses policies to decide when to act.

> An open-source implementation of Syntony, based on the architecture and instantiation guidelines described in the thesis ([Fazul, 2026](#how-to-cite)).

This repository builds on an earlier implementation developed during the doctoral research. Its commit history begins with the preparation of the public distribution.

The control pipeline separates metric acquisition, local interpretation, event-time correlation, policy admission, execution and feedback.

**Monitor → Event Composer → CEP → Coordinator → Executor → execution feedback**

The implementation uses Redis Streams. Domain knowledge is configured through a local **Specification Catalog**.

See the [runtime architecture](docs/modules.md#architecture) and the [end-to-end control flow](docs/event-contracts.md#end-to-end-control-flow). The [documentation index](docs/README.md) guides you from setup to extension.

## Repository layout

```text
config/                         instance selection and global timing
specifications/
  domains/                      monitor definitions and local composition rules
  events/                       cross-event and cross-domain CEP patterns
  policies/                     action mappings, handlers, deduplication
runtime/
  monitor/                      real metric acquisition; Dockerfile and requirements
  composer/                     local event rules and watermark admission
  cep/                          stable control rounds and event correlation
  coordinator/                  arbitration, blocking and command dispatch
  executor/                     real action execution and feedback
  internal_state/               policy flags, exclusive plans and outcome transitions
scripts/                        source bundling, local integration, stream inspection
deployment/
  examples/local/               full local Compose and real managed application
  examples/monitors/            real command, HTTP and OS acquisition examples
  examples/grid5000/            single-node and six-node deployment examples
  docker/                       optional all-module toolbox image
docs/                           extension guide, event contracts and implementation guide
tests/                          runtime, HTTP/process and offline deployment tests
docker-compose.yml              standard Compose entry point
LICENSE                         MIT license
```

The four main directories follow the repository organization described in the thesis chapter on instantiation.

| Area | Syntony implementation |
|---|---|
| `config/` | [Instance configuration](config/README.md) selects catalog packages and sets sampling, watermark and control-round parameters. |
| `specifications/` | The [Specification Catalog](specifications/README.md) contains domain packages, CEP patterns and policy mappings. Each domain package groups its monitor sources and local composition rules in one JSON file. |
| `runtime/` | [Runtime components](runtime/README.md) implement Monitor, Event Composer, CEP, Coordinator and Executor. The logical Internal state is persisted in Redis. |
| `deployment/` | [Deployment artifacts](deployment/README.md) package and connect components for local Compose and Grid’5000. Each module's Dockerfile lives alongside its implementation. |

## Events, temporal semantics and feedback

See [event contracts](docs/event-contracts.md) for stream names and payloads and [algorithm alignment](docs/algorithm-alignment.md) for the implementation/test mapping.

The Composer compares a metric against the previous watermark minus the lateness tolerance, advances its watermark monotonically, and routes rejected metrics to `events:late`. Accepted observations are emitted before their corresponding watermark progress. The CEP uses the minimum watermark of all required domains and closes stable control rounds in order. A missing/idle required domain stalls progress; its watermark is not fabricated.

CEP patterns support `count_event`, `same_round_all`, `participants_all`, and `passthrough_event`. Use explicit participants for cross-domain correlation; `count_event` and `same_round_all` also accept an optional single-domain filter. Coordination types are `E_trg`, `E_sync`, `E_block` and `E_unblock`, using the thesis notation. `E_block` activates a named action guard; `E_unblock` releases that guard. Action mappings and handlers reside in configuration. A missing handler emits `action_fail`; command failure and timeout are not converted into success.

Execution feedback is an `E_fed` observation with `action_done`, `action_fail`, or `action_timeout`, action/ticket/coordination IDs, and handler verification details. Its event time is the actual completion/report time; the triggering round is retained separately. Shell interpolation is not implicit: handlers receive an `argv` array.

The [specification operator map](docs/specification-operators.md) relates the thesis notation to catalog rules, temporal patterns and state constraints.

## Configure and extend the Specification Catalog

An instance configuration selects catalog packages, sets temporal parameters and binds the runtime to a target environment. The [Specification Catalog](specifications/README.md) organizes the reusable definitions into three layers. Domain packages define metric sources and local observation rules. Event packages define CEP correlation patterns. Policy packages map coordination events to actions and set their admission constraints.

Includes resolve relative to the file that declares them. Objects merge recursively and lists concatenate. Conflicting scalar values are rejected. A declared parameter such as `${ENDPOINT}` can be set through the environment. Run `make validate` to check the assembled configuration and its references. [Catalog extension guide](docs/extending-the-catalog.md)

**Meta-adaptation** lets Syntony change the policy that governs subsequent adaptations in response to observed conditions. The CEP activates a declared profile at a stable-round boundary and records the active version in its checkpoint. Changes to catalog definitions or CEP timing settings require a fresh Redis instance or a coordinated migration of the saved state and streams. [Meta-reconfiguration guide](docs/meta-reconfiguration.md) · [CEP recovery](docs/event-contracts.md#cep-recovery)

## Quick start

The [default example instance](config/syntony_config.json) configures two domains for HTTP service recovery. The application domain observes the HTTP service, and the dependency domain tracks the availability of its external dependency. Syntony correlates these observations to select recovery actions.

The [example catalog](specifications/README.md#supplied-examples) includes its domain definitions, correlation patterns and recovery policies. The optional [meta-adaptation example](docs/meta-reconfiguration.md#run-the-example) switches to a fallback profile on dependency failure and returns to the normal profile on recovery.

Requirements: Docker Engine running, Docker Compose 2.20+ (the root file uses `include`), and Python 3.11+ for host-side validation and demonstrations. Run the following commands from this repository's root:

```bash
python3 -m runtime validate
make up
python3 deployment/examples/local/demo.py
make logs
```

The root `docker-compose.yml` is a convenience entry point so `docker compose up` works from the checkout. Deployment definitions live under `deployment/`; this arrangement keeps the service configuration in one place. The standard equivalent is `docker compose up --build -d --wait`. The root `docker-compose.yml` includes the full service definition in [deployment/examples/local/compose.json](deployment/examples/local/compose.json), which can also be used directly with `docker compose -f deployment/examples/local/compose.json ...`. JSON is a valid Compose/YAML representation; there is one canonical set of service definitions.

The example exposes `http://127.0.0.1:18081`. It collects actual HTTP metrics, terminates a real supervised child process, correlates application and dependency observations, invokes a restart handler, and checks that the process generation and count of restarts triggered by Syntony increase. `verified: true` means the recovery check passed. Failure or timeout is reported as failure.

The managed dependency returns its actual hostname. Fallback serves only a response previously obtained from that dependency; an empty cache produces an error. Fault injection endpoints are confined to the example.

```bash
# Use a different external gateway port.
GATEWAY_PORT=18082 make up
python3 deployment/examples/local/demo.py --gateway http://127.0.0.1:18082

# Stop while keeping Redis state.
make down

# Explicitly discard this instance's state before a clean demonstration.
docker compose down --volumes
```

Do not run simultaneous fault demonstrations on the same instance. See the [local deployment guide](deployment/examples/local/README.md) for service wiring, development mounts, image naming, health checks, and individual module commands.

## Module-specific images

Each executable module has its own Dockerfile and requirements file under `runtime/<module>/`. The local Compose builds these Dockerfiles using the repository root as context. Both domain Monitors use the Monitor image; both Composers use the Composer image. CEP, Coordinator, and Executor use their corresponding images. The managed HTTP service has a separate example image.

| Image suffix | Dockerfile | Responsibility |
|---|---|---|
| `monitor` | `runtime/monitor/Dockerfile` | Collect real HTTP, command or OS metrics |
| `composer` | `runtime/composer/Dockerfile` | Admit metrics and compose local observed events |
| `cep` | `runtime/cep/Dockerfile` | Correlate stable event-time rounds |
| `coordinator` | `runtime/coordinator/Dockerfile` | Apply mappings, priority, deduplication and leases |
| `executor` | `runtime/executor/Dockerfile` | Execute handlers and emit correlated outcomes |
| `example` | `deployment/examples/local/Dockerfile` | Run the managed HTTP dependency/supervisor |

The default names are `syntony-<module>:dev`. Set `IMAGE_PREFIX` and `IMAGE_TAG` to use another namespace/version. The images include common runtime code because configuration validation and event contracts span modules, while each image has its own default process and dependency file.

Build `deployment/docker/Dockerfile` for an all-module toolbox image. The local Compose uses the per-module Dockerfiles.

## Python setup and package interfaces

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
make validate hygiene lint format-check test compose-check
```

The Python dependency `redis>=5.2,<6` selects the redis-py client. The local Compose stack runs Redis Server 7.4.

Install a local `redis-server` executable to include the isolated Redis transaction tests in `make test`. Set `REDIS_SERVER` if it is outside `PATH`. CI installs Redis and runs these tests.

Every module package exposes a documented interface, for example:

```python
from runtime import load_syntony_config
from runtime.monitor import collect
from runtime.composer import WatermarkManager, match_rule
from runtime.cep import compile_pattern, effective_watermark
from runtime.coordinator import map_to_actions
from runtime.executor import process_executor_command
```

These CLI commands start the modules for the HTTP recovery example:

```bash
python -m runtime monitor --domain application_service
python -m runtime composer --domain application_service
python -m runtime cep
python -m runtime coordinator
python -m runtime executor
```

Outside Docker, set `REDIS_URL` to the Redis endpoint. If you run the HTTP recovery example, also set `GATEWAY_URL` to the address of its gateway. When running from outside the checkout, pass `--config /absolute/path/to/instance.json` to select your instance configuration.

## Monitor examples

The [monitor guide](deployment/examples/monitors/README.md) includes runnable configurations for built-in OS collection, a JSON command measuring actual host load/disk capacity, and HTTP collection from the example service. Each can be inspected once without Redis or run with a Monitor and Composer to publish observations.

The optional [host-guard instance](config/service-resilience-host-guard.json) adds host-load observations to the service recovery example and temporarily blocks restarts during host pressure. See [instance configurations](config/README.md) for the supplied profiles.

For multimetric rules, windows and recovery sequences, see the [Composer examples](config/examples/README.md#composer-examples).

## Useful operational scripts

| Command | Purpose |
|---|---|
| `make integration` | Build/start local modules, verify real recovery, show logs, stop the instance |
| `python scripts/bundle.py --output dist/syntony-fw.tar.gz` | Package Syntony sources with SHA-256 manifests for transfer |
| `python scripts/inspect_streams.py --redis-url redis://HOST:6379/0` | Inspect counts and recent real event envelopes |
| `python deployment/examples/grid5000/deploy.py --help` | Prepare/deploy/check the one-node Syntony or CEP profile |

`make integration` refuses to replace an already-running instance. It opens real HTTP ports and uses Docker; invoke it explicitly. Tests include actual HTTP/process checks, isolated Redis transactions for admission and recovery, and storage doubles for individual temporal rules.

## Grid’5000 deployment examples

The [Grid deployment guide](deployment/examples/grid5000/README.md) distinguishes three cases:

1. **Syntony on one node:** deploy the complete control pipeline with the HTTP recovery example.
2. **CEP component on one node:** deploy the correlation stage alongside an existing integration that supplies observations and domain watermarks. The [component guide](deployment/examples/grid5000/cep/README.md) explains how the other stages connect.
3. **Managed system and Syntony across six nodes:** distribute the managed system and control components across reserved nodes. The supplied [multinode example](deployment/examples/grid5000/ceph/README.md) uses Ceph as example storage infrastructure and runs Syntony against the HTTP recovery application.

The [Ceph deployment example](deployment/examples/grid5000/ceph/README.md) shows how to configure the nodes, distribute images and check cluster health. To use Syntony to manage Ceph, add Ceph metrics and action handlers to the Specification Catalog.

No Grid allocation or live deployment is performed by `prepare`. Build and run on reserved resources, not on the site frontend. Check the generated topology, network bindings and cluster health in the chosen reservation before running workloads.

## Policy state and recovery

Policies declare domain-qualified boolean flags, admission guards, flags to set on dispatch, deadlines and an exclusivity class. Admission reserves the complete plan and enqueues its commands atomically in Redis. Executor outcomes apply `on_success`, `on_failure`, `on_timeout` or `on_uncertain` effects once per action. Timeout or uncertain execution retains the exclusive reservation; a successful recovery handler must report `{"verified": true}` before it can release it.

The HTTP recovery example demonstrates these mechanisms with a read-only `VERIFY_LOCAL_SERVICE` recovery action. See [state and policy configuration](docs/state-and-policies.md) for the complete contract and examples.

## State and supported operating model

Use one Coordinator and one Executor per Redis instance. Different instances require separate Redis state. Coordinator/Executor consumer groups retain pending deliveries, and action IDs prevent automatic repetition of an already-recorded external action. A crash after an action starts but before its result is saved yields `execution_uncertain`; the configured uncertainty transition retains guards until verified recovery. Feedback may be redelivered; consumers must recognize action IDs.

The CEP checkpoints its Round Table, domain watermarks, round origin, last closed round and input stream cursors in Redis. It commits each batch of round notifications and coordination events with its checkpoint, then resumes from those cursors after restart. The Composer keeps separate temporal state in memory. See [CEP recovery](docs/event-contracts.md#cep-recovery) for configuration and restart behavior. There is no general exactly-once side-effect guarantee, distributed transaction, rollback, dynamic executor dependency scheduler, or automatic state retention/expunge policy. Choose idempotent adapters and an explicit lifecycle for a deployment. Administrative clock-resynchronization requests are diagnostic unless an external operational adapter handles them; the runtime does not pretend to perform NTP/PTP.

## How to cite

If you use Syntony in your research, cite the following PhD thesis. The entry is also available as a [BibTeX file](CITATION.bib).

### Suggested citation

Fazul, Rhauani Weber Aita. 2026. *Syntony: An Adaptive Event-driven Control Framework for Multi-domain Reconfiguration in Data-intensive Systems*. PhD thesis, Universidade Federal de Santa Catarina (UFSC), Florianópolis, Brazil. Forthcoming.

### BibTeX

```bibtex
@phdthesis{fazul2026syntony,
  author  = {Fazul, Rhauani Weber Aita},
  title   = {{Syntony: An Adaptive Event-driven Control Framework for Multi-domain Reconfiguration in Data-intensive Systems}},
  school  = {Universidade Federal de Santa Catarina (UFSC)},
  type    = {{PhD} thesis},
  address = {Florian{\'o}polis, Brazil},
  year    = {2026},
  note    = {Forthcoming}
}
```

## Local development files

`.ruff_cache/` stores temporary results from the Ruff formatter/linter. It is generated locally and excluded from Git, Docker build contexts and source bundles. Python `__pycache__/`, virtual environments, build outputs and local environment files are excluded as well. `.github/workflows/` contains the automated repository checks.

## License and contribution

Licensed under the [MIT License](LICENSE), using the [standard MIT text](https://choosealicense.com/licenses/mit/). Containerized third-party software remains subject to its respective licenses. See [CONTRIBUTING.md](CONTRIBUTING.md) and [release checks](docs/release-checklist.md).
