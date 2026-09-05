"""Atomic assignment of an existing node's material, including older bridges."""
import base64


def _name_literal(name: str) -> str:
    if not isinstance(name, str) or not name or "\0" in name:
        raise ValueError("Object names must be nonempty strings without NUL")
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    return f'((dotNetClass "System.Text.Encoding").UTF8.GetString ((dotNetClass "System.Convert").FromBase64String "{encoded}"))'


def _handle(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ValueError("Handles must be positive integers below 2^63")
    return value


def existing_material_script(names, handles, source_name, source_handle) -> str:
    if not names and not handles:
        raise ValueError("names or handles is required")
    if len(names) + len(handles) > 10000:
        raise ValueError("At most 10000 material targets are supported")
    if not source_name and not source_handle:
        raise ValueError("source_name or source_handle is required")
    source = (f'getAnimByHandle {_handle(source_handle)}' if source_handle
              else f'maByName {_name_literal(source_name)}')
    crosscheck = (f'if source.name != {_name_literal(source_name)} do throw "Source handle/name mismatch"'
                  if source_name and source_handle else '')
    targets = [f'maByName {_name_literal(name)}' for name in names]
    targets += [f'getAnimByHandle {_handle(handle)}' for handle in handles]
    return f'''(
        local ownsHold = false
        try (
            if theHold.Holding() then "__BUSY__" else (
                fn maByName name = (
                    local matches = getNodeByName name exact:true all:true
                    if matches.count != 1 do throw "Object name must resolve uniquely"
                    matches[1]
                )
                local source = ({source})
                if not isValidNode source do throw "Source node is no longer valid"
                {crosscheck}
                local material = source.material
                if material == undefined do throw "Source object has no material"
                local candidates = #({','.join('('+target+')' for target in targets)})
                local targets = #()
                for node in candidates do (
                    if not isValidNode node do throw "Target node is no longer valid"
                    appendIfUnique targets node
                )
                theHold.Begin()
                ownsHold = true
                for node in targets do node.material = material
                for node in targets where node.material != material do throw "Material assignment readback failed"
                local output = stringstream ""
                format "OK|%|%|" (formattedPrint ((getHandleByAnim source) as integer64) format:"d") (formattedPrint ((getHandleByAnim material) as integer64) format:"d") to:output
                for node in targets do format "%," (formattedPrint ((getHandleByAnim node) as integer64) format:"d") to:output
                theHold.Accept "Assign existing material"
                ownsHold = false
                try (redrawViews()) catch ()
                output as string
            )
        ) catch (
            local detail = getCurrentException() as string
            if ownsHold and theHold.Holding() do theHold.Cancel()
            "__ERROR__|" + detail
        )
    )'''


def existing_material_result(raw: str) -> dict:
    if raw == "__BUSY__":
        return {"status": "error", "code": "USER_BUSY", "retryable": True,
                "error": "An undo operation is active; retry after it finishes"}
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    try:
        status, source, material, assigned = raw.split("|")
        if status != "OK":
            raise ValueError("missing success marker")
        handles = [_handle(int(h)) for h in assigned.split(",") if h]
        if not handles:
            raise ValueError("missing assigned handles")
        return {"mode": "instance", "source_handle": _handle(int(source)),
                "material_handle": _handle(int(material)), "assigned_handles": handles,
                "assignedCount": len(handles)}
    except (ValueError, TypeError) as exc:
        raise RuntimeError("No complete material assignment readback; inspect before retrying") from exc
