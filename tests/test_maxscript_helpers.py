import unittest

from maxmcp.helpers.maxscript import safe_name, safe_string


class MaxscriptHelperTests(unittest.TestCase):
    def test_safe_string_escapes_backslashes_and_quotes(self) -> None:
        value = 'C:\\temp\\"quoted"'
        self.assertEqual(safe_string(value), 'C:\\\\temp\\\\\\"quoted\\"')

    def test_safe_name_escapes_single_quotes_too(self) -> None:
        value = 'Box "A"\\B\'s'
        self.assertEqual(safe_name(value), 'Box \\"A\\"\\\\B\\\'s')


if __name__ == "__main__":
    unittest.main()
