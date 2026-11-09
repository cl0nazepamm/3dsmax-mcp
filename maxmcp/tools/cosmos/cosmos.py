"""Search, download and import Chaos Cosmos assets through the installed service."""
from typing import Literal

from ...server import client, mcp
from . import _cosmos as impl


@mcp.tool()
def cosmos_search(
    query: str = "",
    kind: Literal["all", "model", "material", "hdri"] = "all",
    downloaded: bool = False,
    limit: int = 10,
    offset: int = 0,
    renderer: Literal["current", "corona", "vray"] = "current",
    thumbnails: bool = False,
    image_path: str = "",
    sizes: bool = False,
) -> dict:
    """Find compatible Cosmos assets for the selected Max instance.
    Returns asset IDs, kinds, preview URLs, file sizes and download states. Filter models,
    materials or HDRIs with kind. Renderer defaults to the current scene renderer.
    image_path (absolute .png/.jpg/.jpeg/.webp, instead of query) finds assets that look
    like the image, best match first, e.g. a reference photo or a viewport capture.
    sizes=true adds each model's bounding box: bbox_cm, size_cm and size_scene [x,y,z].
    image_path and sizes need Cosmos Browser 2026.11.04 or newer.
    thumbnails=true also saves one labeled contact sheet of the page and returns its
    file path in contact_sheet; read that file to see the assets. Tile N is the asset
    whose tile is N. Use cosmos_download to cache an asset or cosmos_import to import it.
    """
    return impl.search(client, query, kind, downloaded, limit, offset, renderer, thumbnails,
                       image_path, sizes)


@mcp.tool()
def cosmos_download(
    asset_id: str,
    wait_seconds: int = 20,
    renderer: Literal["current", "corona", "vray"] = "current",
) -> dict:
    """Download a Cosmos model, material or HDRI without importing it.
    Existing downloads are reused. Waits up to 0..60 seconds and returns ready,
    queued or downloading; call again to continue waiting. Requires Cosmos sign-in.
    The asset ID comes from cosmos_search.
    """
    return impl.download(client, asset_id, wait_seconds, renderer)


@mcp.tool()
def cosmos_import(
    asset_id: str,
    wait_seconds: int = 20,
    renderer: Literal["current", "corona", "vray"] = "current",
) -> dict:
    """Download if needed and import one Cosmos asset into the selected Max instance.
    Returns imported nodes, materials or maps and their file checks. Materials/maps
    use the renderer's normal importer; selection is preserved. A pending
    download makes no scene edit. Repeating a completed model import creates another
    instance. If import completion is unknown, inspect the scene before retrying.
    """
    return impl.import_asset(client, asset_id, wait_seconds, renderer)
