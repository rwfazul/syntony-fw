# Specification operators

The PhD thesis uses logical and temporal notation to describe Syntony rules.
The catalog expresses those rules through Composer rule families, CEP pattern
families and policy constraints. The table maps each thesis operator to the
current implementation and identifies the scope of each correspondence.

## Thesis notation and catalog support

| Thesis notation | Meaning | Catalog representation and scope |
|---|---|---|
| `∧` | Conjunction | Expression `and` requires all child conditions in the evaluated round. Composer `all_metrics`, local `event_combo_all` and CEP `participants_all` retain their existing semantics. |
| `∨` | Disjunction | Expression `or` accepts any child condition. Composer `any_metrics` supports metric disjunction. |
| `¬` | Negation | Expression `not` negates one child condition, including event absence in a closed round. Existing comparisons, forbidden participants and false-valued policy guards remain supported. |
| `⊕` | Temporal / same-round alignment | `same_round_all` aligns named event types. Expression event predicates joined by `and` evaluate domain-qualified observations in the same stable round. |
| `□` | Invariance / always | Expression `always` requires its child to hold in every round of a complete trailing window of `rounds`. This is bounded invariance over the evaluated trace. |
| `◇` | Eventuality / eventually | Expression `eventually` finds a condition in a complete trailing window. An `obligation` tracks prospective eventuality after its `when` condition and reports satisfaction or expiry. |
| `U` | Persistence until | An `obligation` with `maintain` requires that condition in each round until `ensure` becomes true. Maintenance failure or expiry violates the bounded obligation. |
| `⇒[t]` | Bounded temporal implication | An `obligation` starts when `when` matches and requires `ensure` by `within_rounds`. The bound is measured in control rounds. |
| `↦` | Condition-to-intent mapping | A matched expression or selected obligation outcome emits `coord_type`. Its pattern name selects policy mappings for action-producing intents. |
| `↑` | Event emission | Composer, CEP and Executor publish observations, coordination and feedback to their Redis streams. |
| `@t` | Time of occurrence | Observations retain `event_time`. Operator decisions also identify their stable evaluation round. Expression and obligation decisions use `base_event: round_evaluation` to identify their evaluation context. |
| `state(x)` | Current state | Expression `state` compares a declared, domain-qualified boolean flag with `value`. Each evaluation round captures a coherent snapshot of committed flags. |
| `prev(x)` | Previous state | Expression `prev` reads the value immediately before that flag's last committed assignment. Dispatch and outcome transactions persist the previous value atomically with the current one. |

Scalar comparisons `gt`, `gte`, `lt`, `lte`, `eq` and `neq` are
predicates within Composer rules. Conjunction, temporal alignment and policy
admission combine conditions at different stages of the pipeline.

## Control rounds and clock ticks

A control round is the event-time interval indexed by one row of the Round
Table. The CEP evaluates a round when the minimum required-domain watermark
makes it stable. A clock tick reports that progress.

Use `same_round_all` in new patterns. The spelling `same_tick_all` remains an
accepted alias. New catalog files use round names for configuration. Previous tick names are
accepted as aliases. Conflicting aliases are rejected. Configuration is
normalized to the existing persisted representation so equivalent round/tick
spellings preserve CEP checkpoint and policy-profile identities.

| Identifier | Meaning |
|---|---|
| `cep.round.seconds` | Duration of one control round in seconds |
| `cep.round.retention_rounds` | Number of rounds retained in the Round Table |
| `cep.relative_round_origin` | Select a round origin relative to the initial event-time frontier |
| `within_rounds` | Trailing window size for count and participant patterns, or the number of rounds after the trigger allowed for an obligation |
| `require_current_round` | Require at least one positive participant in the evaluated round |
| `tick`, `tick_start`, `tick_end` | Round index and its event-time boundaries in envelopes |
| `clock:tick` | Stream reporting closed control rounds |
| `payload.trigger_tick_end` | Boundary of the round that originated an executed action |

### Configuration aliases

| Preferred name | Accepted previous name |
|---|---|
| `cep.round` | `cep.tick` |
| `cep.round.retention_rounds` | `cep.tick.retention_ticks` |
| `cep.relative_round_origin` | `cep.relative_tick_origin` |
| `within_rounds` | `within_ticks` |
| `require_current_round` | `require_current_tick` |

The stream `clock:tick` reports clock progress. Envelope fields `tick`,
`tick_start`, `tick_end` and `trigger_tick_end` keep their published names.

## Domain-qualified same-round alignment

This pattern requires both observations in the same stable round and vetoes
the intent if a maintenance observation is present there.

```json
{
  "name": "restart_with_dependency_available",
  "type": "participants_all",
  "within_rounds": 1,
  "participants": [
    {"domain": "application_service", "event_type": "service_failed"},
    {"domain": "external_dependency", "event_type": "dependency_available"}
  ],
  "forbidden_participants": [
    {"domain": "application_service", "event_type": "maintenance_active"}
  ],
  "coord_type": "E_sync"
}
```

Declare the observation names in the domain Composer rules, include both domains
in `cep.watermark_domains` and provide the corresponding policy mapping before
using this illustrative pattern. The [supplied catalog examples](../specifications/README.md#supplied-examples)
provide complete instance selections.

## Expression syntax

An `expression` pattern declares its decision domain and an expression tree.
Every `event` leaf requires `domain` and `event_type`; optional `where` fields
use equality on top-level envelope fields. Event leaves inspect observations in
the round currently being evaluated. A matching expression emits once per
stable round. Use Coordinator deduplication and state guards to control
repeated action admission. Join them with `and` or `or` through
`args`, or negate one child with `not` and `arg`.

```json
{
  "name": "local_failure_with_dependency_available",
  "type": "expression",
  "domain": "application_service",
  "coord_type": "E_sync",
  "expression": {
    "op": "and",
    "args": [
      {"op": "event", "domain": "application_service", "event_type": "local_service_unavailable"},
      {"op": "event", "domain": "external_dependency", "event_type": "dependency_available"}
    ]
  }
}
```

`always` and `eventually` wrap one `arg` and declare a positive integer
`rounds`. Nested windows add their history requirements. For example,
`always(2, eventually(3, E))` needs four rounds of history. The first evaluation
waits for the whole window after startup or profile activation. Empty rounds
participate in evaluation. The retention horizon must cover the complete
expression window.

A `state` or `prev` leaf requires `domain`, `flag` and an optional boolean
`value` that defaults to `true`. These leaves refer to declared policy flags.
Their snapshots represent committed state at evaluation time. They do not
reconstruct the flag's value at a historical observation timestamp. Historical
windows reuse each round's saved snapshot.

Before the first tracked assignment, `prev` defaults to the current value.
Assignments that leave a flag unchanged still count as assignments. Replayed
outcomes leave both values unchanged because each outcome is applied once.

## Prospective obligations

An `obligation` pattern declares `when`, `ensure`, a positive integer
`within_rounds` and optional `maintain`. All three conditions use expression
syntax. `when` may inspect a bounded history. `ensure` and `maintain` describe
the current round and accept Boolean combinations of event and flag leaves.

```json
{
  "name": "verify_recovery_deadline",
  "type": "obligation",
  "domain": "application_service",
  "coord_type": "E_trg",
  "when": {
    "op": "event",
    "domain": "application_service",
    "event_type": "local_service_unavailable"
  },
  "ensure": {
    "op": "event",
    "domain": "application_service",
    "event_type": "local_service_available"
  },
  "within_rounds": 3,
  "emit_on": "violated"
}
```

A match of `when` creates one obligation per pattern per stable round.
Observations in the same round collectively form that trigger. If round `k`
starts an obligation with bound `N`, `ensure` may satisfy it in rounds
`k` through `k + N`, including the deadline round. If `ensure` holds, the
obligation completes immediately. Otherwise, a false `maintain` condition
violates it immediately. If neither resolves it, the end of round `k + N`
reports expiry. Completion takes precedence over maintenance in the same round.

Persistent antecedents can create overlapping obligations. Each has its own
start and deadline. One consequent can satisfy several obligations in its
evaluation round. Obligations are scoped by pattern and start round. They do
not bind a dynamic entity or action identifier from the antecedent to the
consequent. Within a round, observations are evaluated together. Same-round
completion does not assert timestamp ordering between event leaves. For local
ordered detection, Composer `event_sequence` evaluates event timestamps.

Bounds use round indices; their granularity is
`cep.round.seconds`.

Every resolution is written to `events:temporal` with `status`, `reason`,
`started_round`, `deadline_round`, `resolved_round` and trigger context.
`emit_on` selects `satisfied` or `violated` for coordination emission and
defaults to `violated`. The selected outcome also appears as `operator_result`
in the coordination envelope. Action-producing patterns still need a policy
mapping and pass through normal Coordinator admission.

## Recovery and profile changes

Operator snapshots, pending obligations and stream cursors are committed in
the same transaction as the Round Table and emitted records. Restarts resume
pending obligations without replaying committed outcomes. Watermark progress
determines expiry. An idle required domain therefore stalls temporal decisions
along with other CEP patterns.

Profile activation cancels outstanding obligations, records
`status: cancelled` and `reason: profile_changed` in `events:temporal`, and
starts a new expression history at the activation round. This also applies to
operator patterns common to both profiles. Cancellation emits an audit record.
It does not dispatch the satisfied or violated action.

Direct Python callers pass one `OperatorRuntime` instance through the
`operators` argument of successive `close_stable_rounds` calls. Configure its
`state_reader` with `get_flag_context` when expressions reference flags. The
normal `python -m runtime cep` entry point creates and checkpoints this context.

Operator patterns run on stable rounds. The administrative force-processing
exception is limited to the existing pattern families.

## Runnable catalog example

The optional [temporal operator package](../specifications/events/service_temporal_operators.json)
adds logical guards, bounded recovery obligations, an until condition, stable
health windows and current/previous flag checks to the HTTP example.

```bash
python -m runtime validate --config config/service-resilience-operators.json
```

The [Compose override](../deployment/examples/local/operators.compose.json)
selects this configuration for all seven runtime processes. From the repository
root, start it as a separate Compose project with its own Redis volume.

```bash
docker compose -p syntony-operators \
  -f deployment/examples/local/compose.json \
  -f deployment/examples/local/operators.compose.json up --build -d --wait
python deployment/examples/local/demo.py
docker compose -p syntony-operators \
  -f deployment/examples/local/compose.json \
  -f deployment/examples/local/operators.compose.json exec redis redis-cli XRANGE events:temporal - +
docker compose -p syntony-operators \
  -f deployment/examples/local/compose.json \
  -f deployment/examples/local/operators.compose.json down
```

Stop another local example using the gateway port before starting this one.
The application demo checks worker recovery. Operator results appear after their
evaluation rounds become stable.

This instance adds a named guard for restart actions, with a three-second lease
and explicit release rules. Its local recovery obligation requests the real
verification handler on expiry, subject to the declared recovery-needed and
not-running flags. A three-second deduplication window limits repeat dispatch. The default instance selects its original packages.
See the [catalog guide](extending-the-catalog.md) for selecting a configuration
consistently across all runtime processes.

Implementation entry points are `runtime/composer/rules.py`,
`runtime/composer/composer.py`, `runtime/cep/patterns.py`, `runtime/cep/expressions.py` and
`runtime/internal_state/control.py`. See [algorithm alignment](algorithm-alignment.md)
for behavioral coverage and [event contracts](event-contracts.md) for envelopes.
