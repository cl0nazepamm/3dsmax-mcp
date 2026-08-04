import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from scripts.gen_tool_registry import extract_tools

from maxmcp.helpers.mcg_graph import (
    MCGSecurityError,
    MCGValidationError,
    graph_hash,
    inspect_graph,
    patch_graph,
)
from maxmcp.helpers.mcg_models import MCGPatchOperation, MCGVerificationSpec
from maxmcp.tools import mcg


GRAPH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<graph version="0.50" uuid="11111111-1111-1111-1111-111111111111">
  <meta_info>
    <graph_version guid="22222222-2222-2222-2222-222222222222" number="1.0.0" />
    <identifier>TemplateBox</identifier><displayName>Template Box</displayName>
    <description>Fixture</description><category>Tests</category>
  </meta_info>
  <nodes>
    <node operator="Output: geometry" id="0" />
    <node operator="CreateBox" id="1" />
    <node operator="Parameter: Single" id="2" name="Size" default_value="10" />
  </nodes>
  <connections>
    <connection sourcenode="1" sourceport="0" destnode="0" destport="0" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="0" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="1" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="2" />
  </connections>
</graph>
"""


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


class MCGToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "template.maxtool"
        self.template.write_text(GRAPH_XML, encoding="utf-8")
        self.env = patch.dict(os.environ, {"MCP_MCG_TEMP_ROOT": str(self.root)}, clear=False)
        self.env.start()
        mcg._reset_mcg_state_for_tests()

    def tearDown(self) -> None:
        mcg._reset_mcg_state_for_tests()
        self.env.stop()
        self.temp.cleanup()

    def _create(self, identifier: str = "AgentBox") -> dict:
        source_id = mcg._source_id(self.template)
        context = {"bridge_available": False, "max_root": ""}
        with patch.object(mcg, "_context_with_fallback", return_value=context):
            return mcg.mcg_create_graph(
                kind="geometry",
                identifier=identifier,
                template_id=source_id,
                compile_graph=False,
            )

    def test_create_graph_forks_read_only_source_into_private_workspace(self) -> None:
        source_hash = graph_hash(self.template)
        result = self._create()
        created = Path(result["graph"]["path"])

        self.assertTrue(result["temporary"])
        self.assertTrue(mcg.is_within(created, mcg._workspace_root()))
        self.assertNotEqual(result["graph"]["uuid"], "11111111-1111-1111-1111-111111111111")
        self.assertNotEqual(
            result["graph"]["graph_version_guid"],
            "22222222-2222-2222-2222-222222222222",
        )
        self.assertEqual(graph_hash(self.template), source_hash)

    def test_create_graph_rejects_source_kind_mismatch(self) -> None:
        source_id = mcg._source_id(self.template)
        context = {"bridge_available": False, "max_root": ""}
        with patch.object(mcg, "_context_with_fallback", return_value=context):
            with self.assertRaisesRegex(MCGValidationError, "modifier"):
                mcg.mcg_create_graph(
                    kind="modifier",
                    identifier="WrongKind",
                    template_id=source_id,
                    compile_graph=False,
                )

    def test_named_destination_port_is_resolved_before_dry_run(self) -> None:
        created = self._create()
        graph_id = created["graph_id"]
        expected_hash = created["graph"]["hash"]
        record = {
            "identifier": "CreateBox",
            "inputs": [
                {"index": 0, "name": "width"},
                {"index": 1, "name": "height"},
                {"index": 2, "name": "depth"},
            ],
        }
        operations = [
            {
                "op": "disconnect",
                "source_node": 2,
                "source_port": 0,
                "dest_node": 1,
                "dest_port": 2,
            },
            {
                "op": "connect",
                "source_node": 2,
                "source_port": 0,
                "dest_node": 1,
                "dest_port": "depth",
            },
        ]
        with patch.object(mcg, "_operator_record", return_value=record):
            result = mcg.mcg_apply_patch(
                graph_id,
                expected_hash,
                operations,
                dry_run=True,
            )

        self.assertEqual(result["operations"][1]["dest_port"], 2)
        self.assertEqual(graph_hash(mcg._resolve_graph(graph_id)), expected_hash)

    def test_fractional_node_and_port_values_are_rejected(self) -> None:
        info = {"nodes": [{"id": "1", "operator": "CreateBox"}]}
        with self.assertRaisesRegex(MCGValidationError, "whole number"):
            mcg._normalize_patch_ports(
                info,
                [{"op": "connect", "source_node": 1.5, "source_port": 0, "dest_node": 1, "dest_port": 0}],
            )
        with self.assertRaisesRegex(MCGValidationError, "whole number"):
            mcg._normalize_patch_ports(
                info,
                [{"op": "connect", "source_node": 1, "source_port": 0.5, "dest_node": 1, "dest_port": 0}],
            )

    def test_function_output_name_resolves_to_source_port_one(self) -> None:
        info = {
            "nodes": [
                {"id": "1", "operator": "Add"},
                {"id": "2", "operator": "Output: geometry"},
            ]
        }
        normalized = mcg._normalize_patch_ports(
            info,
            [
                {
                    "op": "connect",
                    "source_node": 1,
                    "source_port": "function",
                    "dest_node": 2,
                    "dest_port": 0,
                }
            ],
        )
        self.assertEqual(normalized[0]["source_port"], 1)

    def test_compile_path_discovers_collision_safe_generated_wrapper_class(self) -> None:
        graph = self.root / "probe.maxtool"
        graph.write_text(GRAPH_XML, encoding="utf-8")
        graph.with_suffix(".ms").write_text(
            'plugin simpleObject RequestedName_1\n name:"Probe"\n classID:#(1,2)\n()',
            encoding="utf-8",
        )
        raw = "|".join(
            [
                "MCG_COMPILE",
                "true",
                "true",
                "7",
                "1",
                "2",
                "false",
                _b64("RequestedName"),
                _b64("Requested Name"),
                _b64("Geometry"),
                _b64("Validation succeeded"),
            ]
        )
        with patch.object(
            mcg.client,
            "send_command",
            side_effect=[{"result": raw, "meta": {}}, {"result": True}],
        ):
            result = mcg._compile_path(graph)

        self.assertEqual(result["generated_class"], "RequestedName_1")
        self.assertTrue(result["class_available"])
        self.assertFalse(result["identifier_class_available"])

    def test_geometry_parameter_round_trip_uses_unambiguous_separator(self) -> None:
        prop_data = f"{_b64('Size')}:{_b64('24.0')}"
        raw = "|".join(
            [
                "MCG_VERIFY",
                "geometry",
                _b64("AgentBox"),
                "8",
                "12",
                "24",
                "24",
                "24",
                "-12",
                "-12",
                "-12",
                "12",
                "12",
                "12",
                _b64(prop_data),
                "true",
            ]
        )
        parsed = mcg._parse_verification(raw)
        acceptance = mcg._evaluate_acceptance(
            parsed,
            {"parameters": {"Size": 24.0}, "expect": {"dimensions": [24, 24, 24]}},
        )

        self.assertEqual(parsed["parameters"], {"Size": "24.0"})
        self.assertTrue(parsed["disposed"])
        self.assertTrue(acceptance["passed"])

    def test_modifier_default_acceptance_rejects_empty_output(self) -> None:
        result = {
            "kind": "modifier",
            "disposed": True,
            "input": {"num_vertices": 8, "num_faces": 12, "dimensions": [10, 10, 10], "center": [0, 0, 5]},
            "output": {"num_vertices": 0, "num_faces": 0, "dimensions": [0, 0, 0], "center": [0, 0, 0]},
            "parameters": {},
        }
        self.assertFalse(mcg._evaluate_acceptance(result, {})["passed"])

    def test_modifier_verification_proves_translation_and_parameter_readback(self) -> None:
        prop_data = f"{_b64('offset')}:{_b64('[3,4,5]')}"
        raw = "|".join(
            [
                "MCG_VERIFY", "modifier", _b64("AgentModifier"),
                "8", "12", "10", "10", "10", "0", "0", "5",
                "8", "12", "10", "10", "10", "3", "4", "10",
                _b64(prop_data), "true",
            ]
        )
        parsed = mcg._parse_verification(raw)
        acceptance = mcg._evaluate_acceptance(
            parsed,
            {
                "parameters": {"offset": [3, 4, 5]},
                "expect": {"center": [3, 4, 10], "changed": True},
            },
        )

        self.assertEqual(parsed["output"]["center"], [3.0, 4.0, 10.0])
        self.assertTrue(acceptance["passed"])

    def test_patch_failure_rolls_back_and_registers_failed_candidate(self) -> None:
        created = self._create()
        graph_id = created["graph_id"]
        before_hash = created["graph"]["hash"]
        failed = {"compile": {"compiled": False, "diagnostics": "bad operator"}, "verified": False}
        with patch.object(mcg, "_compile_and_verify", return_value=failed):
            result = mcg.mcg_apply_patch(
                graph_id,
                before_hash,
                [{"op": "set_meta", "values": {"description": "candidate"}}],
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(graph_hash(mcg._resolve_graph(graph_id)), before_hash)
        self.assertIn("source_id", result["details"]["failed_candidate"])
        self.assertTrue(result["details"]["rollback"]["restored"])

    def test_patch_failure_never_rolls_back_over_a_newer_graph_hash(self) -> None:
        created = self._create("ConcurrentAgentBox")
        graph_id = created["graph_id"]
        path = mcg._resolve_graph(graph_id)

        def mutate_after_candidate(*_args, **_kwargs):
            patch_graph(
                path,
                graph_hash(path),
                [{"op": "set_meta", "values": {"description": "newer external edit"}}],
            )
            return {"compile": {"compiled": False, "diagnostics": "candidate failed"}, "verified": False}

        with patch.object(mcg, "_compile_and_verify", side_effect=mutate_after_candidate):
            result = mcg.mcg_apply_patch(
                graph_id,
                created["graph"]["hash"],
                [{"op": "set_meta", "values": {"description": "iteration candidate"}}],
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("rollback_conflict", result["details"])
        self.assertEqual(inspect_graph(path)["description"], "newer external edit")

    def test_dependency_scan_blocks_unresolved_operators(self) -> None:
        info = {"nodes": [{"id": "1", "operator": "UnknownCustomOperator"}]}
        with patch.object(mcg, "_operator_record", return_value=None):
            blockers, warnings = mcg._dependency_security(info, max_root=None)

        self.assertEqual(warnings, [])
        self.assertEqual(blockers[0]["kind"], "unresolved_dependency")
        self.assertEqual(blockers[0]["operator"], "UnknownCustomOperator")

    def test_dependency_scan_blocks_impure_scene_mutation_operators(self) -> None:
        info = {"nodes": [{"id": "1", "operator": "SetParent"}]}
        record = {"identifier": "SetParent", "impure": True, "source_path": ""}
        with patch.object(mcg, "_operator_record", return_value=record):
            blockers, warnings = mcg._dependency_security(info, max_root=None)

        self.assertEqual(warnings, [])
        self.assertEqual(blockers[0]["kind"], "impure_operator")
        self.assertIn("mutate scene state", blockers[0]["message"])

    def test_dependency_scan_blocks_unknown_offline_impurity(self) -> None:
        info = {"nodes": [{"id": "1", "operator": "OfflineOnlyOperator"}]}
        record = {
            "identifier": "OfflineOnlyOperator",
            "impure": None,
            "impure_known": False,
            "source_path": "",
        }
        with patch.object(mcg, "_operator_record", return_value=record):
            blockers, warnings = mcg._dependency_security(info, max_root=None)

        self.assertEqual(warnings, [])
        self.assertEqual(blockers[0]["kind"], "unknown_impurity")

    def test_restore_checkpoint_requires_the_current_graph_hash(self) -> None:
        created = self._create("RestoreAgentBox")
        graph_id = created["graph_id"]
        changed = mcg.mcg_apply_patch(
            graph_id,
            created["graph"]["hash"],
            [{"op": "set_meta", "values": {"description": "changed"}}],
            compile_graph=False,
        )
        checkpoint_token = changed["checkpoint_token"]
        current_hash = graph_hash(mcg._resolve_graph(graph_id))

        stale = mcg.mcg_restore_checkpoint(
            graph_id,
            checkpoint_token,
            expected_hash="0" * 64,
            compile_graph=False,
        )
        self.assertEqual(stale["status"], "error")
        self.assertEqual(stale["error_type"], "MCGHashConflict")
        self.assertEqual(graph_hash(mcg._resolve_graph(graph_id)), current_hash)

        restored = mcg.mcg_restore_checkpoint(
            graph_id,
            checkpoint_token,
            expected_hash=current_hash,
            compile_graph=False,
        )
        self.assertTrue(restored["restore"]["restored"])
        self.assertEqual(restored["restore"]["after_hash"], created["graph"]["hash"])

    def test_reload_operators_uses_the_parameterless_max_2027_api(self) -> None:
        context = {"bridge_available": True, "safe_mode": False}
        with (
            patch.object(mcg, "_context_with_fallback", return_value=context),
            patch.object(
                mcg.client,
                "send_command",
                return_value={"result": "Operators reloaded."},
            ) as send_command,
        ):
            result = mcg.mcg_reload_operators()

        script = send_command.call_args.args[0]
        self.assertTrue(result["reloaded"])
        self.assertIn("bridge.ReloadOperators()", script)
        self.assertNotIn("ReloadOperatorsWithMessages", script)

    def test_typed_patch_contract_rejects_boolean_ids(self) -> None:
        adapter = TypeAdapter(MCGPatchOperation)
        with self.assertRaises(ValidationError):
            adapter.validate_python(
                {"op": "connect", "source_node": True, "source_port": 0, "dest_node": 1, "dest_port": 0}
            )

    def test_verification_contract_rejects_unknown_expectations(self) -> None:
        with self.assertRaises(ValidationError):
            MCGVerificationSpec.model_validate({"expect": {"magic": 1}})

    def test_safe_mode_blocks_explicit_executable_graph_authorization(self) -> None:
        executable = mcg._workspace_root() / "executable.maxtool"
        executable.write_text(
            GRAPH_XML.replace("</meta_info>", "<customui>print 1</customui></meta_info>"),
            encoding="utf-8",
        )
        graph_id = mcg._register_graph(executable)
        context = {"bridge_available": True, "max_root": "", "safe_mode": True, "secure_mode": False}
        with (
            patch.object(mcg, "_context_with_fallback", return_value=context),
            patch.object(mcg, "_dependency_security", return_value=([], [])),
            patch.object(mcg, "_compile_path") as compile_path,
        ):
            with self.assertRaises(MCGSecurityError):
                mcg._compile_and_verify(
                    graph_id,
                    verify=False,
                    verification=None,
                    allow_executable=True,
                    timeout_seconds=10,
                )
        compile_path.assert_not_called()

    def test_native_apply_uses_exact_compiled_identity_and_typed_node_selectors(self) -> None:
        proof = {
            "path": str(self.template),
            "hash": "a" * 64,
            "identity": {"uuid": "graph-uuid"},
            "compile": {"compiled": True, "class_id": [123, 456]},
        }
        native_payload = {
            "class_id": [123, 456],
            "graph_path": str(self.template),
            "expected_identifier": "AgentModifier",
        }
        response = {"result": json.dumps({"modifier": {"index": 1}})}
        with (
            patch.object(mcg, "_native_modifier_payload", return_value=(proof, native_payload)),
            patch.object(mcg.client, "send_command", return_value=response) as send_command,
        ):
            result = mcg.mcg_apply_modifier(
                "graph_test",
                target_handle=1001,
                node_parameters={"Source": 1002, "Up Node": "OrientationGuide"},
            )

        sent = json.loads(send_command.call_args.args[0])
        self.assertEqual(send_command.call_args.kwargs["cmd_type"], "native:mcg_apply_modifier")
        self.assertEqual(sent["class_id"], [123, 456])
        self.assertEqual(sent["graph_path"], str(self.template))
        self.assertEqual(sent["handle"], 1001)
        self.assertEqual(sent["node_parameters"]["Source"], {"handle": 1002})
        self.assertEqual(sent["node_parameters"]["Up Node"], {"name": "OrientationGuide"})
        self.assertEqual(result["instance"]["modifier"]["index"], 1)

    def test_native_apply_rejects_non_modifier_graph_before_compile(self) -> None:
        created = self._create("GeometryOnly")
        with patch.object(mcg, "_compile_and_verify") as compile_graph:
            result = mcg.mcg_apply_modifier(created["graph_id"], target_name="Target")

        self.assertEqual(result["status"], "error")
        self.assertIn("modifier graphs", result["error"])
        compile_graph.assert_not_called()

    def test_native_apply_rejects_boolean_node_handles_before_bridge_call(self) -> None:
        with (
            patch.object(mcg, "_native_modifier_payload") as compile_graph,
            patch.object(mcg.client, "send_command") as send_command,
        ):
            result = mcg.mcg_apply_modifier(
                "graph_test",
                target_name="Target",
                node_parameters={"Source": True},
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("cannot be boolean", result["error"])
        compile_graph.assert_not_called()
        send_command.assert_not_called()

    def test_native_set_node_parameter_sends_source_separately(self) -> None:
        proof = {"path": str(self.template), "hash": "b" * 64, "identity": {}, "compile": {}}
        native_payload = {
            "class_id": [10, 20],
            "graph_path": str(self.template),
            "expected_identifier": "AgentModifier",
        }
        with (
            patch.object(mcg, "_native_modifier_payload", return_value=(proof, native_payload)),
            patch.object(
                mcg.client,
                "send_command",
                return_value={"result": json.dumps({"updated": {"parameter": "Source"}})},
            ) as send_command,
        ):
            result = mcg.mcg_set_node_parameter(
                "graph_test",
                modifier_index=2,
                parameter="Source",
                target_name="Target",
                source_handle=2200,
            )

        sent = json.loads(send_command.call_args.args[0])
        self.assertEqual(send_command.call_args.kwargs["cmd_type"], "native:mcg_set_node_parameter")
        self.assertEqual(sent["name"], "Target")
        self.assertEqual(sent["modifier_index"], 2)
        self.assertEqual(sent["parameter"], "Source")
        self.assertEqual(sent["source"], {"handle": 2200})
        self.assertEqual(result["instance"]["updated"]["parameter"], "Source")

    def test_native_resolve_and_inspect_forward_the_compiler_identity(self) -> None:
        proof = {
            "path": str(self.template),
            "hash": "c" * 64,
            "identity": {"uuid": "graph-uuid"},
            "compile": {"compiled": True, "class_id": [77, 88]},
        }
        base_payload = {
            "class_id": [77, 88],
            "graph_path": str(self.template),
            "expected_identifier": "AgentModifier",
        }
        with (
            patch.object(
                mcg,
                "_native_modifier_payload",
                side_effect=[(proof, dict(base_payload)), (proof, dict(base_payload))],
            ),
            patch.object(
                mcg.client,
                "send_command",
                side_effect=[
                    {"result": json.dumps({"resolved": True, "class_id": [77, 88]})},
                    {"result": json.dumps({"modifier": {"index": 3, "class_id": [77, 88]}})},
                ],
            ) as send_command,
        ):
            resolved = mcg.mcg_resolve_class("graph_test")
            inspected = mcg.mcg_inspect_instance(
                "graph_test",
                modifier_index=3,
                target_handle=3001,
            )

        resolve_payload = json.loads(send_command.call_args_list[0].args[0])
        inspect_payload = json.loads(send_command.call_args_list[1].args[0])
        self.assertEqual(send_command.call_args_list[0].kwargs["cmd_type"], "native:mcg_resolve_class")
        self.assertEqual(send_command.call_args_list[1].kwargs["cmd_type"], "native:mcg_inspect_instance")
        self.assertEqual(resolve_payload, base_payload)
        self.assertEqual(inspect_payload["class_id"], [77, 88])
        self.assertEqual(inspect_payload["handle"], 3001)
        self.assertEqual(inspect_payload["modifier_index"], 3)
        self.assertTrue(resolved["instance"]["resolved"])
        self.assertEqual(inspected["instance"]["modifier"]["index"], 3)

    def test_native_instance_inspection_keeps_python_orchestration_out_of_chat_registry(self) -> None:
        tools = extract_tools(Path("maxmcp/tools/mcg.py"))
        names = {tool["name"] for tool in tools}
        self.assertNotIn("mcg_apply_modifier", names)
        self.assertNotIn("mcg_inspect_instance", names)
        self.assertNotIn("mcg_resolve_class", names)
        self.assertNotIn("mcg_set_node_parameter", names)

    def test_structured_native_error_code_is_preserved(self) -> None:
        class NativeFailure(RuntimeError):
            bridge_message = json.dumps(
                {"type": "NativeError", "code": "MCG_GRAPH_MISMATCH", "message": "wrong graph"}
            )

        self.assertEqual(mcg._exception_code(NativeFailure("wrapped")), "MCG_GRAPH_MISMATCH")

    def test_prefixed_native_error_code_is_preserved(self) -> None:
        payload = json.dumps(
            {"type": "NativeError", "code": "MCG_REFERENCE_LOOP", "message": "self reference"}
        )
        self.assertEqual(
            mcg._exception_code(RuntimeError(f"MAXScript error: {payload}")),
            "MCG_REFERENCE_LOOP",
        )


if __name__ == "__main__":
    unittest.main()
