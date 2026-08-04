from pathlib import Path

import install


def test_max_year_for_reads_standard_install_folder_names() -> None:
    assert install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2023")) == 2023
    assert install.max_year_for(Path(r"C:\Program Files\Autodesk\3ds Max 2027")) == 2027
    assert install.max_year_for(Path(r"C:\weird\Max")) is None


def test_legacy_install_paths_returns_old_format_files() -> None:
    max_dir = Path(r"D:\Max\3ds Max 2025")
    assert install.legacy_install_paths(max_dir) == [
        max_dir / "plugins" / "mcp_bridge.gup",
        max_dir / "scripts" / "mcp" / "mcp_server.ms",
        max_dir / "scripts" / "startup" / "mcp_autostart.ms",
    ]


def test_remove_legacy_installations_deletes_files(monkeypatch, tmp_path: Path) -> None:
    max_dir = tmp_path / "3ds Max 2025"
    for rel in (
        "plugins/mcp_bridge.gup",
        "scripts/mcp/mcp_server.ms",
        "scripts/startup/mcp_autostart.ms",
    ):
        path = max_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("legacy", encoding="utf-8")

    monkeypatch.setattr(install, "find_max_installations", lambda: [max_dir])
    assert install.remove_legacy_installations()
    assert all(not path.exists() for path in install.legacy_install_paths(max_dir))


def test_remove_legacy_installations_fails_when_files_remain(monkeypatch, tmp_path: Path) -> None:
    max_dir = tmp_path / "3ds Max 2025"
    legacy = max_dir / "plugins" / "mcp_bridge.gup"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("locked", encoding="utf-8")

    monkeypatch.setattr(install, "find_max_installations", lambda: [max_dir])
    monkeypatch.setattr(install, "delete_elevated", lambda path: False)
    assert not install.remove_legacy_installations()
    assert legacy.exists()


def test_package_contents_xml_uses_bin_paths_and_version() -> None:
    xml = install.package_contents_xml("9.9.9")
    assert 'AppVersion="9.9.9"' in xml
    assert "./Contents/bin/mcp_bridge_2025.gup" in xml
    assert "./Contents/scripts/mcp_server.ms" in xml
    assert "plugins/" not in xml


def test_stage_bundle_creates_expected_layout(monkeypatch, tmp_path: Path) -> None:
    gup_dir = tmp_path / "native" / "bin"
    gup_dir.mkdir(parents=True)
    (gup_dir / "mcp_bridge_2025.gup").write_bytes(b"gup")

    script_src = tmp_path / "maxscript" / "mcp_server.ms"
    script_src.parent.mkdir(parents=True)
    script_src.write_text("-- mcp", encoding="utf-8")

    patched_gups = {
        2025: gup_dir / "mcp_bridge_2025.gup",
        **{year: tmp_path / f"missing_{year}.gup" for year in install.GUP_SRCS if year != 2025},
    }
    monkeypatch.setattr(install, "GUP_SRCS", patched_gups)
    monkeypatch.setattr(install, "MS_SERVER", script_src)

    dest = tmp_path / "bundle"
    included, missing = install.stage_bundle(dest)

    assert included == [2025]
    assert 2023 in missing
    assert (dest / "Contents" / "bin" / "mcp_bridge_2025.gup").exists()
    assert (dest / "Contents" / "scripts" / "mcp_server.ms").exists()
    contents = (dest / "PackageContents.xml").read_text(encoding="utf-8")
    assert "./Contents/bin/mcp_bridge_2025.gup" in contents


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


def test_packaged_mcp_server_entry_uses_absolute_console_script(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "Python Install" / "Scripts" / "3dsmax-mcp.exe"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(install, "IS_PACKAGED", True)
    monkeypatch.setattr(install, "console_script_path", lambda: script)

    assert install.mcp_server_entry("ignored") == {"command": str(script)}


def test_packaged_mcp_server_entry_falls_back_to_installed_module(monkeypatch) -> None:
    monkeypatch.setattr(install, "IS_PACKAGED", True)
    monkeypatch.setattr(install, "console_script_path", lambda: None)

    assert install.mcp_server_entry("ignored") == {
        "command": install.sys.executable,
        "args": ["-m", "maxmcp.server"],
    }


def test_register_agents_uses_packaged_server_command(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "Python Install" / "Scripts" / "3dsmax-mcp.exe"
    script.parent.mkdir(parents=True)
    script.touch()
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(install, "IS_PACKAGED", True)
    monkeypatch.setattr(install, "console_script_path", lambda: script)
    monkeypatch.setattr(
        install.shutil,
        "which",
        lambda name: name if name in {"claude", "codex", "gemini"} else None,
    )
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(install, "register_app_mcp_configs", lambda repo_dir: None)

    assert install.register_agents()
    expected_commands = [
        ["claude", "mcp", "add", "--scope", "user", "3dsmax-mcp", "--", str(script)],
        ["codex", "mcp", "add", "3dsmax-mcp", "--", str(script)],
        ["gemini", "mcp", "add", "--scope", "user", "3dsmax-mcp", str(script)],
    ]
    assert [args for args, _ in calls] == [
        install.subprocess.list2cmdline(command) for command in expected_commands
    ]
    assert all(kwargs["shell"] is True for _, kwargs in calls)
    assert all("uv" not in args for args, _ in calls)
