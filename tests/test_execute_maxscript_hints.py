import json
import unittest
from unittest.mock import patch

from maxmcp.helpers.error_hints import suggest_tools_for_maxscript
from maxmcp.tools.execute import (
    _MAXSCRIPT_ERROR_SENTINEL,
    execute_maxscript,
)


class ExecuteMaxScriptHintTests(unittest.TestCase):
    def _send_returns(self, value: str):
        return patch(
            "maxmcp.tools.execute.client.send_command",
            return_value={"result": value},
        )

    def test_success_passes_through_unmodified(self) -> None:
        with self._send_returns("Box:Box001"):
            result = execute_maxscript(code="Box width:10")
        self.assertEqual(result, "Box:Box001")

    def test_sentinel_error_becomes_structured_payload(self) -> None:
        sentinel_result = _MAXSCRIPT_ERROR_SENTINEL + "-- Type error: Call needs function, got: undefined"
        with self._send_returns(sentinel_result):
            raw = execute_maxscript(code="$Box001.material = undefined()")
        payload = json.loads(raw)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error_type"], "MAXScriptError")
        self.assertIn("Type error", payload["error"])

    def test_osl_script_triggers_introspect_osl_hint(self) -> None:
        sentinel_result = _MAXSCRIPT_ERROR_SENTINEL + "-- No setter for OSLMap.osl_filename"
        osl_script = (
            'm = OSLMap()\n'
            'm.OSLPath = (getDir #maxRoot) + "OSL\\\\UberBitmap2.osl"\n'
            'm.osl_filename = "C:/foo.png"'
        )
        with self._send_returns(sentinel_result):
            raw = execute_maxscript(code=osl_script)
        payload = json.loads(raw)
        self.assertEqual(payload["status"], "error")
        self.assertIn("introspect_osl", payload["hint"]["suggested_tools"])

    def test_uberbitmap_filename_triggers_osl_hint(self) -> None:
        suggestions = suggest_tools_for_maxscript('uber = UberBitmap2.osl; uber.filename = "foo.png"')
        self.assertIn("introspect_osl", suggestions)

    def test_unrelated_script_emits_no_hint(self) -> None:
        sentinel_result = _MAXSCRIPT_ERROR_SENTINEL + "-- Unknown system exception"
        with self._send_returns(sentinel_result):
            raw = execute_maxscript(code="thisDoesNotMatchAnyRule()")
        payload = json.loads(raw)
        self.assertNotIn("hint", payload)

    def test_create_object_pattern_suggests_create_object(self) -> None:
        suggestions = suggest_tools_for_maxscript("Box width:10 length:10 height:10")
        self.assertIn("create_object", suggestions)

    def test_material_assignment_suggests_assign_material(self) -> None:
        suggestions = suggest_tools_for_maxscript("$Box001.material = mat")
        self.assertIn("assign_material", suggestions)


if __name__ == "__main__":
    unittest.main()
