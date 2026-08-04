#!/usr/bin/env python3
"""Manual MCP tool inspector — one tool at a time, no batch runs.

Shows the exact structured tripback an AI agent receives from each tool call.

Usage (3ds Max open, MCP bridge loaded):
    uv run python scripts/tool_playground.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "tool_playground" / "catalog.json"
GEN_CATALOG = ROOT / "scripts" / "gen_tool_catalog.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RISK_LABEL = {
    "safe": "SAFE · read-only",
    "read": "READ · needs args",
    "changes_scene": "MUTATE · changes scene",
    "advanced": "ADVANCED · extra setup",
}

# Catalog example args use this placeholder for object-name params (see
# gen_tool_catalog.example_for). The GUI resolves it against the live scene.
OBJECT_PLACEHOLDER = "$first_object"


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value == OBJECT_PLACEHOLDER
    if isinstance(value, list):
        return any(_contains_placeholder(v) for v in value)
    if isinstance(value, dict):
        return any(_contains_placeholder(v) for v in value.values())
    return False


def _substitute_placeholder(value: Any, target: str) -> Any:
    if isinstance(value, str):
        return target if value == OBJECT_PLACEHOLDER else value
    if isinstance(value, list):
        return [_substitute_placeholder(v, target) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_placeholder(v, target) for k, v in value.items()}
    return value


def ensure_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists() or _catalog_needs_refresh():
        subprocess.check_call(
            [sys.executable, str(GEN_CATALOG)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _catalog_needs_refresh() -> bool:
    if not CATALOG_PATH.exists():
        return True
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    if catalog.get("version", 1) < 2:
        return True
    if not catalog.get("groups"):
        return True
    return False


def format_tripback(tool_name: str, envelope: dict[str, Any]) -> str:
    """Human-readable tripback — same data the AI gets, easy to scan."""
    ok = envelope.get("ok")
    status = "OK" if ok else "FAILED"
    error = envelope.get("error")
    result = envelope.get("result")
    warnings = envelope.get("warnings") or []

    lines = [
        "═" * 60,
        "TRIPBACK",
        "═" * 60,
        f"  status     {status}",
        f"  tool       {tool_name}",
    ]
    if envelope.get("elapsed_ms") is not None:
        lines.append(f"  elapsed    {envelope.get('elapsed_ms')} ms")
    transport_name = (envelope.get("transport") or {}).get("transport")
    if transport_name:
        lines.append(f"  transport  {transport_name}")
    if warnings:
        lines.append(f"  warnings   {len(warnings)}")
    if error:
        et = error.get("type", "Error") if isinstance(error, dict) else "Error"
        em = error.get("message", error) if isinstance(error, dict) else str(error)
        lines.extend(["", "── error ──", f"  [{et}] {em}"])
    lines.extend([
        "",
        "── result (payload the AI reads) ──",
        json.dumps(result, indent=2, ensure_ascii=False) if result is not None else "  null",
        "",
        "── full envelope (exact MCP tool return) ──",
        json.dumps(envelope, indent=2, ensure_ascii=False),
        "",
    ])
    return "\n".join(lines)


class ToolInspectorApp:
    def __init__(self) -> None:
        self.catalog = ensure_catalog()
        self.tools: list[dict] = self.catalog["tools"]
        self._mcp = None
        self._busy = False
        self._selected_name: str | None = None
        self._history: list[tuple[str, bool, str]] = []  # name, ok, tripback text

        self.root = tk.Tk()
        self.root.title("3ds Max MCP — Tool Inspector")
        self.root.geometry("1200x820")
        self.root.minsize(960, 680)

        self._build_ui()
        self.status_var.set("Ready — nothing runs until you click a button.")

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill=tk.X)

        ttk.Label(
            top,
            text="Manual tool inspector · one call at a time · tripback = what the AI sees",
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)

        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var, foreground="#444").pack(side=tk.RIGHT)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        # ── Left: tool picker ──
        left = ttk.Frame(body, width=320)
        body.add(left, weight=1)

        filt = ttk.Frame(left)
        filt.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(filt, text="Search").grid(row=0, column=0, sticky=tk.W)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._rebuild_tool_buttons())
        ttk.Entry(filt, textvariable=self.search_var).grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))
        filt.columnconfigure(1, weight=1)

        ttk.Label(filt, text="Group").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self.groups_meta: list[dict] = self.catalog.get("groups") or []
        group_names = ["All"] + [g["name"] for g in self.groups_meta]
        self.group_var = tk.StringVar(value="All")
        group_cb = ttk.Combobox(filt, textvariable=self.group_var, values=group_names, state="readonly", width=22)
        group_cb.grid(row=1, column=1, sticky=tk.EW, padx=(6, 0), pady=(4, 0))
        group_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_group_changed())

        ttk.Label(filt, text="Category").grid(row=2, column=0, sticky=tk.W, pady=(4, 0))
        self.category_var = tk.StringVar(value="All")
        self.category_cb = ttk.Combobox(filt, textvariable=self.category_var, values=["All"], state="readonly", width=22)
        self.category_cb.grid(row=2, column=1, sticky=tk.EW, padx=(6, 0), pady=(4, 0))
        self.category_cb.bind("<<ComboboxSelected>>", lambda _e: self._rebuild_tool_buttons())

        self.group_hint_var = tk.StringVar(value="")
        ttk.Label(filt, textvariable=self.group_hint_var, wraplength=280, foreground="#666", font=("Segoe UI", 8)).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0)
        )

        self.only_safe_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left, text="Safe tools only", variable=self.only_safe_var, command=self._rebuild_tool_buttons
        ).pack(anchor=tk.W, pady=(4, 0))

        ttk.Label(left, text=f"{len(self.tools)} tools — click Load, then Run", foreground="#666").pack(
            anchor=tk.W, pady=(6, 2)
        )

        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self._tool_canvas = tk.Canvas(list_frame, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tool_canvas.yview)
        self._tool_inner = ttk.Frame(self._tool_canvas)
        self._tool_inner.bind(
            "<Configure>",
            lambda _e: self._tool_canvas.configure(scrollregion=self._tool_canvas.bbox("all")),
        )
        self._tool_canvas.create_window((0, 0), window=self._tool_inner, anchor=tk.NW)
        self._tool_canvas.configure(yscrollcommand=scroll.set)
        self._tool_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tool_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # ── Right: detail + tripback ──
        right = ttk.Frame(body)
        body.add(right, weight=3)

        conn_row = ttk.Frame(right)
        conn_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(conn_row, text="Check Max connection", command=self._check_connection).pack(side=tk.LEFT)
        ttk.Label(conn_row, text="  (optional — uses get_bridge_status)", foreground="#888").pack(side=tk.LEFT)

        self.tool_title = tk.StringVar(value="No tool loaded")
        ttk.Label(right, textvariable=self.tool_title, font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        self.desc_var = tk.StringVar(value="Pick a tool on the left → Load → edit JSON if needed → Run.")
        ttk.Label(right, textvariable=self.desc_var, wraplength=720, justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 6))
        self.risk_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.risk_var, foreground="#a50").pack(anchor=tk.W)

        ttk.Label(right, text="Arguments sent to the tool (JSON object)").pack(anchor=tk.W, pady=(8, 0))
        self.args_text = scrolledtext.ScrolledText(right, height=8, font=("Consolas", 11), wrap=tk.NONE)
        self.args_text.pack(fill=tk.X, pady=4)

        run_row = ttk.Frame(right)
        run_row.pack(fill=tk.X, pady=4)
        self.run_btn = ttk.Button(run_row, text="▶  Run this tool", command=self._run_loaded, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(run_row, text="Reset example args", command=self._reset_args).pack(side=tk.LEFT, padx=8)
        self.run_hint = ttk.Label(run_row, text="", foreground="#666")
        self.run_hint.pack(side=tk.LEFT, padx=8)

        trip_label = ttk.Label(right, text="Tripback — what the AI sees after the call", font=("Segoe UI", 10, "bold"))
        trip_label.pack(anchor=tk.W, pady=(12, 0))

        self.tripback_text = scrolledtext.ScrolledText(
            right, height=22, font=("Consolas", 10), wrap=tk.NONE, state=tk.DISABLED
        )
        self.tripback_text.pack(fill=tk.BOTH, expand=True, pady=4)

        hist_frame = ttk.LabelFrame(self.root, text="Previous calls (click to view tripback again)", padding=6)
        hist_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.hist_list = tk.Listbox(hist_frame, height=4, font=("Consolas", 9), exportselection=False)
        self.hist_list.pack(fill=tk.X)
        self.hist_list.bind("<<ListboxSelect>>", self._on_history_select)

        self._on_group_changed()

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._tool_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_group_changed(self) -> None:
        group = self.group_var.get()
        categories = ["All"]
        hint = ""
        if group != "All":
            meta = next((g for g in self.groups_meta if g["name"] == group), None)
            if meta:
                hint = meta.get("hint", "")
                categories.extend(c["name"] for c in meta.get("categories", []))
        else:
            categories.extend(sorted({t["category"] for t in self.tools}))
        self.group_hint_var.set(hint)
        self.category_var.set("All")
        self.category_cb.configure(values=categories)
        self._rebuild_tool_buttons()

    def _filtered_tools(self) -> list[dict]:
        group = self.group_var.get()
        cat = self.category_var.get()
        q = self.search_var.get().strip().lower()
        only_safe = self.only_safe_var.get()
        out = []
        for t in self.tools:
            if group != "All" and t.get("group", "Other") != group:
                continue
            if cat != "All" and t["category"] != cat:
                continue
            if only_safe and t["risk"] != "safe":
                continue
            if q and q not in t["name"].lower() and q not in t["description"].lower():
                continue
            out.append(t)
        return out

    def _grouped_sections(self, tools: list[dict]) -> list[tuple[str, str, list[dict]]]:
        """Return (group, category, tools) sections for sidebar rendering."""
        if not tools:
            return []

        group = self.group_var.get()
        cat = self.category_var.get()
        q = self.search_var.get().strip()

        # Flat list when user is searching or narrowed to one category.
        if q or cat != "All":
            return [("", cat if cat != "All" else "Results", tools)]

        sections: list[tuple[str, str, list[dict]]] = []
        if group != "All":
            by_cat: dict[str, list[dict]] = {}
            for t in tools:
                by_cat.setdefault(t["category"], []).append(t)
            for category in sorted(by_cat):
                sections.append((group, category, by_cat[category]))
            return sections

        # All groups: section by group, then category.
        by_group: dict[str, dict[str, list[dict]]] = {}
        group_order = [g["name"] for g in self.groups_meta] or sorted({t.get("group", "Other") for t in tools})
        for t in tools:
            gname = t.get("group", "Other")
            by_group.setdefault(gname, {}).setdefault(t["category"], []).append(t)
        for gname in group_order:
            if gname not in by_group:
                continue
            for category in sorted(by_group[gname]):
                sections.append((gname, category, by_group[gname][category]))
        for gname in sorted(by_group):
            if gname in group_order:
                continue
            for category in sorted(by_group[gname]):
                sections.append((gname, category, by_group[gname][category]))
        return sections

    def _add_tool_row(self, parent: ttk.Frame, tool: dict) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        name = tool["name"]
        risk = tool["risk"]
        mark = "●" if risk == "safe" else "○"
        ttk.Label(row, text=f"{mark}", width=2).pack(side=tk.LEFT)
        ttk.Button(
            row,
            text=name,
            width=28,
            command=lambda n=name: self._load_tool(n),
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="Run ▶", width=7, command=lambda n=name: self._run_named(n)).pack(side=tk.LEFT)

    def _rebuild_tool_buttons(self) -> None:
        for w in self._tool_inner.winfo_children():
            w.destroy()

        tools = self._filtered_tools()
        if not tools:
            ttk.Label(self._tool_inner, text="No tools match filters.", foreground="#888").pack(anchor=tk.W, pady=8)
            return

        sections = self._grouped_sections(tools)
        show_group_headers = self.group_var.get() == "All" and self.category_var.get() == "All"
        last_group = None

        for group_name, category_name, section_tools in sections:
            if show_group_headers and group_name and group_name != last_group:
                hdr = ttk.Label(
                    self._tool_inner,
                    text=group_name.upper(),
                    font=("Segoe UI", 9, "bold"),
                    foreground="#333",
                )
                hdr.pack(anchor=tk.W, pady=(10, 2), padx=2)
                last_group = group_name

            if category_name and (show_group_headers or self.group_var.get() != "All"):
                sub = ttk.Label(
                    self._tool_inner,
                    text=f"  {category_name} ({len(section_tools)})",
                    font=("Segoe UI", 8),
                    foreground="#666",
                )
                sub.pack(anchor=tk.W, pady=(0, 2), padx=4)

            block = ttk.Frame(self._tool_inner)
            block.pack(fill=tk.X, padx=(8, 0))
            for tool in section_tools:
                self._add_tool_row(block, tool)

    def _tool_by_name(self, name: str) -> dict | None:
        return next((t for t in self.tools if t["name"] == name), None)

    def _load_tool(self, name: str) -> None:
        tool = self._tool_by_name(name)
        if not tool:
            return
        self._selected_name = name
        self.tool_title.set(name)
        self.desc_var.set(tool["description"])
        self.risk_var.set(RISK_LABEL.get(tool["risk"], tool["risk"]))
        self._set_args(tool["example"])
        self.run_btn.config(state=tk.NORMAL)
        self.run_hint.config(text="Loaded — click Run when ready.")
        self.status_var.set(f"Loaded {name} — not run yet.")

    def _set_args(self, data: dict) -> None:
        self.args_text.delete("1.0", tk.END)
        self.args_text.insert(tk.END, json.dumps(data, indent=2))

    def _get_args(self) -> dict:
        raw = self.args_text.get("1.0", tk.END).strip()
        if not raw:
            return {}
        args = json.loads(raw)
        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object, e.g. {}")
        return args

    def _reset_args(self) -> None:
        if self._selected_name:
            tool = self._tool_by_name(self._selected_name)
            if tool:
                self._set_args(tool["example"])

    def _show_tripback(self, text: str) -> None:
        self.tripback_text.config(state=tk.NORMAL)
        self.tripback_text.delete("1.0", tk.END)
        self.tripback_text.insert(tk.END, text)
        self.tripback_text.config(state=tk.DISABLED)
        self.tripback_text.see("1.0")

    def _add_history(self, name: str, ok: bool, tripback: str) -> None:
        self._history.insert(0, (name, ok, tripback))
        self._history = self._history[:50]
        self.hist_list.delete(0, tk.END)
        for i, (n, o, _) in enumerate(self._history):
            m = "✓" if o else "✗"
            self.hist_list.insert(i, f"{m}  {n}")

    def _on_history_select(self, _event=None) -> None:
        sel = self.hist_list.curselection()
        if not sel:
            return
        _, _, tripback = self._history[sel[0]]
        self._show_tripback(tripback)

    def _get_mcp(self):
        if self._mcp is None:
            from maxmcp.server import mcp  # noqa: WPS433

            self._mcp = mcp
        return self._mcp

    def _call_tool(self, name: str, arguments: dict) -> dict:
        mcp = self._get_mcp()

        async def _run() -> dict:
            _content, meta = await mcp.call_tool(name, arguments=arguments)
            result = meta.get("result") if isinstance(meta, dict) else None
            if isinstance(result, dict) and "ok" in result:
                return result
            if isinstance(result, str):
                return json.loads(result)
            raise TypeError(f"Unexpected MCP tool result type: {type(result)!r}")

        return asyncio.run(_run())

    def _first_scene_object(self) -> str | None:
        """Name of an arbitrary scene object, or None if the scene is empty/unreachable."""
        try:
            env = self._call_tool("query_scene", {"action": "filter", "limit": 1})
        except Exception:
            return None
        if not env.get("ok"):
            return None
        result = env.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(result, dict):
            return None
        objs = result.get("objects")
        if isinstance(objs, list) and objs:
            first = objs[0]
            if isinstance(first, dict):
                return first.get("name")
            if isinstance(first, str):
                return first
        return None

    def _confirm_risk(self, tool: dict) -> bool:
        if tool["risk"] == "changes_scene":
            return messagebox.askyesno(
                "This tool changes the scene",
                f"{tool['name']}\n\nMax scene will be modified. Continue?",
                icon="warning",
            )
        if tool["risk"] == "advanced":
            return messagebox.askyesno(
                "Advanced tool",
                f"{tool['name']}\n\nMay need files, plugins, or setup. Continue?",
            )
        return True

    def _execute(self, name: str, arguments: dict) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "Wait for the current call to finish.")
            return
        tool = self._tool_by_name(name)
        if tool and not self._confirm_risk(tool):
            return

        self._busy = True
        self.run_btn.config(state=tk.DISABLED)
        self.status_var.set(f"Running {name}…")
        self.run_hint.config(text="In flight…")

        def worker() -> None:
            try:
                call_args = arguments
                if _contains_placeholder(call_args):
                    target = self._first_scene_object()
                    if target is None:
                        raise RuntimeError(
                            f"Can't fill {OBJECT_PLACEHOLDER}: no objects in the Max scene. "
                            "Create or select an object, or edit the args."
                        )
                    call_args = _substitute_placeholder(call_args, target)
                envelope = self._call_tool(name, call_args)
                tripback = format_tripback(name, envelope)
                ok = bool(envelope.get("ok"))
                summary = "OK" if ok else "FAILED"
                self.root.after(0, lambda: self._show_tripback(tripback))
                self.root.after(0, lambda: self._add_history(name, ok, tripback))
                self.root.after(
                    0,
                    lambda: self.status_var.set(f"{summary} — {name} ({envelope.get('elapsed_ms', '?')} ms)"),
                )
                self.root.after(0, lambda: self.run_hint.config(text="Done. Tripback above."))
            except json.JSONDecodeError as exc:
                tripback = format_tripback(name, {
                    "ok": False,
                    "result": None,
                    "error": {"type": "JSONDecodeError", "message": str(exc)},
                    "warnings": [],
                    "transport": None,
                    "elapsed_ms": 0,
                })
                self.root.after(0, lambda: self._show_tripback(tripback))
                self.root.after(0, lambda: self.status_var.set(f"Bad JSON in arguments — {exc}"))
            except Exception as exc:
                tripback = format_tripback(name, {
                    "ok": False,
                    "result": None,
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                    "warnings": [],
                    "transport": None,
                    "elapsed_ms": 0,
                })
                self.root.after(0, lambda: self._show_tripback(tripback))
                self.root.after(0, lambda: self._add_history(name, False, tripback))
                self.root.after(0, lambda: self.status_var.set(f"Call failed — {exc}"))
            finally:
                self.root.after(0, self._idle)

        threading.Thread(target=worker, daemon=True).start()

    def _idle(self) -> None:
        self._busy = False
        if self._selected_name:
            self.run_btn.config(state=tk.NORMAL)

    def _run_loaded(self) -> None:
        if not self._selected_name:
            messagebox.showinfo("Load a tool", "Click a tool name on the left to load it first.")
            return
        try:
            args = self._get_args()
        except (json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror("Bad JSON", str(exc))
            return
        self._execute(self._selected_name, args)

    def _run_named(self, name: str) -> None:
        """Run from list button — loads example args unless this tool is already loaded."""
        if self._selected_name != name:
            self._load_tool(name)
        try:
            args = self._get_args()
        except (json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror("Bad JSON", str(exc))
            return
        self._execute(name, args)

    def _check_connection(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_var.set("Checking connection…")

        def worker() -> None:
            try:
                envelope = self._call_tool("get_bridge_status", {})
                tripback = format_tripback("get_bridge_status", envelope)
                ok = bool(envelope.get("ok"))
                self.root.after(0, lambda: self._show_tripback(tripback))
                self.root.after(0, lambda: self._add_history("get_bridge_status", ok, tripback))
                if ok:
                    r = envelope.get("result") or {}
                    ver = r.get("maxVersion", "?") if isinstance(r, dict) else "?"
                    tr = (envelope.get("transport") or {}).get("transport", "?")
                    self.root.after(0, lambda: self.status_var.set(f"Connected — Max {ver} via {tr}"))
                else:
                    self.root.after(0, lambda: self.status_var.set("Not connected — see tripback"))
            except Exception as exc:
                tripback = format_tripback("get_bridge_status", {
                    "ok": False,
                    "result": None,
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                    "warnings": [],
                    "transport": None,
                    "elapsed_ms": 0,
                })
                self.root.after(0, lambda: self._show_tripback(tripback))
                self.root.after(0, lambda: self.status_var.set(f"Not connected — {exc}"))
            finally:
                self.root.after(0, self._idle)

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    ToolInspectorApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
