import unittest

from maxmcp.helpers.native_compat import is_missing_native_route_error
from maxmcp.max_client import MaxBridgeError


class NativeCompatibilityTests(unittest.TestCase):
    def test_structured_unknown_route_is_compatible(self) -> None:
        error = "Unknown command type: native:new_route"
        exc = MaxBridgeError(error, {"success": False, "error": error})

        self.assertTrue(is_missing_native_route_error(exc))

    def test_legacy_runtime_unknown_route_is_compatible(self) -> None:
        self.assertTrue(
            is_missing_native_route_error(
                RuntimeError("Unknown native command: native:new_route")
            )
        )

    def test_native_handler_failure_is_not_compatible(self) -> None:
        error = "Native handler failed to save output"
        exc = MaxBridgeError(error, {"success": False, "error": error})

        self.assertFalse(is_missing_native_route_error(exc))
        self.assertFalse(is_missing_native_route_error(RuntimeError(error)))


if __name__ == "__main__":
    unittest.main()
