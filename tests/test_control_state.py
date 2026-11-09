"""Exercise atomic plan admission and execution transitions against an isolated Redis server."""

import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import redis

from runtime.configuration import load_syntony_config, validate
from runtime.executor.executor import process_executor_command
from runtime.internal_state.control import (
    EXCLUSIVE_KEY,
    PLANS_KEY,
    admit_plan,
    apply_outcome,
    get_flag,
    write_flags,
)


class ControlStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server = os.getenv("REDIS_SERVER") or shutil.which("redis-server")
        if not server:
            raise unittest.SkipTest(
                "Install redis-server or set REDIS_SERVER for transaction tests"
            )
        cls.directory = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.server = subprocess.Popen(
            [
                server,
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                cls.directory.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
        for _ in range(100):
            try:
                if cls.client.ping():
                    return
            except redis.ConnectionError:
                time.sleep(0.02)
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.directory.cleanup()
        raise RuntimeError("The isolated Redis server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.directory.cleanup()

    def setUp(self):
        self.client.flushdb()
        self.config = load_syntony_config()
        self.policy = "restart_confirmed_local_failure"
        self.constraint = self.config["coordinator"]["policy_constraints"][self.policy]
        self.config["coordinator"]["dedupe_rules"] = []
        self.domain = "application_service"
        self.handler = self.config["executor"]["actions"]["RESTART_LOCAL_SERVICE"]

    def plan(self, ticket="first", policy=None, count=1):
        policy = policy or self.policy
        event = dict(
            policy=policy,
            domain=self.domain,
            coord_type="E_sync",
            timestamp=time.time(),
            coordination_event_id=ticket,
        )
        actions = [
            dict(
                type="RESTART_LOCAL_SERVICE",
                domain=self.domain,
                action_id=f"{ticket}:{n}",
                ticket_id=ticket,
                policy=policy,
            )
            for n in range(count)
        ]
        return event, actions

    def flag(self, name):
        return get_flag(self.client, self.domain, name, self.config)

    def outcome(self, action, status="ok", handler=None, **extra):
        apply_outcome(
            self.client,
            action,
            dict(status=status, success=status == "ok", **extra),
            handler or self.handler,
            self.config,
        )

    def test_competing_plans_and_idempotent_redelivery(self):
        candidates = [self.plan(str(n)) for n in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda pair: admit_plan(self.client, *pair, self.config), candidates)
            )
        self.assertEqual(sum(admitted for admitted, _ in results), 1)
        self.assertEqual(self.client.xlen("executor:commands"), 1)
        winner = next(pair for pair, (admitted, _) in zip(candidates, results) if admitted)
        self.assertEqual(
            admit_plan(self.client, *winner, self.config), (True, "already_dispatched")
        )
        self.assertEqual(self.client.xlen("executor:commands"), 1)
        self.assertTrue(self.flag("service_reconfiguration_running"))

    def test_forced_event_cannot_bypass_state_guard(self):
        write_flags(self.client, {self.domain: {"service_status_unknown": True}})
        event, actions = self.plan()
        event["force_process"] = True
        self.assertEqual(
            admit_plan(self.client, event, actions, self.config), (False, "state_guard")
        )
        self.assertEqual(self.client.xlen("executor:commands"), 0)
        self.assertEqual(self.client.hlen(PLANS_KEY), 0)

    def test_exclusivity_lasts_until_all_actions_finish(self):
        self.constraint["required_flags"] = {}
        event, actions = self.plan(count=2)
        self.assertTrue(admit_plan(self.client, event, actions, self.config)[0])
        self.outcome(actions[0])
        self.assertEqual(
            admit_plan(self.client, *self.plan("second"), self.config), (False, "exclusivity")
        )
        self.outcome(actions[1])
        self.assertIsNone(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))
        self.assertTrue(admit_plan(self.client, *self.plan("second"), self.config)[0])

    def test_domain_block_allows_other_domains_to_dispatch(self):
        from runtime.coordinator.internal_state import register_action_block, release_action_block

        self.config["coordinator"]["policy_constraints"] = {}
        blocked, independent = "application_service", "external_dependency"
        guard = dict(
            domain=blocked,
            block_domain=blocked,
            policy="local_guard",
            block_id="local_guard",
            block_scope="domain",
            block_ttl_seconds=30,
            blocked_action_types=["RESTART_LOCAL_SERVICE"],
            timestamp=time.time(),
        )
        register_action_block(guard, self.client)
        event, actions = self.plan("blocked-domain")
        self.assertEqual(
            admit_plan(self.client, event, actions, self.config), (False, "policy_constraint")
        )

        other_event, other_actions = self.plan("independent-domain")
        other_event["domain"] = independent
        other_actions[0]["domain"] = independent
        self.assertTrue(admit_plan(self.client, other_event, other_actions, self.config)[0])

        release = dict(guard, coordination_event_id="release", timestamp=time.time())
        self.assertTrue(release_action_block(release, self.client))
        self.assertTrue(admit_plan(self.client, event, actions, self.config)[0])
        commands = [json.loads(row["data"]) for _, row in self.client.xrange("executor:commands")]
        self.assertEqual([action["domain"] for action in commands], [independent, blocked])

    def test_deadline_and_action_block_reject_entire_plan(self):
        event, actions = self.plan(count=2)
        event["timestamp"] -= 11
        self.assertFalse(admit_plan(self.client, event, actions, self.config)[0])
        self.assertEqual(self.client.xlen("executor:commands"), 0)
        from runtime.coordinator.internal_state import register_action_block

        event["timestamp"] = time.time()
        register_action_block(
            dict(event, blocked_action_types=["RESTART_LOCAL_SERVICE"], block_ttl_seconds=5),
            self.client,
        )
        self.assertFalse(admit_plan(self.client, event, actions, self.config)[0])
        self.assertFalse(self.flag("service_reconfiguration_running"))

    def test_failure_and_timeout_have_distinct_effects(self):
        event, actions = self.plan()
        admit_plan(self.client, event, actions, self.config)
        self.outcome(actions[0], "error")
        self.assertFalse(self.flag("service_reconfiguration_running"))
        self.assertTrue(self.flag("service_recovery_needed"))
        self.assertIsNone(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))
        self.client.flushdb()
        admit_plan(self.client, event, actions, self.config)
        self.outcome(actions[0], "timeout", timed_out=True)
        self.assertTrue(self.flag("service_reconfiguration_running"))
        self.assertTrue(self.flag("service_status_unknown"))
        self.assertEqual(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"), "first")

    def test_later_success_in_same_plan_cannot_clear_uncertain_guards(self):
        event, actions = self.plan(count=2)
        admit_plan(self.client, event, actions, self.config)
        self.outcome(actions[0], "timeout")
        self.outcome(actions[1], "ok")
        self.assertTrue(self.flag("service_status_unknown"))
        self.assertTrue(self.flag("service_reconfiguration_running"))
        self.assertEqual(json.loads(self.client.hget(PLANS_KEY, "first"))["state"], "uncertain")
        self.assertEqual(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"), "first")

    def test_verified_recovery_and_idempotent_outcome(self):
        event, actions = self.plan()
        admit_plan(self.client, event, actions, self.config)
        self.outcome(actions[0], "execution_uncertain")
        event, recovery = self.plan("recovery", "verify_uncertain_local_recovery")
        recovery[0]["type"] = "VERIFY_LOCAL_SERVICE"
        self.assertTrue(admit_plan(self.client, event, recovery, self.config)[0])
        handler = self.config["executor"]["actions"]["VERIFY_LOCAL_SERVICE"]
        with self.assertRaisesRegex(ValueError, "verified"):
            self.outcome(recovery[0], handler=handler, stdout='{"verified":false}')
        self.outcome(recovery[0], handler=handler, stdout='{"verified":true}')
        self.assertFalse(self.flag("service_status_unknown"))
        self.assertIsNone(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))
        self.assertEqual(json.loads(self.client.hget(PLANS_KEY, "first"))["state"], "resolved")
        self.outcome(actions[0], "execution_uncertain")
        self.assertFalse(self.flag("service_status_unknown"))

    def test_real_executor_timeout_and_replay(self):
        event, actions = self.plan()
        admit_plan(self.client, event, actions, self.config)
        handler = dict(
            self.handler,
            argv=[sys.executable, "-c", "import time;time.sleep(5)"],
            timeout_seconds=0.05,
        )
        result = process_executor_command(
            self.client, actions[0], {"RESTART_LOCAL_SERVICE": handler}
        )
        self.assertEqual(result["feedback_type"], "action_timeout")
        self.assertTrue(self.flag("service_status_unknown"))
        result = process_executor_command(
            self.client, actions[0], {"RESTART_LOCAL_SERVICE": handler}
        )
        self.assertEqual(result["result"]["status"], "timeout")
        self.assertTrue(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))

    def test_zero_exit_without_verification_cannot_release_plan(self):
        event, actions = self.plan()
        admit_plan(self.client, event, actions, self.config)
        self.outcome(actions[0], "timeout")
        event, actions = self.plan("verification", "verify_uncertain_local_recovery")
        actions[0]["type"] = "VERIFY_LOCAL_SERVICE"
        admit_plan(self.client, event, actions, self.config)
        handler = dict(
            self.config["executor"]["actions"]["VERIFY_LOCAL_SERVICE"],
            argv=[sys.executable, "-c", 'print("{}")'],
        )
        result = process_executor_command(
            self.client, actions[0], {"VERIFY_LOCAL_SERVICE": handler}
        )
        self.assertEqual(result["result"]["status"], "unverified_recovery")
        self.assertTrue(self.flag("service_status_unknown"))
        self.assertTrue(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))

    def test_complete_pipeline_recovers_real_http_worker(self):
        import test_application_and_grid as application_tests

        app = application_tests.ApplicationTests()
        app.setUp()
        processes = []
        root = Path(__file__).resolve().parents[1]
        try:
            config = copy.deepcopy(self.config)
            for domain in config["domains"].values():
                for source in domain["monitor"]["sources"]:
                    source["url"] = app.url + "/metrics?window=2"
            for handler in config["executor"]["actions"].values():
                handler["argv"][0] = sys.executable
                handler["argv"][handler["argv"].index("--gateway") + 1] = app.url
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "instance.json"
                path.write_text(json.dumps(config))
                env = dict(
                    os.environ,
                    SYNTONY_CONFIG_PATH=str(path),
                    REDIS_URL=f"redis://127.0.0.1:{self.client.connection_pool.connection_kwargs['port']}/0",
                    POLL_INTERVAL="0.05",
                )
                with (Path(directory) / "pipeline.log").open("w+") as output:
                    for role in ("cep", "coordinator", "executor", "composer", "monitor"):
                        domains = config["domains"] if role in {"monitor", "composer"} else [None]
                        for domain in domains:
                            argv = [sys.executable, "-m", "runtime", role]
                            if domain:
                                argv += ["--domain", domain]
                            processes.append(
                                subprocess.Popen(
                                    argv, cwd=root, env=env, stdout=output, stderr=output
                                )
                            )
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline and self.client.xlen("clock:tick") < 2:
                        app.supervisor.execute()
                        time.sleep(0.05)
                    self.assertGreater(self.client.xlen("clock:tick"), 0)
                    generation = app.supervisor.generation
                    app.supervisor.stop_worker()
                    deadline = time.monotonic() + 18
                    recovered = False
                    while time.monotonic() < deadline:
                        app.supervisor.execute()
                        if app.supervisor.generation > generation:
                            feedback = [
                                json.loads(fields["data"])
                                for _, fields in self.client.xrange("events:obs")
                            ]
                            recovered = any(
                                event.get("event_type") == "E_fed"
                                and event.get("payload", {}).get("action")
                                == "RESTART_LOCAL_SERVICE"
                                and event.get("feedback_type") == "action_done"
                                for event in feedback
                            )
                            if recovered:
                                break
                        time.sleep(0.05)
                    output.flush()
                    output.seek(0)
                    self.assertTrue(recovered, output.read()[-12000:])
                    self.assertFalse(self.flag("service_reconfiguration_running"))
                    self.assertFalse(self.flag("service_status_unknown"))
                    self.assertIsNone(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"))
                    self.assertTrue(all(process.poll() is None for process in processes))
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=8)
            app.tearDown()

    def guard(self, name="host_pressure", timestamp=10):
        return dict(
            domain="operating_system",
            block_domain="operating_system",
            block_id=name,
            policy="guard_" + name,
            timestamp=timestamp,
            blocked_action_types=["RESTART_LOCAL_SERVICE"],
            block_ttl_seconds=60,
            block_scope="global",
        )

    def release(self, timestamp=11, identity="release-1"):
        return dict(
            domain="operating_system",
            block_domain="operating_system",
            block_id="host_pressure",
            policy="release_pressure",
            coord_type="E_unblock",
            coordination_event_id=identity,
            timestamp=timestamp,
        )

    def test_release_is_scoped_and_preserves_other_guards_and_plan_exclusivity(self):
        from runtime.coordinator.internal_state import (
            get_active_action_blocks,
            register_action_block,
            release_action_block,
        )

        event, actions = self.plan()
        admit_plan(self.client, event, actions, self.config)
        register_action_block(self.guard(), self.client)
        register_action_block(self.guard("maintenance"), self.client)
        self.assertTrue(release_action_block(self.release(), self.client))
        records = get_active_action_blocks(self.client, event, "RESTART_LOCAL_SERVICE")
        self.assertEqual([record["block_id"] for record in records], ["maintenance"])
        self.assertEqual(self.client.hget(EXCLUSIVE_KEY, "service_reconfiguration"), "first")
        self.assertTrue(self.flag("service_reconfiguration_running"))

    def test_release_replay_and_delayed_events_preserve_newer_guard_state(self):
        from runtime.coordinator.internal_state import (
            ACTION_BLOCK_KEY,
            register_action_block,
            release_action_block,
        )

        register_action_block(self.guard(), self.client)
        self.assertTrue(release_action_block(self.release(), self.client))
        self.assertIsNone(register_action_block(self.guard(timestamp=9), self.client))
        register_action_block(self.guard(timestamp=12), self.client)
        self.assertFalse(release_action_block(self.release(), self.client))
        self.assertFalse(
            release_action_block(self.release(timestamp=11, identity="delayed"), self.client)
        )
        self.assertEqual(self.client.hlen(ACTION_BLOCK_KEY), 1)
        self.assertTrue(
            release_action_block(self.release(timestamp=13, identity="new"), self.client)
        )
        self.assertEqual(self.client.hlen(ACTION_BLOCK_KEY), 0)

    def test_meta_reconfiguration_switches_at_boundaries_and_survives_restart(self):
        from contextlib import ExitStack

        from runtime.policy_profiles import ACTIVE_PROFILE, PROFILE_AUDIT, PROFILE_REQUESTS

        root = Path(__file__).resolve().parents[1]
        config = load_syntony_config(root / "config/service-resilience-meta.json")
        app, dependency = "application_service", "external_dependency"

        def publish(stream, **fields):
            self.client.xadd(stream, {"data": json.dumps(fields)})

        def observations(timestamp, event_type, domain=dependency):
            publish("events:obs", domain=domain, event_time=timestamp, event_type=event_type)

        def advance(value):
            for domain in (app, dependency):
                publish("clock:watermark", domain=domain, watermark=value)

        def rows(stream):
            return [json.loads(row["data"]) for _, row in self.client.xrange(stream)]

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.02)
            self.fail("Timed out waiting for profile transition")

        def active():
            return json.loads(self.client.get(ACTIVE_PROFILE) or "{}")

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(config))
            port = self.client.connection_pool.connection_kwargs["port"]
            env = dict(
                os.environ,
                SYNTONY_CONFIG_PATH=str(path),
                REDIS_URL=f"redis://127.0.0.1:{port}/0",
                POLL_INTERVAL="0.02",
            )
            logs = stack.enter_context((Path(directory) / "pipeline.log").open("w+"))

            def start(role):
                process = subprocess.Popen(
                    [sys.executable, "-m", "runtime", role],
                    cwd=root,
                    env=env,
                    stdout=logs,
                    stderr=logs,
                )

                def stop():
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

                stack.callback(stop)
                return process

            cep = start("cep")
            start("coordinator")
            start("executor")
            observations(100.2, "application_degraded", app)
            observations(100.3, "dependency_unavailable")
            advance(101)
            wait_for(lambda: self.client.xlen(PROFILE_REQUESTS) == 1)
            initial = active()
            self.assertEqual(initial["policy_profile"], "normal")
            self.assertEqual(rows(PROFILE_AUDIT), [])

            observations(101.3, "dependency_unavailable")
            advance(102)
            wait_for(lambda: active().get("policy_version") == 2)
            self.assertEqual(active()["policy_profile"], "fallback")
            self.assertNotEqual(active()["composition_hash"], initial["composition_hash"])
            self.assertEqual(active()["activation_round"], 1)
            self.assertNotIn(
                "enable_fallback_for_dependency", [row["policy"] for row in rows("events:coord")]
            )
            event, actions = self.plan(ticket="old-profile")
            event.update(initial)
            self.assertEqual(
                admit_plan(self.client, event, actions, config), (False, "stale_policy_profile")
            )
            wait_for(
                lambda: any(
                    row.get("feedback_type") == "policy_activated" for row in rows("events:obs")
                )
            )

            cep.kill()
            cep.wait(timeout=5)
            start("cep")
            old_request = rows(PROFILE_REQUESTS)[0]
            publish(PROFILE_REQUESTS, **old_request)
            observations(102.2, "dependency_available")
            advance(103)
            wait_for(lambda: self.client.xlen(PROFILE_REQUESTS) == 3)
            self.assertEqual(active()["policy_version"], 2)
            advance(104)
            wait_for(lambda: active().get("policy_version") == 3)
            self.assertEqual(active()["policy_profile"], "normal")
            self.assertEqual(active()["composition_hash"], initial["composition_hash"])
            self.assertEqual([row["activation_round"] for row in rows(PROFILE_AUDIT)], [1, 3])
            self.assertEqual([row["tick"] for row in rows("clock:tick")], [0, 1, 2, 3])

    def test_executor_feedback_reenters_cep_after_stable_round(self):
        from runtime.cep.cep import ROUND_CHECKPOINT_KEY

        config = self.config
        patterns = config["cep"]["patterns"]
        mappings = config["coordinator"]["policy_mappings"]
        subtypes = {
            "action_done": "pass",
            "action_fail": "raise SystemExit(1)",
            "action_timeout": "import time; time.sleep(5)",
        }
        for subtype, code in subtypes.items():
            policy = f"follow_up_{subtype}"
            patterns.append(
                dict(
                    name=policy,
                    type="passthrough_event",
                    event_type="E_fed",
                    domain=self.domain,
                    coord_type="E_trg",
                    where={"feedback_type": subtype},
                )
            )
            mappings[policy] = copy.deepcopy(mappings[self.policy])
            action = dict(type="PROBE", domain=self.domain, action_id=subtype, tick_end=1)
            handler = dict(mode="command", argv=[sys.executable, "-c", code], timeout_seconds=0.5)
            result = process_executor_command(self.client, action, {"PROBE": handler})
            self.assertEqual(result["feedback_type"], subtype)
            self.assertEqual(result["feedback_payload"]["action_id"], subtype)

        feedback = [json.loads(row["data"]) for _, row in self.client.xrange("events:obs")]
        boundary = int(max(event["event_time"] for event in feedback)) + 1

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(config))
            port = self.client.connection_pool.connection_kwargs["port"]
            env = dict(
                os.environ, SYNTONY_CONFIG_PATH=str(path), REDIS_URL=f"redis://127.0.0.1:{port}/0"
            )
            with (Path(directory) / "cep.log").open("w+") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "runtime", "cep"],
                    env=env,
                    stdout=log,
                    stderr=log,
                )

                def advance(domain):
                    return self.client.xadd(
                        "clock:watermark",
                        {"data": json.dumps(dict(domain=domain, watermark=boundary))},
                    )

                def wait_for_watermark(record_id):
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and process.poll() is None:
                        checkpoint = json.loads(self.client.get(ROUND_CHECKPOINT_KEY) or "{}")
                        if checkpoint.get("stream_cursors", {}).get("clock:watermark") == record_id:
                            return
                        time.sleep(0.02)
                    log.seek(0)
                    self.fail(log.read()[-4000:])

                try:
                    wait_for_watermark(advance(self.domain))
                    self.assertEqual(self.client.xlen("clock:tick"), 0)
                    self.assertEqual(self.client.xlen("events:coord"), 0)
                    wait_for_watermark(advance("external_dependency"))
                finally:
                    process.terminate()
                    process.wait(timeout=5)

        coordination = [json.loads(row["data"]) for _, row in self.client.xrange("events:coord")]
        self.assertCountEqual(
            [event["policy"] for event in coordination],
            [f"follow_up_{subtype}" for subtype in subtypes],
        )
        self.assertTrue(all(event["base_event"] == "E_fed" for event in coordination))
        self.assertTrue(all(event["tick_end"] <= boundary for event in coordination))

    def test_cep_restart_restores_round_table_without_republishing_committed_rounds(self):
        from runtime.cep.cep import ROUND_CHECKPOINT_KEY

        config = self.config
        config["cep"]["tick"]["seconds"] = 1
        config["cep"]["relative_tick_origin"] = True
        policy = "enable_fallback_for_dependency"
        pattern = next(p for p in config["cep"]["patterns"] if p["name"] == policy)
        pattern["within_ticks"] = 1
        app, dependency = "application_service", "external_dependency"

        def observation(domain, name, timestamp):
            self.client.xadd(
                "events:obs",
                {"data": json.dumps(dict(domain=domain, event_type=name, event_time=timestamp))},
            )

        def advance_watermarks(value):
            for domain in (app, dependency):
                self.client.xadd(
                    "clock:watermark", {"data": json.dumps(dict(domain=domain, watermark=value))}
                )

        def run_until_checkpoint(round_index):
            process = subprocess.Popen(
                [sys.executable, "-m", "runtime", "cep"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and process.poll() is None:
                    checkpoint = json.loads(self.client.get(ROUND_CHECKPOINT_KEY) or "{}")
                    if checkpoint.get("last_closed_round") == round_index:
                        return checkpoint
                    time.sleep(0.02)
                self.fail(f"CEP did not checkpoint round {round_index}")
            finally:
                process.kill()
                process.communicate(timeout=5)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(config))
            port = self.client.connection_pool.connection_kwargs["port"]
            env = dict(
                os.environ,
                SYNTONY_CONFIG_PATH=str(path),
                REDIS_URL=f"redis://127.0.0.1:{port}/0",
            )
            observation(app, "application_degraded", 100.2)
            observation(dependency, "dependency_unavailable", 100.3)
            observation(app, "application_degraded", 101.2)
            advance_watermarks(101)
            first = run_until_checkpoint(0)
            self.assertIn("1", first["round_table"])
            self.assertEqual(self.client.xlen("events:coord"), 1)

            observation(dependency, "dependency_unavailable", 101.3)
            advance_watermarks(102)
            run_until_checkpoint(1)

        rounds = [json.loads(row["data"]) for _, row in self.client.xrange("clock:tick")]
        events = [json.loads(row["data"]) for _, row in self.client.xrange("events:coord")]
        self.assertEqual([row["tick"] for row in rounds], [0, 1])
        self.assertEqual(
            [(row["policy"], row["tick"]) for row in events], [(policy, 0), (policy, 1)]
        )

    def test_cep_and_coordinator_apply_block_and_explicit_release(self):
        from runtime.cep import close_stable_rounds, compile_pattern
        from runtime.coordinator.internal_state import ACTION_BLOCK_KEY, get_state

        root = Path(__file__).resolve().parents[1]
        config = load_syntony_config(root / "config/service-resilience-host-guard.json")
        patterns = [
            compile_pattern(p)
            for p in config["cep"]["patterns"]
            if p["coord_type"] in {"E_block", "E_unblock"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps(config))
            port = self.client.connection_pool.connection_kwargs["port"]
            env = dict(
                os.environ,
                SYNTONY_CONFIG_PATH=str(path),
                REDIS_URL=f"redis://127.0.0.1:{port}/0",
                POLL_INTERVAL="0.05",
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "runtime", "coordinator"],
                cwd=root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                high = dict(
                    domain="operating_system", event_type="host_load_high", event_time=0.1, tick=0
                )
                close_stable_rounds(self.client, patterns, {0: [high]}, 1, 8, 1, -1)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not self.client.hlen(ACTION_BLOCK_KEY):
                    time.sleep(0.02)
                self.assertEqual(self.client.hlen(ACTION_BLOCK_KEY), 1)
                normal = dict(
                    domain="operating_system", event_type="host_load_normal", event_time=1.1, tick=1
                )
                close_stable_rounds(self.client, patterns, {1: [normal]}, 1, 8, 2, 0)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if any(
                        record["status"] == "released" for record in get_state(self.client).values()
                    ):
                        break
                    time.sleep(0.02)
                self.assertTrue(
                    any(
                        record["status"] == "released" for record in get_state(self.client).values()
                    )
                )
                self.assertEqual(self.client.hlen(ACTION_BLOCK_KEY), 0)
                rows = [
                    json.loads(fields["data"]) for _, fields in self.client.xrange("events:coord")
                ]
                self.assertEqual([event["coord_type"] for event in rows], ["E_block", "E_unblock"])
                self.assertTrue(all(event["block_id"] == "host_pressure" for event in rows))
                self.assertEqual(self.client.xlen("executor:commands"), 0)
                self.assertIsNone(process.poll())
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_release_configuration_requires_a_declared_guard(self):
        root = Path(__file__).resolve().parents[1]
        config = load_syntony_config(root / "config/service-resilience-host-guard.json")
        for mutation in [
            lambda p: p.pop("block_id"),
            lambda p: p.update(block_id="unknown_guard"),
            lambda p: p.update(block_domain="unknown_domain"),
            lambda p: p.update(blocked_action_types=["RESTART_LOCAL_SERVICE"]),
        ]:
            changed = copy.deepcopy(config)
            release = next(p for p in changed["cep"]["patterns"] if p["coord_type"] == "E_unblock")
            mutation(release)
            with self.assertRaises(ValueError):
                validate(changed)

    def test_schema_rejects_typos_and_unsafe_transitions(self):
        for mutation in [
            lambda cfg: cfg["state"]["flags"][self.domain].update(service_status_unknown="false"),
            lambda cfg: cfg["coordinator"]["policy_constraints"][self.policy].update(
                required_flags={self.domain: {"unknown_flag": False}}
            ),
            lambda cfg: cfg["executor"]["actions"]["RESTART_LOCAL_SERVICE"].update(
                on_timeout={"release_exclusivity": ["service_reconfiguration"]}
            ),
            lambda cfg: cfg["coordinator"]["policy_constraints"][self.policy].update(
                deadline_seconds=-1
            ),
        ]:
            config = copy.deepcopy(self.config)
            mutation(config)
            with self.assertRaises(ValueError):
                validate(config)
