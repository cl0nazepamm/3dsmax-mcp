"""Developer helper for the hot-reloadable native core (MCP_HOT_CORE=ON).

Configures and builds a separate native/build-hot-<ver> tree, prints where the
built core DLL is and how the shell finds it, and shows which core a shell
would load. It never installs, copies into Program Files or ProgramData, or
stops 3ds Max; deploying the hot shell .gup is a separate step.

    python scripts/dev/hot_core.py configure            # native/build-hot-2027
    python scripts/dev/hot_core.py build                # mcp_core target only, Release
    python scripts/dev/hot_core.py path
    python scripts/dev/hot_core.py status --gup-dir "<folder of the installed .gup>"
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[2] / "native"
VERSIONS = ("2023", "2024", "2025", "2026", "2027")
CORE_DLL = Path("Release") / "core" / "mcp_core.dll"
SHELL_GUP = Path("Release") / "mcp_bridge.gup"
TRUE = {"ON", "1", "TRUE", "YES", "Y"}
SPACE = " \t\r\n\ufeff"


def read_cache(directory: Path) -> dict[str, str]:
    """CMakeCache.txt entries as {name: value}; empty when not configured."""
    cache, entries = directory / "CMakeCache.txt", {}
    if cache.is_file():
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            if line[:1] not in ("", "#", "/") and "=" in line:
                key, value = line.split("=", 1)
                entries[key.split(":", 1)[0]] = value
    return entries


def target_dir(args) -> Path:
    return Path(args.build_dir).resolve() if args.build_dir else NATIVE / f"build-hot-{args.max_version}"


def require_hot(directory: Path) -> dict[str, str]:
    cache = read_cache(directory)
    if not cache:
        sys.exit(f"{directory} is not configured; run: hot_core.py configure")
    if cache.get("MCP_HOT_CORE", "").upper() not in TRUE:
        sys.exit(f"{directory} is a static build (MCP_HOT_CORE=OFF); it has no mcp_core target")
    return cache


def cmake_exe(cache: dict[str, str]) -> str:
    for candidate in (os.environ.get("CMAKE_EXE"), cache.get("CMAKE_COMMAND"),
                      r"C:\Program Files\CMake\bin\cmake.exe", shutil.which("cmake")):
        if candidate and Path(candidate).is_file():
            return candidate
    sys.exit("cmake not found; set CMAKE_EXE to cmake.exe")


def run(command: list[str]) -> None:
    print(">", subprocess.list2cmdline(command), flush=True)
    if subprocess.call(command) != 0:
        sys.exit("command failed")


def describe(path: Path, missing: str = "not built yet") -> str:
    if not path.is_file():
        return missing
    stat = path.stat()
    return f"built {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}, {stat.st_size:,} bytes"


def configure(args) -> None:
    target, version = target_dir(args), args.max_version
    existing = read_cache(target)
    if existing and existing.get("MCP_HOT_CORE", "").upper() not in TRUE:
        sys.exit(f"refusing to reconfigure static build {target} with MCP_HOT_CORE=ON")
    candidates = [Path(args.reference).resolve()] if args.reference else \
        [NATIVE / f"build-{version}", NATIVE / "build-2027"]
    reference = next((c for c in candidates if read_cache(c)), None)
    if args.reference and not reference:
        sys.exit(f"--reference {args.reference} is not a configured build directory")
    ref = read_cache(reference) if reference else {}
    sdk = args.sdk or os.environ.get(f"ADSK_3DSMAX_SDK_{version}") or \
        rf"C:\Program Files\Autodesk\3ds Max {version} SDK\maxsdk"
    if not (Path(sdk) / "include" / "max.h").is_file():
        sys.exit(f"3ds Max {version} SDK not found: {sdk} (pass --sdk or set ADSK_3DSMAX_SDK_{version})")
    command = [cmake_exe(existing or ref), "-S", str(NATIVE), "-B", str(target)]
    if not existing:  # a new tree: the reference build's Visual Studio instance, so the same MSVC
        print(f"Generator settings from {reference or 'build.bat defaults'}")
        command += ["-G", ref.get("CMAKE_GENERATOR") or "Visual Studio 17 2022",
                    "-A", ref.get("CMAKE_GENERATOR_PLATFORM") or "x64"]
        if ref.get("CMAKE_GENERATOR_TOOLSET"):
            command += ["-T", ref["CMAKE_GENERATOR_TOOLSET"]]
        if ref.get("CMAKE_GENERATOR_INSTANCE"):
            command.append(f"-DCMAKE_GENERATOR_INSTANCE={ref['CMAKE_GENERATOR_INSTANCE']}")
    run(command + ["-DMCP_HOT_CORE=ON", f"-DMAX_VERSION={version}", f"-DMAXSDK_PATH={sdk}"])
    print(f"\nConfigured {target}. Next: hot_core.py build" +
          (f" --build-dir \"{target}\"" if args.build_dir else f" --max-version {version}"))


def build(args) -> None:
    target = target_dir(args)
    run([cmake_exe(require_hot(target)), "--build", str(target), "--config", "Release", "--target", "mcp_core"])
    print(f"\nCore DLL: {target / CORE_DLL} ({describe(target / CORE_DLL)})")
    print("Load it with reload_native_core(action=\"reload\") once a hot shell points at it (hot_core.py path).")


def path(args) -> None:
    target = target_dir(args)
    require_hot(target)
    dll, gup = target / CORE_DLL, target / SHELL_GUP
    print(f"Core DLL:  {dll}\n           {describe(dll)}")
    print(f"Hot shell: {gup}\n           {describe(gup)}; deploy it separately, this script never installs it")
    print("\nPoint the installed hot shell at this core with one of (first set wins):")
    print("  1. reload_native_core(action=\"reload\", path=...) with the path above, for one reload")
    print(f"  2. MCP_CORE_DLL in 3ds Max's environment, set before Max starts:\n       $env:MCP_CORE_DLL = \"{dll}\"")
    print(f"  3. A file named mcp_core.path next to the installed .gup, with exactly this one line:\n       {dll}")
    print("Otherwise the shell loads <folder of the .gup>\\core\\mcp_core.dll.")


# Source resolution, as McpCoreLoader::ResolveSource (native/include/mcp_bridge/core_loader.h).
def trim(text: str) -> str:
    value = text.strip(SPACE)
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def read_text(file: Path) -> str:
    try:
        data = file.read_bytes()
    except OSError:
        return ""
    if data[:2] == b"\xff\xfe":
        return data[2:].decode("utf-16-le", errors="replace")
    return data[3:].decode("utf-8", errors="replace") if data[:3] == b"\xef\xbb\xbf" else \
        data.decode("utf-8", errors="replace")


def is_absolute(value: str) -> bool:
    return (len(value) >= 2 and value[1] == ":") or value[:1] in ("\\", "/")


def join(directory: str, value: str) -> str:
    return value if is_absolute(value) or not directory else directory.rstrip("\\/") + "\\" + value


def temp_dir() -> Path:
    """GetTempPathW, which the shell's shadow directory is under."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(261)
    return Path(buffer.value) if ctypes.windll.kernel32.GetTempPathW(261, buffer) else Path(".")


def status(args) -> None:
    gup_dir = str(Path(args.gup_dir).resolve()) if args.gup_dir else ""
    path_file = Path(gup_dir) / "mcp_core.path" if gup_dir else None
    line = next((trim(x) for x in read_text(path_file).split("\n") if trim(x)), "") if path_file else ""
    sources = [("request path (--path)", trim(args.path or "")),
               ("MCP_CORE_DLL (this shell's environment; Max reads its own)", trim(os.environ.get("MCP_CORE_DLL", ""))),
               (f"mcp_core.path ({path_file})", line)]
    origin, value = next(((o, v) for o, v in sources if v), ("default", "core\\mcp_core.dll"))
    if not gup_dir and not is_absolute(value):
        print(f"Source:   {origin} -> {value}\nPass --gup-dir to resolve mcp_core.path and relative paths.")
        return
    resolved = Path(os.path.abspath(join(gup_dir, value)))
    print(f"Source:   {origin}\nResolves: {resolved}\n          "
          + describe(resolved, "missing: a reload would fail with CORE_LOAD_FAILED"))
    built = target_dir(args) / CORE_DLL
    if built.is_file():
        same = os.path.normcase(str(resolved)) == os.path.normcase(str(built))
        print(f"Build:    {built}\n          " + ("this is the built core" if same else
              "differs from the built core; run hot_core.py path for how to point the shell at it"))
    shadows = temp_dir() / "3dsmax-mcp" / "core"
    copies = sorted(shadows.glob("mcp_core-*.dll")) if shadows.is_dir() else []
    print(f"Shadow copies in {shadows}: " + (", ".join(c.name for c in copies) or "none"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
            ("configure", configure, "configure native/build-hot-<ver> with MCP_HOT_CORE=ON"),
            ("build", build, "build only the mcp_core target there (Release)"),
            ("path", path, "print the built core DLL and how the shell finds it"),
            ("status", status, "show which core a shell would load")):
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        sub.add_argument("--max-version", choices=VERSIONS, default="2027", help="target 3ds Max (default 2027)")
        sub.add_argument("--build-dir", help="hot build directory (default native/build-hot-<ver>)")
        if name == "configure":
            sub.add_argument("--reference", help="build dir whose generator settings to reuse "
                                                 "(default native/build-<ver>, else native/build-2027)")
            sub.add_argument("--sdk", help="Max SDK maxsdk folder (default ADSK_3DSMAX_SDK_<ver>, "
                                           "else C:\\Program Files\\Autodesk\\3ds Max <ver> SDK\\maxsdk)")
        if name == "status":
            sub.add_argument("--gup-dir", help="folder of the installed hot shell .gup")
            sub.add_argument("--path", help="a reload_native_core path argument to resolve")
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
