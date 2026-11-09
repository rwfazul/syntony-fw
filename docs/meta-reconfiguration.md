# Meta-reconfiguration

Syntony switches between profiles declared in the Specification Catalog while the CEP is running. A profile selects active catalog patterns and their Coordinator mappings. Patterns outside all profiles remain active across transitions. Declare the lists of pattern names under `cep.policy_profiles` and choose the startup profile with `cep.initial_policy_profile`. Put each transition pattern in its source profile so it stops requesting the same transition after activation.

## Activation and recovery

1. An active pattern emits a coordination event that maps to an `activate_profile` action with a `target_profile`.
2. The Executor submits the action ID, expected policy version and deadline to the CEP.
3. Before evaluating the next stable round, the CEP validates the request and switches profiles. Observations before the cutover round are removed from the Round Table and excluded from subsequent evaluations.
4. The CEP commits the profile, incremented version, composition hash, cutover round and acknowledgment with the Round Table checkpoint. The Executor emits `E_fed` with subtype `policy_activated` after confirmation.

The active profile is visible in `syntony:policy:active`. Coordination events carry `policy_profile`, `policy_version` and `composition_hash`. The Coordinator rejects events from previous versions at admission. Plans already admitted keep their action and state contracts through completion. Existing guard leases follow their release and expiry rules.

The CEP records each activation ID once in `syntony:policy:activation_acks` and reports accepted or rejected requests in `control:policy_audit`. Its checkpoint retains requests waiting for a stable round. Expired or stale requests cannot activate a profile. An Executor timeout means that confirmation was unavailable by its deadline. Inspect the acknowledgment to resolve the outcome.

Profile switches preserve watermark parameters, round duration, domain membership and event-time origin. Editing catalog definitions or temporal settings changes the startup configuration and requires separate checkpoint and stream migration.

## Run the example

`config/service-resilience-meta.json` selects a bidirectional example. An unavailable dependency requests `fallback`. A healthy dependency requests `normal` again. Each transition is driven by an observation and activates at a stable-round boundary.

```bash
docker compose -p syntony-meta -f deployment/examples/local/compose.json \
  -f deployment/examples/local/meta.compose.json up --build -d --wait

curl -fsS -X POST 'http://127.0.0.1:18081/inject/dependency?mode=down'
docker compose -p syntony-meta -f deployment/examples/local/compose.json \
  -f deployment/examples/local/meta.compose.json exec -T redis redis-cli XRANGE control:policy_audit - +

curl -fsS -X POST 'http://127.0.0.1:18081/inject/dependency?mode=healthy'
```

Wait until the audit stream records the first activation before restoring the dependency. Use the same audit command to check the return to `normal`. The separate Compose project gives the example its own Redis volume. Stop any other example using port 18081 first.
