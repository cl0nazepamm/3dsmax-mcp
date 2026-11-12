"""Material replacement tools for 3ds Max.

Reassigns an existing source material wherever a target material is used: on
objects and in the sub-material slots of their material trees.  Useful for
unifying split material assignments (e.g. after partial glTF conversion or a
Revit import) or swapping materials across many objects at once.
"""

from __future__ import annotations

import json as _json
from ...server import mcp, client
from ...core.coerce import DictList
from ...core.maxscript import safe_string

# Where the source can come from, in search order. source_from limits the
# search to one of them. "object" (the found_in name) is accepted for "node"
# and sent as "node", the value the native handler takes.
_SOURCE_FROM = ("node", "sub_material", "material_editor", "scene_materials", "material_library")
_SOURCE_FROM_ALIASES = {"object": "node"}
_SOURCE_PLACES = "an object, a sub-material, a Material Editor slot, the scene materials or the material library"


@mcp.tool()
def replace_material(
    source_material: str,
    target_material: str,
    preview: bool = False,
    include_sub_materials: bool = True,
    source_from: str = "",
) -> str:
    """Put source_material wherever target_material is used, on objects and in sub-material slots.

    source = material to APPLY; target = material to REPLACE. Names match
    exactly. Objects whose own material is the target are reassigned; with
    include_sub_materials (default) a target in any sub-material slot of an
    object's material tree (Multi/Sub, Blend, Shell...) is replaced in that
    slot. The source is the first material with that name found in this order:
    node (object materials), sub_material, material_editor, scene_materials,
    material_library; source_from = one of those limits the search to it, e.g.
    "material_editor" for a re-imported copy that shares its name with the
    material on objects. A slot where the source would create a reference loop
    is skipped with a reason.

    Reports replaced_objects, replaced_slots (parent material, 1-based
    slot_index, slot_name) and skipped. status: "replaced" when something
    changed, "no_match" when no material has the target name (check the
    direction), "no_objects" when it exists only in the Material Editor,
    "blocked" when every match was skipped (not reversed: do not swap),
    "unchanged" when every match already uses the source. preview=True returns
    the same report with status "preview" and changes nothing; a preview whose
    source is missing still lists the matches, with source_exists false.
    Example: source_material="Brick_A", target_material="Brick_B" -> every Brick_B use becomes Brick_A.
    """
    source_from, error = _normalize_source_from(source_from)
    if error:
        return _json.dumps({"error": error, "status": "failed"})

    if client.native_available:
        request = {"source_material": source_material, "target_material": target_material,
                   "preview": preview, "include_sub_materials": include_sub_materials}
        if source_from:
            request["source_from"] = source_from
        response = client.send_command(_json.dumps(request), cmd_type="native:replace_material")
        raw = response.get("result", "{}")
        try:
            entry = _json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if not isinstance(entry, dict):
            return raw
        if "error" not in entry:
            if source_from and entry.get("source_from") != source_from:
                _add_warning(entry, _source_from_ignored(source_from))
            if not include_sub_materials and entry.get("include_sub_materials") is not False:
                _add_warning(entry, _subs_ignored())
        _finish_entry(entry, source_material, target_material, source_from, preview)
        if entry.get("status") == "no_match":
            _add_warning(entry, _direction_warning([target_material]))
        return _json.dumps(entry)

    entry = _run_fallback([(source_material, target_material, source_from)], preview,
                          include_sub_materials, batch=False)[0]
    if entry.get("status") == "error":
        entry["status"] = "failed"
    _finish_entry(entry, source_material, target_material, source_from, preview)
    if entry.get("status") == "no_match":
        _add_warning(entry, _direction_warning([target_material]))
    return _json.dumps(entry)


@mcp.tool()
def batch_replace_materials(
    replacements: DictList,
    preview: bool = False,
    dry_run: bool = False,
    include_sub_materials: bool = True,
    source_from: str = "",
) -> str:
    """Run several replace_material entries ({source, target}) as one undoable operation.

    source = material to apply, target = material to replace, as in
    replace_material. Entries apply in order, and each one is evaluated against
    the scene as the previous entries left it, so chains such as A->B then B->C
    are deterministic and A->B then B->A does not swap (go through a temporary
    material). Each result carries replace_material's fields and status;
    preview/dry_run plans the same chain without changing the scene.
    source_from applies to every entry unless an entry has its own source_from.
    """
    preview = preview or dry_run
    entries = list(replacements)
    source_from, error = _normalize_source_from(source_from)
    if error:
        return _json.dumps({"error": f"source_from: {error}; nothing was changed", "status": "failed"})
    effective_from = []
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, dict) and entry.get("source_from") is not None:
            own, error = _normalize_source_from(entry["source_from"])
            if error:
                return _json.dumps({"error": f"replacements[{index}].source_from: {error}; nothing was changed",
                                    "status": "failed"})
            effective_from.append(own)
        else:
            effective_from.append(source_from)

    if client.native_available:
        sent = []
        for entry, entry_from in zip(entries, effective_from):
            if isinstance(entry, dict) and entry.get("source_from") is not None:
                entry = dict(entry, source_from=entry_from)
            sent.append(entry)
        request = {"replacements": sent, "preview": preview, "dry_run": dry_run,
                   "include_sub_materials": include_sub_materials}
        if source_from:
            request["source_from"] = source_from
        response = client.send_command(_json.dumps(request), cmd_type="native:batch_replace_materials")
        raw = response.get("result", "{}")
        try:
            result = _json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            return raw
        ignored = ""
        for entry, entry_from in zip(result["results"], effective_from):
            if isinstance(entry, dict):
                src, tgt = _entry_names(entry)
                if entry_from and "error" not in entry and entry.get("source_from") != entry_from:
                    ignored = ignored or entry_from
                _finish_entry(entry, src, tgt, entry_from, preview)
        if ignored:
            _add_warning(result, _source_from_ignored(ignored))
        if not include_sub_materials and result.get("include_sub_materials") is not False:
            _add_warning(result, _subs_ignored())
        _add_direction_warning(result)
        return _json.dumps(result)

    results: list[dict | None] = []
    pairs: list[tuple[str, str, str]] = []
    for entry, entry_from in zip(entries, effective_from):
        src, tgt = _entry_names(entry)
        if not src or not tgt:
            results.append({
                "source_material": src,
                "target_material": tgt,
                "status": "error",
                "error": "missing source or target",
            })
            continue
        results.append(None)
        pairs.append((src, tgt, entry_from))

    ran = iter(_run_fallback(pairs, preview, include_sub_materials, batch=True) if pairs else [])
    results = [entry if entry is not None else next(ran) for entry in results]
    for entry, entry_from in zip(results, effective_from):
        _finish_entry(entry, entry["source_material"], entry["target_material"], entry_from, preview)

    total_replaced = sum(r.get("replaced_count", 0) for r in results)
    out = {
        "results": results,
        "total_replaced": total_replaced,
        "total_skipped": sum(r.get("skipped_count", 0) for r in results),
        "error_count": sum(1 for r in results if r.get("status") == "error"),
        "status": ("preview" if preview else "replaced") if total_replaced else "no_changes",
        "include_sub_materials": include_sub_materials,
        "preview": preview,
        "dry_run": dry_run,
    }
    if preview and len(pairs) > 1:
        # The fallback cannot plan a chain: every entry is previewed against the
        # current scene. Name the entries whose real run can differ.
        if not _mark_batch_dependencies(out, entries):
            _add_warning(out, "The MAXScript fallback previews each entry against the current scene; "
                              "later entries do not see changes planned by earlier ones.")
    _add_direction_warning(out)
    return _json.dumps(out)


# ── Shared post-processing (native and fallback) ────────────────────────────

def _normalize_source_from(value) -> tuple[str, str | None]:
    """(value to send, error). Empty means "search every place in order"."""
    if value in ("", None):
        return "", None
    if isinstance(value, str):
        value = _SOURCE_FROM_ALIASES.get(value, value)
        if value in _SOURCE_FROM:
            return value, None
    return "", f"source_from must be one of {', '.join(_SOURCE_FROM)} (got {value!r})"


def _add_warning(result: dict, message: str) -> None:
    warnings = result.get("warnings")
    warnings = list(warnings) if isinstance(warnings, list) else ([warnings] if warnings else [])
    warnings.append(message)
    result["warnings"] = warnings


def _entry_names(entry) -> tuple[str, str]:
    """(source, target) of a request entry ({source, target}) or a result entry (*_material)."""
    if not isinstance(entry, dict):
        return "", ""
    names = []
    for short, long in (("source", "source_material"), ("target", "target_material")):
        value = entry.get(short)
        names.append(value if isinstance(value, str) and value else (entry.get(long) or ""))
    return names[0], names[1]


def _direction_warning(targets: list[str]) -> str:
    names = ", ".join(f"'{t}'" for t in targets)
    return (f"No material named {names} was found to replace. source = material to apply (on {_SOURCE_PLACES}), "
            "target = material to replace; if reversed, swap them. To put a material on specific objects use "
            "assign_material.")


def _add_direction_warning(result: dict) -> None:
    """One batch-level hint naming every no_match target."""
    missed = [str(r.get("target_material", "")) for r in result.get("results") or []
              if isinstance(r, dict) and r.get("status") == "no_match"]
    if missed:
        _add_warning(result, _direction_warning(missed))


def _skip_summary(skipped: list) -> str:
    by_reason: dict[str, int] = {}
    for skip in skipped:
        reason = str(skip.get("reason", "unknown")) if isinstance(skip, dict) else "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return ", ".join(f"{count} {reason}" for reason, count in by_reason.items())


def _source_from_ignored(source_from: str) -> str:
    # An older bridge without source_from searches every place.
    return (f"source_from='{source_from}' was not echoed back, so the bridge may have ignored it and used the "
            "default search order; check source.found_in and update the native bridge.")


def _subs_ignored() -> str:
    return ("include_sub_materials=False was not echoed back, so the bridge may have replaced sub-material slots "
            "too; check replaced_slots and update the native bridge.")


def _finish_entry(entry: dict, source: str, target: str, source_from: str, preview: bool) -> dict:
    """Hints added to a native or fallback entry: source hints and the blocked hint."""
    if "error" in entry:
        return entry
    if entry.get("source_exists") is False:
        if preview:
            _add_warning(entry, f"Source material '{source}' was not found on {source_from or _SOURCE_PLACES}; "
                                "a real run would fail.")
        return entry
    info = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    if int(info.get("candidates") or 1) > 1 and not source_from:
        _add_warning(entry, f"Pass source_from ({', '.join(_SOURCE_FROM)}) to pick another '{source}'.")
    elif source == target and not source_from:
        _add_warning(entry, f"source and target are both named '{source}'; the source is the first one found "
                            f"({', '.join(_SOURCE_FROM)}), so objects already using it are left alone. To apply "
                            "a re-imported copy, pass source_from (e.g. source_from=\"material_editor\").")
    if entry.get("status") == "no_objects":
        # Typical of reversed arguments: the "target" is a freshly imported material
        # that sits only in the Material Editor.
        _add_warning(entry, f"If you meant to put '{target}' onto the objects that use '{source}', the "
                            "arguments are reversed: source = material to apply, target = material to replace.")
    if entry.get("status") == "blocked":
        skipped = entry.get("skipped") if isinstance(entry.get("skipped"), list) else []
        _add_warning(entry, f"Target '{target}' was found, but every match was skipped ({_skip_summary(skipped)}). "
                            "source and target are not reversed; do not swap them.")
    return entry


# ── Batch ordering (MAXScript fallback preview) ─────────────────────────────

def _batch_dependencies(entries: list, results: list) -> list[list[int]]:
    """For each entry, the 1-based earlier entries whose result changes what it sees.

    Entry i moves target_i's users onto source_i. A later entry j is affected when its
    source is an earlier target (the source may be gone from objects), its target is an
    earlier source (it gains the users moved there) or its target is an earlier target
    (those users were already moved). Entries that errored change nothing, so they are
    not dependencies; an errored entry is a dependent only when its source was not
    found and an earlier entry removed a material of that name.
    """
    names = [_entry_names(entry) for entry in entries]
    active = [index < len(results) and isinstance(results[index], dict)
              and results[index].get("status") != "error" for index in range(len(names))]
    deps = []
    for j, (src_j, tgt_j) in enumerate(names):
        found = []
        if src_j and tgt_j:
            source_missing = (j < len(results) and isinstance(results[j], dict)
                              and results[j].get("source_exists") is False)
            for i, (src_i, tgt_i) in enumerate(names[:j]):
                if not active[i]:
                    continue
                if active[j] and (src_j == tgt_i or tgt_j == src_i or tgt_j == tgt_i):
                    found.append(i + 1)
                elif not active[j] and source_missing and src_j == tgt_i:
                    found.append(i + 1)
        deps.append(found)
    return deps


def _mark_batch_dependencies(result: dict, entries: list) -> bool:
    """Flag preview entries whose real run can differ; True when any was flagged."""
    results = result.get("results")
    if not isinstance(results, list):
        return False
    dependent = []
    for index, (deps, entry) in enumerate(zip(_batch_dependencies(entries, results), results), start=1):
        if deps:
            entry["depends_on_entries"] = deps
            dependent.append(index)
    if not dependent:
        return False
    listed = ", ".join(str(i) for i in dependent)
    _add_warning(result, f"Entries {listed} reuse a material name that an earlier entry applies or removes "
                         "(see depends_on_entries). The MAXScript fallback previews every entry against the "
                         "current scene, so a real run can differ for them: entries apply in order, so A->B then "
                         "B->A does not swap and a source an earlier entry removed may not be found.")
    return True


# ── MAXScript fallback ──────────────────────────────────────────────────────
# Used without the native bridge. The script reports raw matches and outcomes;
# status and warnings are derived here with the same rules as the native
# handler. Entries run in order inside one undo record when applied. A
# for-collect only goes straight into a local (read inside call arguments it
# failed live with "Bad free thunk local member index").

_FALLBACK_SCRIPT = r"""(
    fn mcpJs value = (
        local q = bit.intAsChar 34
        q + (MCP_Server.escapeJsonString (value as string)) + q
    )
    fn mcpField key value = ((mcpJs key) + ":" + value)
    fn mcpJoin items = (
        local out = "["
        for i = 1 to items.count do (
            if i > 1 do out += ","
            out += items[i]
        )
        out + "]"
    )
    fn mcpObject fields = (
        local out = "{"
        for i = 1 to fields.count do (
            if i > 1 do out += ","
            out += fields[i]
        )
        out + "}"
    )
    fn mcpIsMtl m = (m != undefined and superClassOf m == material)
    fn mcpSubCount m = (try (getNumSubMtls m) catch (0))
    fn mcpSubAt m i = (try (getSubMtl m i) catch (undefined))
    fn mcpSlotName m i = (
        try (getSubMtlSlotName m i localizedName:false) catch (try (getSubMtlSlotName m i) catch (""))
    )
    -- true when owner referencing src would close a reference loop.
    fn mcpLoop src owner = (try (refs.dependencyLoopTest src owner) catch (undefined))

    -- Records a hit unless only (a place, "" for any) excludes it. The place
    -- filter comes first, so a material listed in several places is found in
    -- the requested one, as in the native search.
    fn mcpKeep m place found places only = (
        if (only == "" or place == only) and (findItem found m) == 0 do (
            append found m
            append places place
        )
    )
    fn mcpFindTree m wanted found places visited only = (
        if (mcpIsMtl m) and (findItem visited m) == 0 do (
            append visited m
            for i = 1 to (mcpSubCount m) do (
                local sub = mcpSubAt m i
                if (mcpIsMtl sub) do (
                    if sub.name == wanted do mcpKeep sub "sub_material" found places only
                    mcpFindTree sub wanted found places visited only
                )
            )
        )
    )
    -- Lookup order: object materials, their sub-material trees, then Material
    -- Editor slots and their trees.
    fn mcpFindNamed wanted found places only = (
        for obj in objects where (mcpIsMtl obj.material) and obj.material.name == wanted do (
            mcpKeep obj.material "object" found places only
        )
        local visited = #()
        for obj in objects do mcpFindTree obj.material wanted found places visited only
        for i = 1 to meditMaterials.count do (
            local m = meditMaterials[i]
            if (mcpIsMtl m) and m.name == wanted do mcpKeep m "material_editor" found places only
            mcpFindTree m wanted found places visited only
        )
    )
    -- The source: mcpFindNamed's places, then the scene materials and the
    -- current material library. srcFrom (non-empty) keeps one place only.
    -- sceneMaterials is rebuilt only on request and still lists the materials
    -- of deleted objects, so it is refreshed first.
    fn mcpFindSource wanted srcFrom found places = (
        local only = if srcFrom == "node" then "object" else srcFrom
        mcpFindNamed wanted found places only
        try (UpdateSceneMaterialLib()) catch ()
        try (
            for m in sceneMaterials where (mcpIsMtl m) and m.name == wanted do (
                mcpKeep m "scene_materials" found places only
            )
        ) catch ()
        try (
            for m in currentMaterialLibrary where (mcpIsMtl m) and m.name == wanted do (
                mcpKeep m "material_library" found places only
            )
        ) catch ()
    )
    -- Slots holding a material named wanted in one object's material tree. A
    -- matched material is not descended: its slot is the one replaced.
    fn mcpCollectSlots m wanted parents indices hits visited = (
        if (mcpIsMtl m) and (findItem visited m) == 0 do (
            append visited m
            for i = 1 to (mcpSubCount m) do (
                local sub = mcpSubAt m i
                if (mcpIsMtl sub) do (
                    if sub.name == wanted then (
                        local k = 0
                        for j = 1 to parents.count while k == 0 do (
                            if parents[j] == m and indices[j] == i do k = j
                        )
                        if k == 0 do (
                            append parents m
                            append indices i
                            k = parents.count
                        )
                        append hits k
                    ) else mcpCollectSlots sub wanted parents indices hits visited
                )
            )
        )
    )

    -- A missing source fails an applied entry; a preview still lists the
    -- matches (src undefined: no loop test, nothing applied).
    fn mcpReplaceOne srcName tgtName applyChanges srcFrom includeSubs = (
        local found = #()
        local places = #()
        mcpFindSource srcName srcFrom found places
        if found.count == 0 and applyChanges then (
            mcpObject #((mcpField "source_found" "false"))
        ) else (
            local src = if found.count > 0 then found[1] else undefined
            local objMatches = #()
            local parents = #()
            local indices = #()
            local hitObjs = #()
            local objHits = #()
            for obj in objects where (mcpIsMtl obj.material) do (
                if obj.material.name == tgtName then append objMatches obj
                else if includeSubs do (
                    local hits = #()
                    mcpCollectSlots obj.material tgtName parents indices hits #()
                    if hits.count > 0 do (
                        append hitObjs obj
                        append objHits hits
                    )
                )
            )

            local replacedObjs = #()
            local changedSlots = #()
            local slotJson = #()
            local skipped = #()
            for obj in objMatches do (
                local reason = ""
                if src == undefined then reason = ""
                else if obj.material == src then reason = "already_source"
                else (
                    local looped = mcpLoop src obj
                    if looped == undefined then reason = "loop_test_failed"
                    else if looped then reason = "would_create_loop"
                    else if applyChanges do (
                        try (obj.material = src) catch ()
                        if obj.material != src do reason = "readback_mismatch"
                    )
                )
                if reason == "" then append replacedObjs obj
                else append skipped (mcpObject #(
                    (mcpField "kind" (mcpJs "object")),
                    (mcpField "object" (mcpJs obj.name)),
                    (mcpField "handle" (obj.handle as string)),
                    (mcpField "reason" (mcpJs reason))))
            )
            for k = 1 to parents.count do (
                local p = parents[k]
                local slot = indices[k]
                local prev = mcpSubAt p slot
                local users = 0
                for h in objHits do (if (findItem h k) > 0 do users += 1)
                local fields = #(
                    (mcpField "parent_material" (mcpJs p.name)),
                    (mcpField "parent_class" (mcpJs (classOf p))),
                    (mcpField "slot_index" (slot as string)),
                    (mcpField "slot_name" (mcpJs (mcpSlotName p slot))),
                    (mcpField "previous_material" (mcpJs (if prev != undefined then prev.name else ""))),
                    (mcpField "object_count" (users as string)))
                local reason = ""
                if src == undefined then reason = ""
                else if prev == src then reason = "already_source"
                else if p == src then reason = "parent_is_source"
                else (
                    local looped = mcpLoop src p
                    if looped == undefined then reason = "loop_test_failed"
                    else if looped then reason = "would_create_loop"
                    else if applyChanges do (
                        try (setSubMtl p slot src) catch ()
                        local after = mcpSubAt p slot
                        if after != src do reason = (if after == prev then "set_rejected" else "readback_mismatch")
                    )
                )
                if reason == "" then (
                    append changedSlots k
                    append slotJson (mcpObject fields)
                ) else (
                    insertItem (mcpField "kind" (mcpJs "sub_material")) fields 1
                    append fields (mcpField "reason" (mcpJs reason))
                    append skipped (mcpObject fields)
                )
            )

            local affected = for o in replacedObjs collect o
            for j = 1 to hitObjs.count do (
                local touched = false
                for k in objHits[j] while not touched do (if (findItem changedSlots k) > 0 do touched = true)
                if touched do appendIfUnique affected hitObjs[j]
            )
            local matchCount = objMatches.count + parents.count
            local targetExists = matchCount > 0
            if not targetExists do (
                local tgtFound = #()
                mcpFindNamed tgtName tgtFound #() ""
                targetExists = tgtFound.count > 0
            )
            local sourceJson = "null"
            if src != undefined do sourceJson = mcpObject #(
                (mcpField "name" (mcpJs src.name)),
                (mcpField "class" (mcpJs (classOf src))),
                (mcpField "found_in" (mcpJs places[1])))
            local replacedNames = for o in replacedObjs collect (mcpJs o.name)
            local affectedNames = for o in affected collect (mcpJs o.name)
            mcpObject #(
                (mcpField "source_found" (if src != undefined then "true" else "false")),
                (mcpField "source" sourceJson),
                (mcpField "source_candidates" (found.count as string)),
                (mcpField "match_count" (matchCount as string)),
                (mcpField "target_exists" (if targetExists then "true" else "false")),
                (mcpField "replaced_objects" (mcpJoin replacedNames)),
                (mcpField "replaced_slots" (mcpJoin slotJson)),
                (mcpField "skipped" (mcpJoin skipped)),
                (mcpField "affected_objects" (mcpJoin affectedNames)))
        )
    )

    local mcpEntries = __ENTRIES__
    local mcpResults = #()
    __RUN__
    mcpJoin mcpResults
)"""

_LOCATION_TEXT = {
    "object": "on an object",
    "sub_material": "in a sub-material slot",
    "material_editor": "in the Material Editor",
    "scene_materials": "in the scene materials",
    "material_library": "in the current material library",
}


def _fallback_script(pairs: list[tuple[str, str, str]], preview: bool, include_sub_materials: bool = True) -> str:
    entries = "#(" + ", ".join(
        f'#("{safe_string(src)}", "{safe_string(tgt)}", "{safe_string(src_from)}")' for src, tgt, src_from in pairs
    ) + ")"
    subs = "true" if include_sub_materials else "false"
    run = "for e in mcpEntries do append mcpResults (mcpReplaceOne e[1] e[2] {apply} e[3] " + subs + ")"
    if preview:
        run = run.format(apply="false")
    else:
        run = 'undo "MCP replace_material" on (' + run.format(apply="true") + ")"
    return _FALLBACK_SCRIPT.replace("__ENTRIES__", entries).replace("__RUN__", run)


def _run_fallback(pairs: list[tuple[str, str, str]], preview: bool, include_sub_materials: bool = True,
                  *, batch: bool = False) -> list[dict]:
    response = client.send_command(_fallback_script(pairs, preview, include_sub_materials))
    raw = response.get("result", "")
    try:
        parsed = _json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, list) or len(parsed) != len(pairs):
        return [
            {
                "source_material": src,
                "target_material": tgt,
                "status": "error",
                "error": "Unexpected MAXScript fallback result",
                "raw": raw,
            }
            for src, tgt, _ in pairs
        ]
    return [_fallback_entry(item, src, tgt, preview, src_from, include_sub_materials, batch=batch)
            for item, (src, tgt, src_from) in zip(parsed, pairs)]


def _replace_status(match_count: int, changed: int, skipped: list, target_exists: bool, preview: bool) -> str:
    """Status rules shared with the native handler."""
    if match_count == 0:
        return "no_objects" if target_exists else "no_match"
    if changed:
        return "preview" if preview else "replaced"
    if all(skip.get("reason") == "already_source" for skip in skipped):
        return "unchanged"
    return "blocked"


def _fallback_entry(raw: dict, source: str, target: str, preview: bool, source_from: str = "",
                    include_sub_materials: bool = True, *, batch: bool = False) -> dict:
    entry: dict = {"source_material": source, "target_material": target}
    if source_from:
        entry["source_from"] = source_from
    entry["include_sub_materials"] = include_sub_materials
    source_found = isinstance(raw, dict) and bool(raw.get("source_found"))
    # A preview without the source lists the matches (replace_material only;
    # a batch entry without its source is an error, as in the native handler).
    if not source_found and not (preview and not batch and isinstance(raw, dict) and "match_count" in raw):
        where = (f"source_from={source_from}" if source_from else
                 "objects, in sub-material slots, in the Material Editor, the scene materials or the current "
                 "material library")
        entry["status"] = "error"
        entry["source_exists"] = False
        entry["error"] = f"Source material '{source}' not found (searched {where})"
        return entry

    objects = list(raw.get("replaced_objects") or [])
    slots = list(raw.get("replaced_slots") or [])
    skipped = list(raw.get("skipped") or [])
    affected = list(raw.get("affected_objects") or [])
    match_count = int(raw.get("match_count") or 0)
    changed = len(objects) + len(slots)
    status = _replace_status(match_count, changed, skipped, bool(raw.get("target_exists")), preview)

    warnings = []
    entry["source_exists"] = source_found
    if source_found:
        source_info = dict(raw.get("source") or {})
        candidates = int(raw.get("source_candidates") or 1)
        source_info["candidates"] = candidates
        entry["source"] = source_info
        if candidates > 1:
            where = _LOCATION_TEXT.get(source_info.get("found_in", ""), "first")
            warnings.append(f"{candidates} materials are named '{source}'; used the one found {where}.")
    verb = "would be" if preview else "was"
    refused = sum(1 for skip in skipped if skip.get("reason") != "already_source")
    if status == "no_match":
        scope = "in their sub-material slots " if include_sub_materials else ""
        warnings.append(
            f"No material named '{target}' exists on objects, {scope}or in the Material Editor."
            + ("" if include_sub_materials else " Sub-material slots were not searched (include_sub_materials=False).")
        )
    elif status == "no_objects":
        warnings.append(
            f"'{target}' exists only in the Material Editor; no object or sub-material slot "
            f"uses it, so nothing {verb} replaced."
        )
    elif status == "blocked":
        warnings.append(
            f"Every match of '{target}' was skipped, so nothing {verb} replaced; "
            "see skipped for reasons."
        )
    elif status == "unchanged":
        warnings.append(f"Every match of '{target}' already uses '{source}', so nothing {verb} replaced.")
    elif refused:
        warnings.append(f"{refused} match(es) were skipped; see skipped for reasons.")

    entry.update({
        "status": status,
        "preview": preview,
        "match_count": match_count,
        "replaced_count": changed,
        "reassigned_count": len(objects),
        "replaced_objects": objects,
        "replaced_slot_count": len(slots),
        "replaced_slots": slots,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "affected_count": len(affected),
        "affected_objects": affected,
    })
    if warnings:
        entry["warnings"] = warnings
    return entry
