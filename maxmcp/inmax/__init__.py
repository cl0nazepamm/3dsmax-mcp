"""maxmcp inside 3ds Max's own Python: the mx API bound in-process to the native bridge.

    import maxmcp.inmax as inmax
    inmax.activate()                    # mx now calls the bridge in-process
    import maxmcp.mx as mx
    box = mx.box("Block", width=100, length=60, height=40, pos=(0, 0, 0))

run_script(code) runs a whole mx script as one undo step, rolled back if it raises.
Requires a bridge that exports mcp_bridge_call and the Python version the tool layer's
compiled dependencies were built for.
"""

from .binding import available, bridge_call
from .host import activate
from .runner import launch, run_script

__all__ = ["activate", "available", "bridge_call", "launch", "run_script"]
