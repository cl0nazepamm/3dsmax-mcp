"""Vendor the minimal Qt subset native/src/qt_dialogs.cpp needs.

The bridge links against the Qt DLLs Max already loads, so nothing from Qt is
shipped. Building needs only the headers qt_dialogs.cpp includes and import
libraries for the symbols it calls. This script downloads the official Qt
builds (the oldest Qt of each major version Max ships), compiles
qt_dialogs.cpp against them, copies exactly the included headers into
native/third_party/qt/<version>/include, and writes one .def file per Qt DLL.
CMake turns the .def files into import libraries.

Run again after changing qt_dialogs.cpp's Qt includes or Qt calls:
    .venv\\Scripts\\python.exe scripts\\codegen\\vendor_qt.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "native" / "src" / "qt_dialogs.cpp"
CACHE = ROOT / "local" / "qt-sdk"
OUT = ROOT / "native" / "third_party" / "qt"
VCVARS = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat")

# Max 2023/2024 ship Qt 5.15.1; 2025/2026 ship 6.5.3 and 2027 ships 6.8.3.
# Qt keeps binary compatibility within a major version, so 6.5.3 serves 2025+.
# Max 2027 compiles as C++20, earlier versions as C++17.
VERSIONS = {
    "5.15.1": {"major": 5, "standards": ["/std:c++17"]},
    "6.5.3": {"major": 6, "standards": ["/std:c++17", "/std:c++20"]},
}
MODULES = ("Core", "Gui", "Widgets")
# Mirrors native/CMakeLists.txt for qt_dialogs.cpp. Optimized and unoptimized
# builds reference different symbols, so both are collected.
FLAGS = ["/nologo", "/c", "/EHa", "/GR", "/MD", "/Zc:__cplusplus", "/permissive-", "/showIncludes",
         "/DUNICODE", "/D_UNICODE", "/DNOMINMAX", "/DWIN32", "/D_WIN32", "/DNDEBUG", "/DQT_NO_VERSION_TAGGING"]
OPTIMIZATION = ["/O2", "/Od"]


def sdk_dir(version: str) -> Path:
    path = CACHE / version / "msvc2019_64"
    if not (path / "include" / "QtWidgets").is_dir():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["uvx", "--from", "aqtinstall", "aqt", "install-qt", "windows", "desktop", version,
                        "win64_msvc2019_64", "--archives", "qtbase", "-O", str(CACHE)], check=True)
    return path


def run_vs(command: list[str], cwd: Path) -> str:
    line = subprocess.list2cmdline(command)
    result = subprocess.run(f'call "{VCVARS}" >nul && {line}', shell=True, cwd=cwd,
                            capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def exported(lib: Path, work: Path) -> dict[str, str]:
    """Map each symbol of an import library to its import type: code or data."""
    names: dict[str, str] = {}
    symbol = None
    for line in run_vs(["dumpbin", "/headers", str(lib)], work).splitlines():
        line = line.strip()
        if line.startswith("Symbol name"):
            symbol = line.split(":", 1)[1].split()[0]
        elif line.startswith("Type") and symbol:
            names[symbol] = line.split(":", 1)[1].strip()
            symbol = None
    return names


def vendor(version: str, config: dict) -> None:
    sdk = sdk_dir(version)
    include_root = (sdk / "include").resolve()
    headers: set[Path] = set()
    imported: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for standard, optimization in [(s, o) for s in config["standards"] for o in OPTIMIZATION]:
            obj = work / "qt_dialogs.obj"
            output = run_vs(["cl", *FLAGS, standard, optimization, f"/I{ROOT / 'native' / 'include'}",
                             f"/I{ROOT / 'native' / 'third_party'}", f"/I{include_root}",
                             f"/Fo{obj}", str(SOURCE)], work)
            for match in re.finditer(r"Note: including file:\s+(.+)", output):
                path = Path(match.group(1).strip()).resolve()
                if path.is_relative_to(include_root):
                    headers.add(path)
            for line in run_vs(["dumpbin", "/symbols", str(obj)], work).splitlines():
                if "UNDEF" in line and "External" in line and "| " in line:
                    imported.add(line.split("| ", 1)[1].split()[0].removeprefix("__imp_"))

        target = OUT / version
        if target.exists():
            shutil.rmtree(target)
        for header in sorted(headers):
            dest = target / "include" / header.relative_to(include_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(header, dest)

        assigned: set[str] = set()
        for module in MODULES:
            dll = f"Qt{config['major']}{module}"
            types = exported(sdk / "lib" / f"{dll}.lib", work)
            symbols = sorted(imported & types.keys())
            assigned.update(symbols)
            if symbols:
                body = "".join(f"    {name}{' DATA' if types[name] == 'data' else ''}\n" for name in symbols)
                (target / f"{dll}.def").write_text(f"LIBRARY {dll}.dll\nEXPORTS\n{body}", encoding="ascii")
    unknown = sorted(name for name in imported - assigned if "Q" in name and "std@@" not in name)
    if unknown:
        raise RuntimeError(f"Qt {version}: imports not found in Qt{MODULES}: {unknown[:10]}")
    (target / "README.md").write_text(
        f"Minimal Qt {version} subset for `native/src/qt_dialogs.cpp`, generated by\n"
        "`scripts/codegen/vendor_qt.py` from the official Qt build. The bridge links against the\n"
        "Qt DLLs that 3ds Max already loads; no Qt binary is shipped.\n\n"
        "Qt is available under the GNU LGPL v3 (https://www.gnu.org/licenses/lgpl-3.0.html).\n",
        encoding="ascii")
    print(f"Qt {version}: {len(headers)} headers, {len(assigned)} imported symbols")


def main() -> int:
    for version, config in VERSIONS.items():
        vendor(version, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
