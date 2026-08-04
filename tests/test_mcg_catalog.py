import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.helpers.mcg_catalog import (
    bundled_sample_root,
    parse_compound_metadata,
    parse_operator_metadata,
    sample_roots,
)


class MCGCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_operator_metadata_is_explicitly_untyped(self) -> None:
        path = self.root / "OperatorMetaInfo.xml"
        path.write_text(
            """<operators><operator><identifier>CreateThing</identifier><displayName>Thing</displayName>
            <category>Tests</category><description>Creates it.</description><arg0>size</arg0></operator></operators>""",
            encoding="utf-8",
        )
        record = parse_operator_metadata(str(path))[0]

        self.assertFalse(record["typed"])
        self.assertEqual(record["return_type"], "")
        self.assertEqual(record["inputs"], [{"index": 0, "name": "size", "type": ""}])
        self.assertIsNone(record["impure"])
        self.assertFalse(record["impure_known"])

    def test_compound_inputs_follow_output_traversal_and_empty_deprecated_is_false(self) -> None:
        path = self.root / "fixture.maxcompound"
        path.write_text(
            """<graph uuid="u"><meta_info><identifier>Fixture</identifier><deprecated /></meta_info>
            <nodes>
              <node operator="Output: compound" id="0" />
              <node operator="Join" id="1" />
              <node operator="Input: Single" id="20" name="second" />
              <node operator="Input: Single" id="10" name="first" />
            </nodes><connections>
              <connection sourcenode="1" sourceport="0" destnode="0" destport="0" />
              <connection sourcenode="20" sourceport="0" destnode="1" destport="1" />
              <connection sourcenode="10" sourceport="0" destnode="1" destport="0" />
            </connections></graph>""",
            encoding="utf-8",
        )
        record = parse_compound_metadata(str(path))

        self.assertIsNotNone(record)
        self.assertEqual([item["name"] for item in record["inputs"]], ["first", "second"])
        self.assertFalse(record["deprecated"])
        self.assertFalse(record["typed"])
        self.assertEqual(record["return_type"], "")
        self.assertFalse(record["impure"])
        self.assertTrue(record["impure_known"])

    def test_bundled_samples_are_portable_xml_only_corpus(self) -> None:
        with patch.dict(os.environ, {"MCP_MCG_SAMPLE_ROOTS": ""}, clear=False):
            roots = sample_roots()

        bundled = bundled_sample_root().resolve()
        self.assertIn(bundled, roots)
        self.assertEqual(len(list(bundled.rglob("*.maxtool"))), 43)
        self.assertEqual(len(list(bundled.rglob("*.maxcompound"))), 282)
        self.assertFalse((bundled / "Scenes").exists())
        self.assertFalse((bundled / "Packages").exists())
        self.assertEqual(len(list(bundled.rglob("*.max"))), 0)
        self.assertEqual(len(list(bundled.rglob("*.mcg"))), 0)

    def test_configured_sample_roots_extend_bundled_samples(self) -> None:
        with patch.dict(
            os.environ,
            {"MCP_MCG_SAMPLE_ROOTS": str(self.root)},
            clear=False,
        ):
            roots = sample_roots()

        self.assertEqual(roots[0], self.root.resolve())
        self.assertIn(bundled_sample_root().resolve(), roots)


if __name__ == "__main__":
    unittest.main()
