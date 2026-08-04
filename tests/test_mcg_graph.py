import tempfile
import unittest
from pathlib import Path

from maxmcp.helpers.mcg_graph import (
    MCGGraphError,
    MCGHashConflict,
    MCGSecurityError,
    MCGValidationError,
    create_graph_from_template,
    graph_hash,
    inspect_graph,
    is_within,
    patch_graph,
    restore_checkpoint,
)


GRAPH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<graph version="0.50" uuid="11111111-1111-1111-1111-111111111111">
  <meta_info>
    <graph_version guid="22222222-2222-2222-2222-222222222222" number="1.2.3" />
    <identifier>TemplateBox</identifier>
    <displayName>Template Box</displayName>
    <description>A safe graph fixture.</description>
    <category>Tests</category>
  </meta_info>
  <nodes>
    <node operator="Output: geometry" id="0" position="440:220" size="140:70" />
    <node operator="CreateBox" id="1" position="240:140" size="140:154" />
    <node operator="Parameter: Single" id="2" name="Size" min_value="0" max_value="100" default_value="10" position="20:80" size="140:196" />
    <node groupnode="Box inputs" id="4" position="10:60" size="380:260">
      <nodes>1,2</nodes>
    </node>
  </nodes>
  <connections>
    <connection sourcenode="1" sourceport="0" destnode="0" destport="0" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="0" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="1" />
    <connection sourcenode="2" sourceport="0" destnode="1" destport="2" />
  </connections>
</graph>
"""


class MCGGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.graph = self.root / "graph.maxtool"
        self.graph.write_text(GRAPH_XML, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_variant(self, name: str, xml: str) -> Path:
        path = self.root / name
        path.write_text(xml, encoding="utf-8")
        return path

    def test_inspect_graph_normalizes_identity_nodes_connections_and_groups(self) -> None:
        summary = inspect_graph(self.graph)

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["hash"], graph_hash(self.graph))
        self.assertEqual(summary["uuid"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            summary["graph_version_guid"],
            "22222222-2222-2222-2222-222222222222",
        )
        self.assertEqual(summary["graph_version_number"], "1.2.3")
        self.assertEqual(summary["identifier"], "TemplateBox")
        self.assertEqual(summary["node_count"], 4)
        self.assertEqual(summary["connection_count"], 4)
        self.assertEqual(summary["group_count"], 1)
        self.assertEqual(summary["terminal_output_type"], "geometry")
        self.assertEqual(summary["groups"][0]["members"], ["1", "2"])
        self.assertEqual(summary["parameters"][0]["name"], "Size")
        self.assertEqual(summary["parameters"][0]["type"], "Single")
        self.assertFalse(summary["executable"])

    def test_create_graph_from_template_generates_both_identity_guids(self) -> None:
        destination = self.root / "created.maxtool"
        source_hash = graph_hash(self.graph)

        proof = create_graph_from_template(
            self.graph,
            destination,
            "AgentBox",
            display_name="Agent Box",
            description="Generated in a temporary workspace.",
            category="MCP.Tests",
        )
        created = inspect_graph(destination)

        self.assertTrue(proof["created"])
        self.assertTrue(proof["identity_changed"])
        self.assertNotEqual(created["uuid"], inspect_graph(self.graph)["uuid"])
        self.assertNotEqual(
            created["graph_version_guid"],
            inspect_graph(self.graph)["graph_version_guid"],
        )
        self.assertEqual(created["graph_version_number"], "0.0.1")
        self.assertEqual(created["identifier"], "AgentBox")
        self.assertEqual(created["display_name"], "Agent Box")
        self.assertEqual(created["description"], "Generated in a temporary workspace.")
        self.assertEqual(created["category"], "MCP.Tests")
        self.assertEqual(graph_hash(self.graph), source_hash)
        self.assertEqual(proof["hash"], graph_hash(destination))

        with self.assertRaisesRegex(MCGGraphError, "already exists"):
            create_graph_from_template(self.graph, destination, "NoOverwrite")

    def test_patch_graph_supports_all_structured_operations_and_preserves_identity(self) -> None:
        before = inspect_graph(self.graph)
        proof = patch_graph(
            self.graph,
            before["hash"],
            [
                {
                    "op": "disconnect",
                    "source_node": 2,
                    "source_port": 0,
                    "dest_node": 1,
                    "dest_port": 2,
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": 3,
                        "operator": "Constant",
                        "value": "5.0",
                        "position": "40:300",
                    },
                },
                {
                    "op": "connect",
                    "source_node": 3,
                    "source_port": 0,
                    "dest_node": 1,
                    "dest_port": 2,
                },
                {
                    "op": "set_node",
                    "id": 2,
                    "attributes": {"default_value": "20"},
                },
                {"op": "replace_operator", "id": 1, "operator": "CreateBoxV2"},
                {"op": "add_node", "id": 5, "operator": "Constant", "value": 99},
                {"op": "set_node", "id": 4, "members": [1, 2, 3, 5]},
                {"op": "remove_node", "id": 5},
                {
                    "op": "set_meta",
                    "values": {
                        "display_name": "Patched Box",
                        "description": "Patched by structured operations.",
                        "category": "MCP.Patched",
                    },
                },
            ],
        )
        after = inspect_graph(self.graph)

        self.assertTrue(proof["identity_preserved"])
        self.assertEqual(after["uuid"], before["uuid"])
        self.assertEqual(after["graph_version_guid"], before["graph_version_guid"])
        self.assertEqual(after["graph_version_number"], "1.2.4")
        self.assertEqual(proof["before_hash"], before["hash"])
        self.assertEqual(proof["after_hash"], after["hash"])
        self.assertEqual(proof["operation_count"], 9)
        self.assertEqual(after["node_count"], 5)
        self.assertEqual(after["connection_count"], 4)
        self.assertEqual(after["display_name"], "Patched Box")
        self.assertEqual(after["description"], "Patched by structured operations.")
        self.assertEqual(after["category"], "MCP.Patched")

        nodes = {node["id"]: node for node in after["nodes"]}
        self.assertEqual(nodes["1"]["operator"], "CreateBoxV2")
        self.assertEqual(nodes["2"]["attributes"]["default_value"], "20")
        self.assertEqual(nodes["4"]["members"], ["1", "2", "3"])
        self.assertNotIn("5", nodes)
        self.assertTrue(
            any(
                connection["source_node"] == "3"
                and connection["dest_node"] == "1"
                and connection["dest_port"] == "2"
                for connection in after["connections"]
            )
        )

    def test_patch_graph_dry_run_returns_proof_without_writing_or_checkpoint(self) -> None:
        before_bytes = self.graph.read_bytes()
        before_hash = graph_hash(self.graph)
        checkpoint_dir = self.root / "checkpoints"

        proof = patch_graph(
            self.graph,
            before_hash,
            [{"op": "set_meta", "key": "description", "value": "Preview only"}],
            dry_run=True,
            checkpoint_dir=checkpoint_dir,
        )

        self.assertTrue(proof["dry_run"])
        self.assertTrue(proof["changed"])
        self.assertNotEqual(proof["after_hash"], before_hash)
        self.assertEqual(proof["hash"], before_hash)
        self.assertIsNone(proof["checkpoint_path"])
        self.assertEqual(self.graph.read_bytes(), before_bytes)
        self.assertFalse(checkpoint_dir.exists())
        self.assertEqual(inspect_graph(self.graph)["graph_version_number"], "1.2.3")

    def test_patch_graph_rejects_stale_hash_without_writing(self) -> None:
        before = self.graph.read_bytes()
        with self.assertRaises(MCGHashConflict) as raised:
            patch_graph(
                self.graph,
                "0" * 64,
                [{"op": "set_meta", "key": "description", "value": "stale"}],
            )
        self.assertEqual(raised.exception.actual, graph_hash(self.graph))
        self.assertEqual(self.graph.read_bytes(), before)

    def test_patch_graph_rejects_invalid_xml_attributes_and_values_without_writing(self) -> None:
        before = self.graph.read_bytes()
        before_hash = graph_hash(self.graph)

        with self.assertRaisesRegex(MCGGraphError, "Invalid node attribute name"):
            patch_graph(
                self.graph,
                before_hash,
                [{"op": "set_node", "id": 1, "attributes": {"bad key": "value"}}],
            )
        self.assertEqual(self.graph.read_bytes(), before)

        with self.assertRaisesRegex(MCGValidationError, "not valid XML"):
            patch_graph(
                self.graph,
                before_hash,
                [{"op": "set_meta", "values": {"description": "bad\x00value"}}],
            )
        self.assertEqual(self.graph.read_bytes(), before)

    def test_validation_reports_required_graph_invariants(self) -> None:
        variants = {
            "duplicate.maxtool": (
                GRAPH_XML.replace('groupnode="Box inputs" id="4"', 'groupnode="Box inputs" id="2"'),
                "duplicate node id",
            ),
            "endpoint.maxtool": (
                GRAPH_XML.replace('destnode="0" destport="0"', 'destnode="99" destport="0"', 1),
                "missing destination node",
            ),
            "outputs.maxtool": (
                GRAPH_XML.replace('operator="CreateBox"', 'operator="Output: geometry"', 1),
                "exactly one terminal Output",
            ),
            "group.maxtool": (
                GRAPH_XML.replace("<nodes>1,2</nodes>", "<nodes>1,99</nodes>"),
                "references missing node",
            ),
            "destination.maxtool": (
                GRAPH_XML.replace(
                    '    <connection sourcenode="2" sourceport="0" destnode="1" destport="1" />',
                    '    <connection sourcenode="2" sourceport="0" destnode="1" destport="1" />\n'
                    '    <connection sourcenode="3" sourceport="0" destnode="1" destport="1" />',
                ),
                "duplicate destination port",
            ),
            "source-port.maxtool": (
                GRAPH_XML.replace('sourceport="0"', 'sourceport="2"', 1),
                "source port must be 0 or 1",
            ),
            "terminal-port.maxtool": (
                GRAPH_XML.replace('destnode="0" destport="0"', 'destnode="0" destport="99"', 1),
                "must use destination port 0",
            ),
            "terminal-input.maxtool": (
                GRAPH_XML.replace(
                    '    <connection sourcenode="1" sourceport="0" destnode="0" destport="0" />\n',
                    "",
                ),
                "must have exactly one input connection",
            ),
            "unreachable-parameter.maxtool": (
                GRAPH_XML.replace(
                    '    <connection sourcenode="2" sourceport="0" destnode="1" destport="0" />\n',
                    "",
                )
                .replace(
                    '    <connection sourcenode="2" sourceport="0" destnode="1" destport="1" />\n',
                    "",
                )
                .replace(
                    '    <connection sourcenode="2" sourceport="0" destnode="1" destport="2" />\n',
                    "",
                ),
                "is not connected to the terminal",
            ),
        }

        for name, (xml, expected_error) in variants.items():
            with self.subTest(name=name):
                path = self._write_variant(name, xml)
                summary = inspect_graph(path)
                self.assertFalse(summary["valid"])
                self.assertTrue(
                    any(expected_error in error for error in summary["validation_errors"]),
                    summary["validation_errors"],
                )
                with self.assertRaises(MCGValidationError):
                    patch_graph(
                        path,
                        graph_hash(path),
                        [{"op": "set_meta", "key": "description", "value": "no"}],
                    )

    def test_checkpoint_is_exact_and_restore_is_atomic(self) -> None:
        before_bytes = self.graph.read_bytes()
        before_hash = graph_hash(self.graph)
        checkpoints = self.root / "checkpoints"

        patch = patch_graph(
            self.graph,
            before_hash,
            [{"op": "set_meta", "key": "description", "value": "Changed"}],
            checkpoint_dir=checkpoints,
        )
        checkpoint = Path(patch["checkpoint_path"])

        self.assertTrue(checkpoint.is_file())
        self.assertEqual(checkpoint.read_bytes(), before_bytes)
        self.assertEqual(graph_hash(checkpoint), before_hash)
        self.assertFalse(list(self.root.glob(".*.tmp")))

        restored = restore_checkpoint(
            self.graph,
            checkpoint,
            expected_hash=patch["after_hash"],
        )
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["after_hash"], before_hash)
        self.assertEqual(self.graph.read_bytes(), before_bytes)
        self.assertFalse(list(self.root.glob(".*.tmp")))

        with self.assertRaises(MCGHashConflict):
            restore_checkpoint(self.graph, checkpoint, expected_hash="f" * 64)

    def test_invalid_patch_never_creates_checkpoint_or_changes_graph(self) -> None:
        before = self.graph.read_bytes()
        checkpoints = self.root / "invalid-checkpoints"
        with self.assertRaises(MCGValidationError):
            patch_graph(
                self.graph,
                graph_hash(self.graph),
                [
                    {
                        "op": "connect",
                        "source_node": 2,
                        "source_port": 0,
                        "dest_node": 99,
                        "dest_port": 0,
                    }
                ],
                checkpoint_dir=checkpoints,
            )
        self.assertEqual(self.graph.read_bytes(), before)
        self.assertFalse(checkpoints.exists())

    def test_is_within_resolves_traversal_and_sibling_prefixes(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        child = workspace / "nested" / "graph.maxtool"
        sibling = self.root / "workspace-evil" / "graph.maxtool"

        self.assertTrue(is_within(workspace, workspace))
        self.assertTrue(is_within(child, workspace))
        self.assertFalse(is_within(workspace / ".." / "outside.maxtool", workspace))
        self.assertFalse(is_within(sibling, workspace))

    def test_eval_maxscript_is_blocked_unless_explicitly_allowed(self) -> None:
        before = graph_hash(self.graph)
        operation = {
            "op": "add_node",
            "id": 3,
            "operator": "EvalMAXScript",
            "code": "1+1",
        }

        with self.assertRaises(MCGSecurityError) as raised:
            patch_graph(self.graph, before, [operation])
        self.assertTrue(any("EvalMAXScript" in reason for reason in raised.exception.reasons))
        self.assertEqual(graph_hash(self.graph), before)

        proof = patch_graph(
            self.graph,
            before,
            [operation],
            allow_executable=True,
        )
        self.assertTrue(proof["executable"])
        inspected = inspect_graph(self.graph)
        self.assertTrue(inspected["executable"])
        self.assertEqual(inspected["executable_content"][0]["kind"], "operator")
        self.assertEqual(inspected["executable_content"][0]["node_id"], "3")

    def test_nonblank_customui_is_blocked_but_blank_customui_is_safe(self) -> None:
        before = graph_hash(self.graph)
        operation = {
            "op": "set_meta",
            "key": "customui",
            "value": "rollout AgentUI \"Agent\" ()",
        }
        with self.assertRaises(MCGSecurityError) as raised:
            patch_graph(self.graph, before, [operation])
        self.assertTrue(any("customui" in reason for reason in raised.exception.reasons))
        self.assertEqual(graph_hash(self.graph), before)

        proof = patch_graph(
            self.graph,
            before,
            [operation],
            allow_executable=True,
        )
        self.assertTrue(proof["executable"])

        executable_template = self._write_variant(
            "executable-template.maxtool",
            GRAPH_XML.replace(
                "  </meta_info>",
                "    <customui>rollout AgentUI \"Agent\" ()</customui>\n  </meta_info>",
            ),
        )
        with self.assertRaises(MCGSecurityError):
            create_graph_from_template(
                executable_template,
                self.root / "blocked.maxtool",
                "Blocked",
            )

        blank_template = self._write_variant(
            "blank-customui.maxtool",
            GRAPH_XML.replace("  </meta_info>", "    <customui>  </customui>\n  </meta_info>"),
        )
        created = self.root / "blank-safe.maxtool"
        create_graph_from_template(blank_template, created, "BlankSafe")
        self.assertFalse(inspect_graph(created)["executable"])

    def test_public_exceptions_accept_one_message_or_many(self) -> None:
        validation = MCGValidationError("one validation error")
        security = MCGSecurityError("one security finding")

        self.assertEqual(validation.errors, ("one validation error",))
        self.assertEqual(security.reasons, ("one security finding",))


if __name__ == "__main__":
    unittest.main()
