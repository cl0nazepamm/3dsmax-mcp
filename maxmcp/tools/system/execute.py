import json
import re

from ...core.error_hints import suggest_tools_for_maxscript
from ._precheck import PrecheckBlocked
from ._python_execution import python_execution_script
from ...core.max_client import DialogBlocked
from ...server import mcp, client


_MAXSCRIPT_ERROR_SENTINEL = "__MCP_MS_ERR__:"

# Mirrors QuietPolicy::MentionsSceneFileCommand (native/include/mcp_bridge/quiet_policy.h):
# commands whose "save changes?" prompt quiet mode would answer by discarding work.
_SCENE_FILE_COMMANDS = ("resetmaxfile", "loadmaxfile", "fetchmaxfile", "quitmax", "checkforsave",
                        "max reset file", "max file new", "max file open", "max fetch",
                        # Interface spellings, e.g. through Autodesk.Max's COREInterface.
                        "filereset", "filefetch", "loadfromfile")
_ASCII_SPACE = re.compile(r"[ \t\n\v\f\r]+")


def _normalized(script: str) -> str:
    return _ASCII_SPACE.sub(" ", script).strip(" ").lower()


def mentions_scene_file_command(script: str) -> bool:
    """True when the script's text mentions a command that can prompt to save or discard the scene."""
    text = _normalized(script)
    return any(name in text for name in _SCENE_FILE_COMMANDS)


# The same state Max's own "save changes?" prompt reads. Edits made with undo off don't set it,
# in Max itself as well, so they are not protected by the prompt or by this check.
_SAVE_REQUIRED_SCRIPT = "getSaveRequired()"


class UnsavedChangesError(RuntimeError):
    """The scene has unsaved changes and the script would reset, open, fetch or quit; nothing ran."""

    code = "UNSAVED_CHANGES"
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.details = {"script_sent": False}


def _refuse_if_unsaved(tool: str) -> None:
    # loadMaxFile never asks to save, even with prompts shown, so a modified scene would be replaced
    # without a word. No exception for scripts that call checkForSave(): the name may only sit in a
    # comment, and a script can ignore the answer. With allow_discard=True such a script still asks.
    try:
        response = client.send_command(_SAVE_REQUIRED_SCRIPT, cmd_type="maxscript")
    except DialogBlocked as blocked:
        # Only this read-only check waits on the dialog; the script itself was never sent.
        raise PrecheckBlocked(blocked, tool, "Nothing was run") from None
    if str(response.get("result", "")).strip().lower() == "true":
        raise UnsavedChangesError(
            "The scene has unsaved changes and this script resets, opens, fetches or quits, so they would "
            "be lost. Nothing was run. Ask the user: save first (saveMaxFile or manage_scene), or run it "
            "again with allow_discard=True if they agree to lose the changes.")


@mcp.tool()
def execute_maxscript(code: str = "", command: str = "", quiet: bool | None = None,
                      allow_discard: bool = False) -> str:
    """Execute arbitrary MAXScript in 3ds Max and return the result.

    Use when: no dedicated MCP tool covers the operation (custom one-offs, rare APIs).
    Not when: objects, materials, selection, transforms, modifiers, layers, or scene queries —
    prefer the matching dedicated tool instead of raw MAXScript.

    By default the script runs in Max's quiet mode: prompts such as queryBox,
    overwrite and missing-file warnings silently take their default answer.
    A script that mentions resetMaxFile, loadMaxFile, fetchMaxFile, quitMax,
    checkForSave, "max file new/open", "max reset file", "max fetch" or the
    Interface's FileReset/FileFetch/LoadFromFile runs with prompts shown, because
    quiet mode skips the save-changes prompt (checkForSave() returns true and
    resetMaxFile() discards unsaved work): a save-changes prompt then returns
    BLOCKED_BY_DIALOG instead of discarding work (#noPrompt or quiet:true arguments
    in the script still suppress it, and so discard unsaved changes: add them only
    with the user's OK). quiet=False always shows prompts. quiet=True forces quiet
    mode, except on such a script: there it needs allow_discard=True as well; pass
    both only when the user agreed to lose unsaved changes. The check reads only
    this text: for a script that runs other scripts (fileIn, include,
    python.ExecuteFile, macros.run) that may reset, open or quit, pass quiet=False.
    Answer a shown prompt with max_dialogs. Such a script is refused with
    UNSAVED_CHANGES, before anything runs, while the scene has unsaved changes
    (loadMaxFile never asks to save, even with prompts on); save first, or pass
    allow_discard=True with the user's OK (a script that calls checkForSave()
    still shows its prompt then). Edits made with undo off, as plain
    execute_maxscript edits are by default, don't mark the scene modified, in Max too.

    Always pass #noPrompt to importFile, including OBJ/FBX imports:
    importFile @"C:/assets/model.fbx" #noPrompt
    Import dialogs block execution and can cause MCP timeouts. Configure importer
    options before importing; do not override #noPrompt with quiet:false.
    """
    script = code or command
    if not script:
        return "Error: provide MAXScript code in the 'code' parameter"
    return _run_script(script, quiet, allow_discard, "execute_maxscript")


def _run_script(script: str, quiet: bool | None, allow_discard: bool, tool: str) -> str:
    file_command = mentions_scene_file_command(script)
    if file_command and not allow_discard:
        _refuse_if_unsaved(tool)
    override = None
    if file_command and quiet is None:
        # The bridge's quiet policy would pick this too; sent explicitly, it also holds on an older
        # bridge without that policy, which would run the script quiet and discard unsaved work.
        quiet, override = False, "file_command"
    elif file_command and quiet and not allow_discard:
        quiet, override = False, "file_command_quiet_ignored"  # quiet would answer "save changes?" by discarding
    response = client.send_command(script, cmd_type="maxscript",
                                   request_fields=None if quiet is None else {"quiet": bool(quiet)})
    if override and isinstance(response, dict):
        meta = response.get("meta")
        if not isinstance(meta, dict):
            meta = response["meta"] = {}
        meta["quietOverride"] = override  # reported as a warning
    result = response.get("result", "")

    if isinstance(result, str) and result.startswith(_MAXSCRIPT_ERROR_SENTINEL):
        message = result[len(_MAXSCRIPT_ERROR_SENTINEL):].strip()
        payload: dict[str, object] = {
            "status": "error",
            "error_type": "MAXScriptError",
            "error": message,
        }
        suggested = suggest_tools_for_maxscript(script)
        if suggested:
            payload["hint"] = {
                "message": (
                    "execute_maxscript is a fallback. Before retrying the same "
                    "script, consider using a dedicated MCP tool that handles "
                    "this intent directly."
                ),
                "suggested_tools": suggested,
            }
        return json.dumps(payload)

    return result


@mcp.tool()
def execute_python(code: str, allow_discard: bool = False) -> str:
    """Execute Python in 3ds Max; return stdout, stderr and a JSON-compatible value.

    Use when no dedicated tool covers the operation. Runs on Max's main thread
    with pymxs and rt (pymxs.runtime) in scope; requires bridge safe_mode=false.
    Assign `result` to return it in `value` (JSON-compatible; otherwise null).
    Each call has fresh variables; imported modules remain loaded in Max.
    Undoable scene edits form one undo step and roll back on an uncaught error.
    File I/O and other effects outside Max's undo system cannot be rolled back.
    Python errors include captured output and a traceback in error.details.
    Code that mentions a reset, open, fetch or quit command (rt.loadMaxFile,
    rt.resetMaxFile, ...; see execute_maxscript) runs with prompts shown and is
    refused with UNSAVED_CHANGES while the scene has unsaved changes; save
    first, or pass allow_discard=True with the user's OK.
    """
    if not code.strip():
        return "Error: provide Python code in the 'code' parameter"
    return _run_script(python_execution_script(code), None, allow_discard, "execute_python")
