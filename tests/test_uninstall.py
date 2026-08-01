from pathlib import Path
from unittest.mock import patch

import install
import uninstall


def test_find_max_installations_uses_adsk_env_var(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-max"
    custom.mkdir()
    (custom / "3dsmax.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(custom))
    monkeypatch.setattr(install, "MAX_YEARS", [2025])

    assert uninstall.find_max_installations() == [custom]


def test_find_max_installations_returns_all_available(monkeypatch, tmp_path: Path) -> None:
    max_2024 = tmp_path / "max-2024"
    max_2026 = tmp_path / "max-2026"
    max_2024.mkdir()
    max_2026.mkdir()
    (max_2024 / "3dsmax.exe").write_text("", encoding="utf-8")
    (max_2026 / "3dsmax.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADSK_3DSMAX_x64_2024", str(max_2024))
    monkeypatch.setenv("ADSK_3DSMAX_x64_2026", str(max_2026))
    monkeypatch.setattr(install, "MAX_YEARS", [2024, 2026])

    assert uninstall.find_max_installations() == [max_2024, max_2026]


def test_find_max_installations_deduplicates_same_path(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "shared-max"
    custom.mkdir()
    (custom / "3dsmax.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADSK_3DSMAX_x64_2024", str(custom))
    monkeypatch.setenv("ADSK_3DSMAX_x64_2025", str(custom))
    monkeypatch.setattr(install, "MAX_YEARS", [2024, 2025])

    assert uninstall.find_max_installations() == [custom]


def test_remove_dir_elevated_removes_tree(tmp_path: Path) -> None:
    target = tmp_path / "3dsmax-mcp"
    (target / "Contents" / "bin").mkdir(parents=True)
    (target / "PackageContents.xml").write_text("x", encoding="utf-8")

    assert uninstall.remove_dir_elevated(target)
    assert not target.exists()
    assert uninstall.remove_dir_elevated(target)  # already gone is still success


def test_remove_max_deployment_calls_delete(tmp_path: Path) -> None:
    max_dir = tmp_path / "max"
    plugins = max_dir / "plugins"
    scripts_mcp = max_dir / "scripts" / "mcp"
    startup = max_dir / "scripts" / "startup"
    plugins.mkdir(parents=True)
    scripts_mcp.mkdir(parents=True)
    startup.mkdir(parents=True)

    gup = plugins / "mcp_bridge.gup"
    ms_server = scripts_mcp / "mcp_server.ms"
    ms_auto = startup / "mcp_autostart.ms"
    for f in (gup, ms_server, ms_auto):
        f.write_text("x", encoding="utf-8")

    deleted: list[Path] = []

    def fake_delete(path: Path) -> bool:
        deleted.append(path)
        if path.exists():
            path.unlink()
        return True

    with patch.object(uninstall, "delete_elevated", side_effect=fake_delete):
        uninstall.remove_max_deployment(max_dir)

    assert deleted == [gup, ms_server, ms_auto]
    assert not scripts_mcp.exists()
