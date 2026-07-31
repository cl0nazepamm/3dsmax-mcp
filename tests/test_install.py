import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install


class InstallTests(unittest.TestCase):
    def test_max_year_for_reads_standard_install_folder_names(self) -> None:
        self.assertEqual(install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2023")), 2023)
        self.assertEqual(install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2027")), 2027)
        self.assertIsNone(install.max_year_for(Path(r"C:\weird\Max")))

    def test_max_year_for_uses_installer_env_var_for_custom_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-max"
            custom.mkdir()
            with patch.dict("os.environ", {"ADSK_3DSMAX_x64_2025": str(custom)}):
                self.assertEqual(install.max_year_for(custom), 2025)

    def test_find_max_installations_uses_env_var_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-max-2026"
            custom.mkdir()
            (custom / "3dsmax.exe").write_text("", encoding="utf-8")
            with patch.dict("os.environ", {"ADSK_3DSMAX_x64_2026": str(custom)}):
                self.assertEqual(install.find_max_installations(), [custom])

    def test_max_dir_for_year_prefers_env_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"ADSK_3DSMAX_x64_2025": tmp}):
                self.assertEqual(install.max_dir_for_year(2025), Path(tmp))

    def test_max_dir_for_year_falls_back_to_default(self) -> None:
        env = dict(install.os.environ)
        env.pop("ADSK_3DSMAX_x64_2025", None)
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(
                install.max_dir_for_year(2025),
                Path(r"C:\Program Files\Autodesk\3ds Max 2025"),
            )

    def test_legacy_install_paths_returns_old_format_files(self) -> None:
        max_dir = Path(r"D:\Max\3ds Max 2025")
        paths = install.legacy_install_paths(max_dir)
        self.assertEqual(
            paths,
            [
                max_dir / "plugins" / "mcp_bridge.gup",
                max_dir / "scripts" / "mcp" / "mcp_server.ms",
                max_dir / "scripts" / "startup" / "mcp_autostart.ms",
            ],
        )

    def test_remove_legacy_installations_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            max_dir = Path(tmp) / "3ds Max 2025"
            for rel in (
                "plugins/mcp_bridge.gup",
                "scripts/mcp/mcp_server.ms",
                "scripts/startup/mcp_autostart.ms",
            ):
                path = max_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("legacy", encoding="utf-8")

            with patch.object(install, "find_max_installations", return_value=[max_dir]):
                self.assertTrue(install.remove_legacy_installations())
            self.assertTrue(all(not path.exists() for path in install.legacy_install_paths(max_dir)))

    def test_remove_legacy_installations_fails_when_files_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            max_dir = Path(tmp) / "3ds Max 2025"
            legacy = max_dir / "plugins" / "mcp_bridge.gup"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("locked", encoding="utf-8")

            with patch.object(install, "find_max_installations", return_value=[max_dir]):
                with patch.object(install, "delete_elevated", return_value=False):
                    self.assertFalse(install.remove_legacy_installations())
            self.assertTrue(legacy.exists())

    def test_package_contents_xml_uses_bin_paths_and_version(self) -> None:
        xml = install.package_contents_xml("9.9.9")
        self.assertIn('AppVersion="9.9.9"', xml)
        self.assertIn("./Contents/bin/mcp_bridge_2025.gup", xml)
        self.assertIn("./Contents/scripts/mcp_server.ms", xml)
        self.assertNotIn("plugins/", xml)

    def test_stage_bundle_creates_expected_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gup = root / "native" / "bin"
            gup.mkdir(parents=True)
            (gup / "mcp_bridge_2025.gup").write_bytes(b"gup")

            script_src = root / "maxscript" / "mcp_server.ms"
            script_src.parent.mkdir(parents=True)
            script_src.write_text("-- mcp", encoding="utf-8")

            patched_gups = {
                2025: gup / "mcp_bridge_2025.gup",
                **{year: root / f"missing_{year}.gup" for year in install.GUP_SRCS if year != 2025},
            }

            dest = root / "bundle"
            with patch.object(install, "GUP_SRCS", patched_gups):
                with patch.object(install, "MS_SERVER", script_src):
                    included, missing = install.stage_bundle(dest)

            self.assertEqual(included, [2025])
            self.assertIn(2023, missing)
            self.assertTrue((dest / "Contents" / "bin" / "mcp_bridge_2025.gup").exists())
            self.assertTrue((dest / "Contents" / "scripts" / "mcp_server.ms").exists())
            contents = (dest / "PackageContents.xml").read_text(encoding="utf-8")
            self.assertIn("./Contents/bin/mcp_bridge_2025.gup", contents)

    def test_native_bridge_sources_are_exact_versioned_binaries(self) -> None:
        for year in (2023, 2024, 2025, 2026, 2027):
            self.assertEqual(install.GUP_SRCS[year].name, f"mcp_bridge_{year}.gup")
            self.assertEqual(
                install.gup_src_for(Path(fr"C:\Program Files\Autodesk\3ds Max {year}")),
                install.GUP_SRCS[year],
            )
        self.assertIsNone(install.gup_src_for(Path(r"C:\Program Files\Autodesk\3ds Max 2028")))

    def test_claude_desktop_config_paths_include_store_and_classic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local_app = Path(tmp) / "LocalAppData"
            roaming = Path(tmp) / "Roaming"
            store_pkg = local_app / "Packages" / "Claude_pzs8sxrjxfjjc"
            store_config = store_pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
            store_config.parent.mkdir(parents=True)

            with patch.dict("os.environ", {"LOCALAPPDATA": str(local_app), "APPDATA": str(roaming)}):
                paths = install.claude_desktop_config_paths()
            self.assertIn(store_config, paths)
            self.assertEqual(paths[-1], roaming / "Claude" / "claude_desktop_config.json")

    def test_app_mcp_config_paths_includes_cursor_and_store_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local_app = Path(tmp) / "LocalAppData"
            (local_app / "Packages" / "Claude_testpkg" / "LocalCache" / "Roaming" / "Claude").mkdir(
                parents=True
            )
            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(local_app), "APPDATA": str(Path(tmp) / "Roaming")},
            ):
                labels = [label for label, _ in install.app_mcp_config_paths()]
                paths = [path for _, path in install.app_mcp_config_paths()]
            self.assertIn("Claude Desktop (Microsoft Store)", labels)
            self.assertIn("Cursor", labels)
            self.assertEqual(paths[labels.index("Cursor")], Path.home() / ".cursor" / "mcp.json")

    def test_mcp_server_entry_uses_uv_run(self) -> None:
        entry = install.mcp_server_entry(r"C:\repo\3dsmax-mcp")
        self.assertEqual(
            entry,
            {"command": "uv", "args": ["run", "--directory", r"C:\repo\3dsmax-mcp", "3dsmax-mcp"]},
        )


def test_max_year_for_uses_installer_env_var_for_custom_paths(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-max"
    custom.mkdir()
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(custom))
    assert install.max_year_for(custom) == 2025


def test_find_max_installations_uses_env_var_path(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-max-2026"
    custom.mkdir()
    (custom / "3dsmax.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADSK_3DSMAX_x64_2026", str(custom))
    # Pin to one year so real installs on the host machine don't leak in
    monkeypatch.setattr(install, "MAX_YEARS", [2026])
    assert install.find_max_installations() == [custom]


def test_max_dir_for_year_prefers_env_over_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(tmp_path))
    assert install.max_dir_for_year(2025) == tmp_path


def test_max_dir_for_year_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("ADSK_3DSMAX_x64_2025", raising=False)
    assert install.max_dir_for_year(2025) == Path(r"C:\Program Files\Autodesk\3ds Max 2025")


def test_native_bridge_sources_are_exact_versioned_binaries() -> None:
    for year in (2023, 2024, 2025, 2026, 2027):
        assert install.GUP_SRCS[year].name == f"mcp_bridge_{year}.gup"
        assert install.gup_src_for(Path(fr"C:\Program Files\Autodesk\3ds Max {year}")) == install.GUP_SRCS[year]

    assert install.gup_src_for(Path(r"C:\Program Files\Autodesk\3ds Max 2028")) is None


def test_claude_desktop_config_paths_include_store_and_classic(monkeypatch, tmp_path: Path) -> None:
    local_app = tmp_path / "LocalAppData"
    roaming = tmp_path / "Roaming"
    store_pkg = local_app / "Packages" / "Claude_pzs8sxrjxfjjc"
    store_config = store_pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    store_config.parent.mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(roaming))

    paths = install.claude_desktop_config_paths()
    assert store_config in paths
    assert paths[-1] == roaming / "Claude" / "claude_desktop_config.json"


def test_app_mcp_config_paths_includes_cursor_and_store_claude(monkeypatch, tmp_path: Path) -> None:
    local_app = tmp_path / "LocalAppData"
    (local_app / "Packages" / "Claude_testpkg" / "LocalCache" / "Roaming" / "Claude").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    labels = [label for label, _ in install.app_mcp_config_paths()]
    paths = [path for _, path in install.app_mcp_config_paths()]
    assert "Claude Desktop (Microsoft Store)" in labels
    assert "Cursor" in labels
    assert paths[labels.index("Cursor")] == Path.home() / ".cursor" / "mcp.json"


def test_mcp_server_entry_uses_uv_run() -> None:
    entry = install.mcp_server_entry(r"C:\repo\3dsmax-mcp")
    assert entry == {
        "command": "uv",
        "args": ["run", "--directory", r"C:\repo\3dsmax-mcp", "3dsmax-mcp"],
    }
