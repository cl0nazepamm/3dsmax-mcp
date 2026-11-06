"""Cosmos orchestration; network I/O remains in the external MCP process."""
from __future__ import annotations

import io
import json
import math
import re
import tempfile
import threading
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..max_client import MaxClient

from .cosmos_client import (DEFAULT_BASE_URL, Cosmos, CosmosError,
                            asset_id as normalize_id, runtime_base_url)

_KIND_TAGS = {"all": (), "model": (1,), "material": (38,), "hdri": (427,)}
_STATES = {1: "unavailable", 2: "not_downloaded", 3: "ready",
           4: "downloading", 5: "unavailable", 6: "ready", 7: "creating"}
_import_lock = threading.Lock()
_download_jobs: dict[tuple[str, str], float] = {}
_download_lock = threading.Lock()


def _max(client, command, cmd_type="maxscript"):
    response = client.send_command(command, cmd_type=cmd_type)
    result = response.get("result", {})
    return json.loads(result) if isinstance(result, str) else result


def _context(client, renderer):
    if renderer not in ("current", "corona", "vray"):
        raise ValueError("renderer must be current, corona or vray")
    # Also binds the existing MaxClient to its normal selected/default instance.
    current = _max(client, '(local s=classof renderers.current as string; '
                   '"{\\"renderer\\":\\"" + (MCP_Server.escapeJsonString s) + "\\"}")')
    target = client.get_selected_max_instance()
    if not target.get("available") or not target.get("target_pid"):
        raise CosmosError("Select a running Max instance first.", "COSMOS_NO_IMPORTER")
    selected_renderer = re.sub(r"[^a-z0-9]", "", current["renderer"].lower())
    wanted = renderer
    if renderer == "current":
        wanted = ("corona" if selected_renderer.startswith("corona") else
                  "vray" if selected_renderer.startswith("vray") else "")
    if not wanted:
        raise CosmosError("Current renderer has no Cosmos integration; choose corona or vray.",
                          "COSMOS_NO_IMPORTER")
    # A renderer can start a second Cosmos while another one holds the default
    # port. runtime.json then names the new instance, but hosts may stay
    # registered with the old one, so try the default port as well.
    urls, errors, answered = [], [], False
    try:
        urls.append(runtime_base_url())
    except CosmosError as exc:
        errors.append(exc)
    if DEFAULT_BASE_URL not in urls:
        urls.append(DEFAULT_BASE_URL)
    for url in urls:
        service = Cosmos(base_url=url, timeout=15)
        try:
            importers = service.importers()
        except CosmosError as exc:
            errors.append(exc)
            continue
        answered = True
        matches = [i for i in importers
                   if i["pid"] == target["target_pid"]
                   and re.sub(r"[^a-z0-9]", "", i["renderer"].lower()) == wanted]
        if len(matches) > 1:
            raise CosmosError("Cosmos at %s lists %d %s importers for Max PID %s; restart Cosmos."
                              % (url, len(matches), wanted, target["target_pid"]), "COSMOS_NO_IMPORTER")
        if matches:
            service.importer_id = matches[0]["id"]
            return service, matches[0]
    if not answered:
        raise errors[0]
    raise CosmosError("Expected one %s Cosmos importer for Max PID %s; open Cosmos in that renderer."
                      % (wanted, target["target_pid"]), "COSMOS_NO_IMPORTER")


def _summary(service, asset):
    state = _STATES.get(asset["availability"], "unknown")
    return {"asset_id": asset["id"], "name": asset["name"], "kind": asset["kind"],
            "revision": asset["revision"], "state": state, "size_bytes": asset["size"],
            "thumbnail": (service.base_url + "/api/v1/package" + asset["preview_path"]
                          if asset.get("preview_path") else None)}


# Cosmos only publishes 1000 px previews (no thumbnail size exists on the service, the
# CDN or disk), so contact sheets downscale them here, off Max's main thread.
_SHEET_DIR = Path(tempfile.gettempdir()) / "3dsmax-mcp" / "cosmos"
_TILE = 192
_LABEL = 34
_SHEET_COLUMNS = 5
_PREVIEW_LIMIT = 16 * 1024 * 1024
_tiles: OrderedDict = OrderedDict()
_tiles_lock = threading.Lock()


def _preview_tile(url):
    from PIL import Image

    with _tiles_lock:
        if url in _tiles:
            _tiles.move_to_end(url)
            return _tiles[url]
    with urllib.request.urlopen(url, timeout=10) as response:
        data = response.read(_PREVIEW_LIMIT + 1)
    if len(data) > _PREVIEW_LIMIT:
        raise ValueError("preview exceeds the size limit")
    with Image.open(io.BytesIO(data)) as image:
        image.thumbnail((_TILE, _TILE), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", image.size, (43, 43, 43))
        rgba = image.convert("RGBA")
        tile.paste(rgba, mask=rgba.getchannel("A"))
    with _tiles_lock:
        _tiles[url] = tile
        while len(_tiles) > 256:
            _tiles.popitem(last=False)
    return tile


def _font(size):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "..."


def _contact_sheet(items):
    """Compose one labeled JPEG; tile N is items[N-1]. Missing previews get a placeholder."""
    from PIL import Image, ImageDraw

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        futures = [pool.submit(_preview_tile, item["thumbnail"]) if item.get("thumbnail") else None
                   for item in items]
    tiles, missing = [], []
    for item, future in zip(items, futures):
        try:
            tiles.append(future.result() if future else None)
        except Exception:
            tiles.append(None)
        if tiles[-1] is None:
            missing.append(item["asset_id"])
    columns = min(_SHEET_COLUMNS, len(items))
    rows = math.ceil(len(items) / columns)
    cell_h = _TILE + _LABEL
    sheet = Image.new("RGB", (columns * _TILE, rows * cell_h), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    number_font, name_font = _font(15), _font(12)
    for index, (item, tile) in enumerate(zip(items, tiles)):
        x, y = (index % columns) * _TILE, (index // columns) * cell_h
        if tile is None:
            draw.rectangle((x + 4, y + 4, x + _TILE - 5, y + _TILE - 5), outline=(90, 90, 90))
            draw.text((x + _TILE // 2, y + _TILE // 2), "no preview", fill=(140, 140, 140),
                      font=name_font, anchor="mm")
        else:
            sheet.paste(tile, (x + (_TILE - tile.width) // 2, y + (_TILE - tile.height) // 2))
        number = str(index + 1)
        draw.text((x + 6, y + _TILE + 3), number, fill=(255, 255, 255), font=number_font)
        left = x + 12 + int(draw.textlength(number, font=number_font))
        draw.text((left, y + _TILE + 6), _fit(draw, item["name"], name_font, x + _TILE - 6 - left),
                  fill=(200, 200, 200), font=name_font)
    _SHEET_DIR.mkdir(parents=True, exist_ok=True)
    path = _SHEET_DIR / ("cosmos_search_%d.jpg" % time.time_ns())
    sheet.save(path, "JPEG", quality=85)
    return {"type": "image_file", "file": str(path), "mime_type": "image/jpeg",
            "width": sheet.width, "height": sheet.height, "size_bytes": path.stat().st_size,
            "tiles": len(items), "missing_previews": missing}


def search(client, query, kind, downloaded, limit, offset, renderer, thumbnails=False):
    if kind not in _KIND_TAGS:
        raise ValueError("kind must be all, model, material or hdri")
    if len(query) > 500:
        raise ValueError("query must be at most 500 characters")
    service, importer = _context(client, renderer)
    result = service.search(query, limit=limit, offset=offset,
                            tag_ids=_KIND_TAGS[kind], downloaded=downloaded)
    items = [_summary(service, a) for a in result["items"]]
    out = {"renderer": importer["renderer"], "max_pid": importer["pid"], "assets": items,
           "offset": offset, "next_offset": offset + len(items) if len(items) == limit else None}
    if thumbnails and items:
        for tile, item in enumerate(items, 1):
            item["tile"] = tile
        try:
            out["contact_sheet"] = _contact_sheet(items)
        except Exception as exc:
            # The search result stands on its own; report the sheet failure next to it.
            out["contact_sheet_error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def _download(service, package_id, wait_seconds):
    if not isinstance(wait_seconds, int) or isinstance(wait_seconds, bool) or not 0 <= wait_seconds <= 60:
        raise ValueError("wait_seconds must be an integer from 0 to 60")
    package_id = normalize_id(package_id)
    asset = service.asset(package_id)
    key = (service.importer_id, package_id)
    if asset["availability"] in (3, 6):
        with _download_lock:
            _download_jobs.pop(key, None)
        return asset
    if asset["availability"] in (1, 5):
        raise CosmosError("This asset is unavailable to the current Cosmos account/importer.",
                          "COSMOS_ASSET_UNAVAILABLE")
    if asset["availability"] not in (2, 4):
        raise CosmosError("Asset is not downloadable in its current state.", "COSMOS_ASSET_UNAVAILABLE")
    with _download_lock:
        submitted = _download_jobs.get(key)
        if submitted is not None and time.monotonic() - submitted > 180:
            _download_jobs.pop(key, None)
            raise CosmosError("Cosmos did not finish the download within 180 seconds. Check the asset in Cosmos.",
                              "COSMOS_DOWNLOAD_FAILED")
        # Do not enqueue another copy while a prior request is being scheduled.
        if asset["availability"] == 2 and submitted is None:
            service.download(package_id, asset["revision"])
            submitted = _download_jobs[key] = time.monotonic()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        asset = service.asset(package_id)
        if asset["availability"] in (3, 6):
            with _download_lock:
                _download_jobs.pop(key, None)
            return asset
        if asset["availability"] in (1, 5):
            with _download_lock:
                _download_jobs.pop(key, None)
            service.require_sign_in()
            raise CosmosError("Cosmos download did not complete. Check this asset's download error in Cosmos.",
                              "COSMOS_DOWNLOAD_FAILED")
    return asset


def download(client, package_id, wait_seconds, renderer):
    service, importer = _context(client, renderer)
    asset = _download(service, package_id, wait_seconds)
    result = _summary(service, asset)
    if result["state"] not in ("ready", "unavailable"):
        result["state"] = "queued" if asset["availability"] == 2 else "downloading"
        result["next"] = "Call cosmos_download with this asset_id to continue waiting."
    return result


_ASSET_SNAPSHOT = "(\n fn compactName value = (\n  (dotNetClass \"System.Text.RegularExpressions.Regex\").Replace (toLower(value as string)) \"[^a-z0-9]\" \"\"\n )\n local key=\"__KEY__\"\n local aid=\"__ASSET__\"\n fn matchesName value key = ((findString (compactName value) key)!=undefined)\n fn fileOf value = (\n  local f=try(value.filename as string)catch(\"\")\n  if f==\"\" do f=try(value.HDRIMapName as string)catch(\"\")\n  f\n )\n fn quoteJSON value = (\"\\\"\" + (MCP_Server.escapeJsonString(value as string)) + \"\\\"\")\n fn refJSON value isNode = (\n  local h=if isNode then value.handle as string else (getHandleByAnim value) as string\n  \"{\\\"handle\\\":\"+(quoteJSON h)+\",\\\"name\\\":\"+(quoteJSON(try(value.name)catch(\"\")))+\",\\\"class\\\":\"+(quoteJSON(classof value))+\",\\\"filename\\\":\"+(quoteJSON(fileOf value))+\"}\"\n )\n fn joinJSON values = (\n  local s=\"[\"\n  for i=1 to values.count do (if i>1 do s+=\",\"; s+=values[i])\n  s+\"]\"\n )\n local nodes=for n in objects where (try(n.cosmosAssetId==aid)catch(false)) collect n\n local mats=#()\n for n in nodes where n.material!=undefined do appendIfUnique mats n.material\n for c in material.classes do try(\n  for m in (getClassInstances c processAllAnimatables:true) where (matchesName m.name key) do appendIfUnique mats m\n )catch()\n local maps=#()\n fn visitMaps value maps visited depth = (\n  if value!=undefined and depth<12 and findItem visited value==0 do (\n   append visited value\n   if superclassof value==textureMap do appendIfUnique maps value\n   for i=1 to (try(getNumSubTexmaps value)catch(0)) do visitMaps (getSubTexmap value i) maps visited (depth+1)\n   for i=1 to (try(getNumSubMtls value)catch(0)) do visitMaps (getSubMtl value i) maps visited (depth+1)\n  )\n )\n local visited=#()\n for m in mats do visitMaps m maps visited 0\n for c in textureMap.classes where (matchPattern (c as string) pattern:\"*bitmap*\" or matchPattern (c as string) pattern:\"*hdri*\") do try(\n  for m in (getClassInstances c processAllAnimatables:true) where ((matchesName m.name key) or (matchesName (fileOf m) key)) do appendIfUnique maps m\n )catch()\n \"{\\\"nodes\\\":\"+(joinJSON(for n in nodes collect(refJSON n true)))+\",\\\"materials\\\":\"+(joinJSON(for m in mats collect(refJSON m false)))+\",\\\"maps\\\":\"+(joinJSON(for m in maps collect(refJSON m false)))+\"}\"\n)"


def _asset_snapshot(client, asset):
    # Match this asset only. Do not snapshot unrelated scene/material metadata.
    key = re.sub(r"[^a-z0-9]", "", asset["name"].lower())
    if len(key) < 4:
        raise CosmosError("Asset has no usable identity for import verification.")
    script = _ASSET_SNAPSHOT.replace("__KEY__", key).replace("__ASSET__", normalize_id(asset["id"]))
    return _max(client, script)


def _selection(client, handles=None):
    if handles is None:
        return _max(client, '(local s="["; local a=selection as array; '
                    'for i=1 to a.count do (if i>1 do s+=","; s+=(a[i].handle as string)); s+"]")')
    values = ",".join(str(int(h)) for h in handles)
    client.send_command('(if theHold.Holding() then throw "USER_BUSY" else '
                        '(select (for h in #(' + values + ') '
                        'where (maxOps.getNodeByHandle h)!=undefined collect(maxOps.getNodeByHandle h)); "OK"))')



def _wait_import(client, asset, before, timeout=30):
    """Cosmos acknowledges dispatch before the host finishes creating resources."""
    expected = {"model": "nodes", "material": "materials", "hdri": "maps"}.get(asset["kind"])
    old = {item["handle"] for item in before.get(expected, [])}
    deadline = time.monotonic() + timeout
    while True:
        after = _asset_snapshot(client, asset)
        if expected and any(item["handle"] not in old for item in after[expected]):
            return after
        if time.monotonic() >= deadline:
            return after
        time.sleep(0.25)


def import_asset(client, package_id, wait_seconds, renderer):
    if not _import_lock.acquire(blocking=False):
        raise CosmosError("A Cosmos import is already in progress.", "USER_BUSY", True)
    operation_client = None
    try:
        service, importer = _context(client, renderer)
        asset = _download(service, package_id, wait_seconds)
        result = _summary(service, asset)
        if result["state"] != "ready":
            result.update(state="queued" if asset["availability"] == 2 else "downloading", next="Call cosmos_import with this asset_id when ready.")
            return result
        operation_client = MaxClient()
        operation_client.select_max_instance(importer["pid"])
        client = operation_client
        before = _asset_snapshot(client, asset)
        selected = _selection(client)
        if client.get_selected_max_instance().get("target_pid") != importer["pid"]:
            raise CosmosError("Selected Max instance changed before import.", "COSMOS_TARGET_CHANGED")
        _selection(client, [])
        try:
            # Native Chaos importer owns its normal host undo/import behavior.
            # Never hold Max's main thread or a theHold transaction across this RPC.
            try:
                service.timeout = 30
                service.import_asset(asset["id"], asset["revision"])
                after = _wait_import(client, asset, before)
            except Exception as exc:
                return {**result, "state": "import_unknown", "repeat_safe": False,
                        "message": str(exc), "next": "Inspect the requested asset in Max before retrying."}
        finally:
            _selection(client, selected)
        resources = {}
        for kind in ("nodes", "materials", "maps"):
            old = {x["handle"] for x in before[kind]}
            resources[kind] = []
            for item in after[kind]:
                item = dict(item)
                item["created"] = item["handle"] not in old
                if kind == "nodes":
                    item["node_ref"] = {"handle": int(item["handle"]), "name": item["name"]}
                filename = item.get("filename")
                if filename:
                    item["file_exists"] = Path(filename).is_file()
                else:
                    item.pop("filename", None)
                resources[kind].append(item)
        expected = {"model": "nodes", "material": "materials", "hdri": "maps"}.get(asset["kind"])
        observed = bool(expected) and any(item["created"] for item in resources[expected])
        return {**result, "state": "imported" if observed else "imported_unverified",
                "renderer": importer["renderer"], "max_pid": importer["pid"],
                "repeat_safe": False, **resources}
    finally:
        try:
            if operation_client is not None:
                operation_client.release_max_instance()
        finally:
            _import_lock.release()
