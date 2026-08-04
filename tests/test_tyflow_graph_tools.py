import json
import os
import tempfile
import unittest
from unittest.mock import patch

from maxmcp.coerce import DictList, IntList, StrList  # noqa: F401 — annotation check
from maxmcp.tools._tyflow_core import (
    graph_payload,
    parse_ledger,
    read_graph,
    structural_hash,
)
from maxmcp.tools.tyflow_census import tyflow_event_census
from maxmcp.tools.tyflow_graph import (
    connect_tyflow_operator,
    disconnect_tyflow_operator,
    get_tyflow_graph,
    set_tyflow_wiring_ledger,
)
from maxmcp.tools.tyflow_manifest import harvest_tyflow_manifest, list_tyflow_operators
from maxmcp.tools.tyflow_patch import tyflow_apply_patch


def graph_fixture(ledger: str = "") -> str:
    return "\n".join(
        [
            "FLOW|Flow001|tyFlow|42|200500",
            f"LEDGER|{ledger}",
            "EV|EvA|100,200|320|true",
            "OP|EvA|Birth|true|2",
            "PR|EvA|Birth|birthMode|0",
            "PR|EvA|Birth|birthTotal|100",
            "OP|EvA|Send Out|true|0",
            "EV|EvB|500,200|320|true",
        ]
    )


def _fixture_hash() -> str:
    with patch(
        "maxmcp.tools.tyflow.client.send_command",
        return_value={"result": graph_fixture()},
    ):
        graph = read_graph("Flow001")
    return structural_hash(graph)


class LedgerCoreTests(unittest.TestCase):
    def test_parse_ledger_tolerates_garbage(self) -> None:
        for garbage in ("", None, "not json", "[1,2]", '{"edges": "nope"}', 42):
            ledger = parse_ledger(garbage)  # type: ignore[arg-type]
            self.assertEqual(ledger["edges"], [])
            self.assertEqual(ledger["ops"], {})
            self.assertEqual(ledger["hash"], "")

    def test_parse_ledger_normalizes_edges(self) -> None:
        raw = json.dumps(
            {
                "hash": "abc",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvB"}, "junk"],
                "ops": {"EvA/Birth": "Birth"},
            }
        )
        ledger = parse_ledger(raw)
        self.assertEqual(len(ledger["edges"]), 1)
        self.assertEqual(ledger["edges"][0]["to_event"], "EvB")
        self.assertEqual(ledger["ops"]["EvA/Birth"], "Birth")

    def test_structural_hash_stable_and_sensitive(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture()},
        ):
            graph_one = read_graph("Flow001")
            graph_two = read_graph("Flow001")
        self.assertEqual(structural_hash(graph_one), structural_hash(graph_two))
        graph_two["particleCount"] = 9999  # volatile — excluded
        self.assertEqual(structural_hash(graph_one), structural_hash(graph_two))
        graph_two["events"][0]["operators"][0]["properties"][0]["value"] = "1"
        self.assertNotEqual(structural_hash(graph_one), structural_hash(graph_two))


class GraphReadTests(unittest.TestCase):
    def test_get_graph_parses_structure(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture()},
        ):
            payload = json.loads(get_tyflow_graph("Flow001", include_properties=True))
        self.assertEqual(payload["eventCount"], 2)
        self.assertEqual(payload["events"][0]["position"], [100, 200])
        self.assertEqual(payload["events"][0]["operators"][0]["name"], "Birth")
        self.assertEqual(
            payload["events"][0]["operators"][0]["properties"][1]["value"], "100"
        )
        self.assertEqual(payload["ledger_status"], "absent")

    def test_properties_stripped_by_default_but_hashed(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture()},
        ):
            slim = json.loads(get_tyflow_graph("Flow001"))
            full = json.loads(get_tyflow_graph("Flow001", include_properties=True))
        self.assertNotIn("properties", slim["events"][0]["operators"][0])
        self.assertEqual(slim["graph_hash"], full["graph_hash"])

    def test_ledger_status_fresh_and_stale(self) -> None:
        fixture_hash = _fixture_hash()
        fresh_ledger = json.dumps(
            {"v": 1, "hash": fixture_hash, "edges": [], "ops": {}}
        ).replace("|", "")
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture(ledger=fresh_ledger)},
        ):
            payload = json.loads(get_tyflow_graph("Flow001"))
        self.assertEqual(payload["ledger_status"], "fresh")

        stale_ledger = json.dumps({"v": 1, "hash": "0" * 40, "edges": [], "ops": {}})
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture(ledger=stale_ledger)},
        ):
            payload = json.loads(get_tyflow_graph("Flow001"))
        self.assertEqual(payload["ledger_status"], "stale")

    def test_error_passthrough(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": "__ERROR__|Object not found: Flow001"},
        ):
            payload = json.loads(get_tyflow_graph("Flow001"))
        self.assertIn("error", payload)


class ConnectLedgerTests(unittest.TestCase):
    def test_connect_records_edge(self) -> None:
        responses = [
            {"result": '{"ok":true}'},
            {"result": graph_fixture()},
            {"result": "OK"},
        ]
        with patch(
            "maxmcp.tools.tyflow.client.send_command", side_effect=responses
        ) as mock_send:
            payload = json.loads(
                connect_tyflow_operator("Flow001", "EvA", "Send Out", "EvB")
            )
        self.assertTrue(payload["connected"])
        self.assertEqual(
            payload["edges"],
            [{"from_event": "EvA", "from_op": "Send_Out", "to_event": "EvB"}],
        )
        ledger_write = mock_send.call_args_list[2].args[0]
        self.assertIn("setAppData", ledger_write)
        self.assertIn("EvB", ledger_write)

    def test_connect_replaces_existing_edge(self) -> None:
        old_ledger = json.dumps(
            {
                "v": 1,
                "hash": "x",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvOld"}],
                "ops": {},
            }
        )
        responses = [
            {"result": '{"ok":true}'},
            {"result": graph_fixture(ledger=old_ledger)},
            {"result": "OK"},
        ]
        with patch("maxmcp.tools.tyflow.client.send_command", side_effect=responses):
            payload = json.loads(
                connect_tyflow_operator("Flow001", "EvA", "Send Out", "EvB")
            )
        self.assertEqual(len(payload["edges"]), 1)
        self.assertEqual(payload["edges"][0]["to_event"], "EvB")

    def test_disconnect_drops_edge(self) -> None:
        old_ledger = json.dumps(
            {
                "v": 1,
                "hash": "x",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvB"}],
                "ops": {},
            }
        )
        responses = [
            {"result": '{"ok":true}'},
            {"result": graph_fixture(ledger=old_ledger)},
            {"result": "OK"},
        ]
        with patch("maxmcp.tools.tyflow.client.send_command", side_effect=responses):
            payload = json.loads(disconnect_tyflow_operator("Flow001", "EvA", "Send Out"))
        self.assertTrue(payload["disconnected"])
        self.assertEqual(payload["edges"], [])

    def test_connect_error_stops_before_ledger(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": '{"error":"Operator not found: Send Out"}'},
        ) as mock_send:
            payload = json.loads(
                connect_tyflow_operator("Flow001", "EvA", "Send Out", "EvB")
            )
        self.assertIn("error", payload)
        self.assertEqual(mock_send.call_count, 1)


class WiringLedgerToolTests(unittest.TestCase):
    def test_replace_mode_and_unknown_event_warning(self) -> None:
        responses = [
            {"result": graph_fixture()},
            {"result": "OK"},
        ]
        with patch("maxmcp.tools.tyflow.client.send_command", side_effect=responses):
            payload = json.loads(
                set_tyflow_wiring_ledger(
                    "Flow001",
                    [
                        {"from_event": "EvA", "from_operator": "Send Out", "to_event": "EvB"},
                        {"from_event": "EvA", "from_op": "Test", "to_event": "Ghost"},
                    ],
                )
            )
        self.assertEqual(payload["edgeCount"], 2)
        self.assertTrue(any("Ghost" in warning for warning in payload["warnings"]))

    def test_rejects_incomplete_edge(self) -> None:
        payload = json.loads(
            set_tyflow_wiring_ledger("Flow001", [{"from_event": "EvA"}])
        )
        self.assertIn("error", payload)


class ApplyPatchTests(unittest.TestCase):
    def test_unknown_op_rejected_before_maxscript(self) -> None:
        with patch("maxmcp.tools.tyflow.client.send_command") as mock_send:
            payload = json.loads(
                tyflow_apply_patch("Flow001", [{"op": "explode"}])
            )
        self.assertIn("unknown op", payload["error"])
        self.assertEqual(mock_send.call_count, 0)

    def test_missing_field_rejected(self) -> None:
        payload = json.loads(
            tyflow_apply_patch("Flow001", [{"op": "add_operator", "event": "EvA"}])
        )
        self.assertIn("missing field 'type'", payload["error"])

    def test_hash_gate_refuses_conflict(self) -> None:
        with patch(
            "maxmcp.tools.tyflow.client.send_command",
            return_value={"result": graph_fixture()},
        ) as mock_send:
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001",
                    [{"op": "add_event", "event": "EvC"}],
                    expected_hash="not-the-hash",
                )
            )
        self.assertIn("hash conflict", payload["error"].lower())
        self.assertEqual(mock_send.call_count, 1)

    def test_script_operator_blocked_without_authorization(self) -> None:
        with patch("maxmcp.tools.tyflow.client.send_command") as mock_send:
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001",
                    [{"op": "add_operator", "event": "EvA", "type": "Script"}],
                )
            )
        self.assertIn("allow_executable", payload["error"])
        self.assertEqual(mock_send.call_count, 0)

    def test_script_operator_blocked_in_safe_mode(self) -> None:
        with patch(
            "maxmcp.tools.tyflow_patch.client.send_command",
            return_value={"result": "true", "meta": {"safeMode": True}},
        ):
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001",
                    [{"op": "add_operator", "event": "EvA", "type": "Script"}],
                    allow_executable=True,
                )
            )
        self.assertIn("safe_mode", payload["error"])

    def test_failed_op_triggers_rollback(self) -> None:
        old_ledger = json.dumps(
            {
                "v": 1,
                "hash": "x",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvB"}],
                "ops": {},
            }
        )
        responses = [
            {"result": graph_fixture(ledger=old_ledger)},  # hash-gate read
            {"result": "OK|zzz_mcp_ckpt_Flow001"},  # checkpoint
            {"result": "OPRES|1|err|Event not found: Missing|"},  # apply
            {"result": "OK"},  # rollback
            {"result": "OK"},  # ledger restore (clone drops appdata)
        ]
        with patch(
            "maxmcp.tools.tyflow.client.send_command", side_effect=responses
        ) as mock_send:
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001", [{"op": "remove_event", "event": "Missing"}]
                )
            )
        self.assertTrue(payload["failed"])
        self.assertTrue(payload["rolled_back"])
        self.assertTrue(payload["ledger_restored"])
        rollback_script = mock_send.call_args_list[3].args[0]
        self.assertIn("zzz_mcp_ckpt_Flow001", rollback_script)
        restore_script = mock_send.call_args_list[4].args[0]
        self.assertIn("setAppData", restore_script)
        self.assertIn("EvB", restore_script)

    def test_success_updates_ledger_with_rename_rewrite(self) -> None:
        old_ledger = json.dumps(
            {
                "v": 1,
                "hash": "x",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvB"}],
                "ops": {"EvA/Send Out": "Send Out"},
            }
        )
        responses = [
            {"result": graph_fixture(ledger=old_ledger)},  # hash-gate read
            {"result": "OPRES|1|ok||"},  # apply (no checkpoint)
            {"result": graph_fixture(ledger=old_ledger)},  # ledger read
            {"result": "OK"},  # ledger write
        ]
        with patch(
            "maxmcp.tools.tyflow.client.send_command", side_effect=responses
        ) as mock_send:
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001",
                    [{"op": "rename_event", "event": "EvA", "new_name": "EvStart"}],
                    checkpoint=False,
                )
            )
        self.assertFalse(payload["failed"])
        self.assertEqual(payload["edges"][0]["from_event"], "EvStart")
        ledger_write = mock_send.call_args_list[3].args[0]
        self.assertIn("EvStart", ledger_write)
        self.assertIn("EvStart/Send_Out", ledger_write.replace("\\/", "/"))

    def test_additive_patch_failure_uses_inverse_rollback(self) -> None:
        old_ledger = json.dumps(
            {
                "v": 1,
                "hash": "x",
                "edges": [{"from_event": "EvA", "from_op": "Send Out", "to_event": "EvB"}],
                "ops": {},
            }
        )
        responses = [
            {"result": graph_fixture(ledger=old_ledger)},  # hash-gate read
            # apply: event added ok, retargeting connect ok, second add fails
            {"result": "OPRES|1|ok||\nOPRES|2|ok||\nOPRES|3|err|boom|"},
            {"result": "OPRES|1|ok||\nOPRES|2|ok||"},  # inverse rollback ops
        ]
        operations = [
            {"op": "add_event", "event": "EvC"},
            {"op": "connect", "from_event": "EvA", "from_operator": "Send Out",
             "to_event": "EvC", "ensure_send_out": False},
            {"op": "add_operator", "event": "EvC", "type": "NopeOp"},
        ]
        with patch(
            "maxmcp.tools.tyflow.client.send_command", side_effect=responses
        ) as mock_send:
            payload = json.loads(tyflow_apply_patch("Flow001", operations))
        self.assertTrue(payload["failed"])
        self.assertTrue(payload["rolled_back"])
        self.assertEqual(payload["rollback_mode"], "inverse")
        # No checkpoint clone was ever created (3 calls: read, apply, inverse).
        self.assertEqual(mock_send.call_count, 3)
        inverse_script = mock_send.call_args_list[2].args[0]
        # Inverse order: reconnect Send Out to its pre-patch target, then remove EvC.
        self.assertIn('"EvB"', inverse_script)
        self.assertIn("remove()", inverse_script)

    def test_destructive_patch_still_uses_clone_checkpoint(self) -> None:
        responses = [
            {"result": graph_fixture()},  # hash-gate read
            {"result": "OK|zzz_mcp_ckpt_Flow001"},  # checkpoint (destructive op)
            {"result": "OPRES|1|ok||"},  # apply
            {"result": "OK"},  # drop checkpoint
            {"result": graph_fixture()},  # ledger read
            {"result": "OK"},  # ledger write
        ]
        with patch(
            "maxmcp.tools.tyflow.client.send_command", side_effect=responses
        ) as mock_send:
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001", [{"op": "remove_operator", "event": "EvA", "operator": "Birth"}]
                )
            )
        self.assertFalse(payload["failed"])
        checkpoint_script = mock_send.call_args_list[1].args[0]
        self.assertIn("cloneNodes", checkpoint_script)

    def test_verification_failure_rolls_back(self) -> None:
        responses = [
            {"result": graph_fixture()},  # hash-gate read
            {"result": "OPRES|1|ok||"},  # apply (additive — no clone)
            {"result": "CNT|0|0\nCNT|50|0"},  # verification counts
            {"result": "OPRES|1|ok||"},  # inverse rollback (remove_event EvC)
        ]
        with patch("maxmcp.tools.tyflow.client.send_command", side_effect=responses):
            payload = json.loads(
                tyflow_apply_patch(
                    "Flow001",
                    [{"op": "add_event", "event": "EvC"}],
                    verify_frames=[0, 50],
                    min_particles=1,
                )
            )
        self.assertTrue(payload["failed"])
        self.assertTrue(payload["rolled_back"])
        self.assertEqual(payload["rollback_mode"], "inverse")
        self.assertFalse(payload["verification"]["passed"])

    def test_list_params_use_coerced_types(self) -> None:
        annotations = tyflow_apply_patch.__annotations__
        self.assertEqual(annotations["operations"], "DictList")
        self.assertEqual(annotations["verify_frames"], "IntList | None")


class ManifestTests(unittest.TestCase):
    def _write_cache(self, tmp: str) -> None:
        cache_dir = os.path.join(tmp, "3dsmax-mcp")
        os.makedirs(cache_dir, exist_ok=True)
        manifest = {
            "tyflowVersion": 200500,
            "operators": [
                {
                    "type": "Birth",
                    "available": True,
                    "executable": False,
                    "propertyCount": 2,
                    "properties": [
                        {"name": "birthMode", "default": "0", "valueClass": "Integer"}
                    ],
                },
                {
                    "type": "Script",
                    "available": True,
                    "executable": True,
                    "propertyCount": 1,
                    "properties": [],
                },
            ],
        }
        with open(
            os.path.join(cache_dir, "tyflow_manifest_200500.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(manifest, handle)

    def test_list_reads_cache_without_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cache(tmp)
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": tmp}),
                patch("maxmcp.tools.tyflow_manifest.client.send_command") as mock_send,
            ):
                payload = json.loads(list_tyflow_operators(query="birthmode"))
        self.assertEqual(mock_send.call_count, 0)
        self.assertEqual(payload["matchCount"], 1)
        self.assertEqual(payload["operators"][0]["type"], "Birth")

    def test_executable_only_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cache(tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                payload = json.loads(list_tyflow_operators(executable_only=True))
        self.assertEqual(payload["matchCount"], 1)
        self.assertTrue(payload["operators"][0]["executable"])

    def test_missing_cache_hints_harvest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                payload = json.loads(list_tyflow_operators())
        self.assertIn("error", payload)
        self.assertIn("harvest_tyflow_manifest", json.dumps(payload["hint"]))

    def test_harvest_returns_cache_without_probing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cache(tmp)
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": tmp}),
                patch(
                    "maxmcp.tools.tyflow_manifest.client.send_command",
                    return_value={"result": "200500"},
                ) as mock_send,
            ):
                payload = json.loads(harvest_tyflow_manifest())
        self.assertTrue(payload["cached"])
        self.assertEqual(mock_send.call_count, 1)  # version probe only


class CensusTests(unittest.TestCase):
    def test_census_parses_counts(self) -> None:
        raw = "\n".join(
            [
                "CEN|0|EvA|10",
                "CEN|0|EvB|5",
                "SAMPLED|0|15|15|0",
                "RESOLVED|mapChannel|uvwValue",
            ]
        )
        with patch(
            "maxmcp.tools.tyflow_census.client.send_command", return_value={"result": raw}
        ):
            payload = json.loads(tyflow_event_census("Flow001", frames=[0]))
        self.assertEqual(payload["frames"]["0"]["EvA"], 10)
        self.assertEqual(payload["sampling"]["0"]["total"], 15)
        self.assertEqual(payload["instrumentation"]["channelProperty"], "mapChannel")
        self.assertNotIn("error", payload)

    def test_census_reports_main_error(self) -> None:
        with patch(
            "maxmcp.tools.tyflow_census.client.send_command",
            return_value={"result": "MAINERR|Flow has no events\nRESOLVED||"},
        ):
            payload = json.loads(tyflow_event_census("Flow001"))
        self.assertIn("error", payload)

    def test_census_rejects_unknown_method(self) -> None:
        payload = json.loads(tyflow_event_census("Flow001", method="telepathy"))
        self.assertIn("error", payload)

    def test_cleanup_always_in_script(self) -> None:
        with patch(
            "maxmcp.tools.tyflow_census.client.send_command",
            return_value={"result": "RESOLVED||"},
        ) as mock_send:
            tyflow_event_census("Flow001")
        script = mock_send.call_args.args[0]
        self.assertIn("zzz_mcp_census", script)
        self.assertIn("MAINERR", script)


if __name__ == "__main__":
    unittest.main()
