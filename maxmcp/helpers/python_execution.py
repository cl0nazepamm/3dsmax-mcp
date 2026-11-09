"""Build the embedded-Python fallback on the existing MAXScript bridge."""

from .maxscript import safe_string


# Run in a disposable namespace: user variables and helper imports do not leak
# into Max's Python __main__. The mailbox is restored by the MAXScript caller.
_RUNNER = '''
def run(code):
    import contextlib
    import io
    import json
    import traceback
    import pymxs

    stdout, stderr = io.StringIO(), io.StringIO()
    namespace = {"__name__": "__main__", "pymxs": pymxs, "rt": pymxs.runtime}
    payload = None

    def failure(exc):
        return {
            "status": "error",
            "code": "PYTHON_ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc) or type(exc).__name__,
            "details": {"traceback": traceback.format_exc()},
        }

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            compiled = compile(code, "<execute_python>", "exec")
            with pymxs.undo(True, "MCP execute_python"):
                try:
                    exec(compiled, namespace)
                    # Validate before committing the undo block. Do not silently
                    # stringify Max wrappers or emit non-JSON NaN/Infinity values.
                    value_json = json.dumps(namespace.get("result"), allow_nan=False)
                except BaseException as exc:
                    # pymxs.undo rolls back and suppresses exceptions. Capture
                    # the failure first, then let it reach the context manager.
                    payload = failure(exc)
                    raise
        except BaseException as exc:
            payload = failure(exc)

    output = {"stdout": stdout.getvalue(), "stderr": stderr.getvalue()}
    if payload is None:
        payload = {"value": json.loads(value_json), **output}
    else:
        payload["details"].update(output)
    pymxs.runtime.MCP_ExecutePython_Result = json.dumps(payload, ensure_ascii=True)
'''


def python_execution_script(code: str) -> str:
    source = _RUNNER + f"\nrun({code!r})\n"
    # Keep the MAXScript/Python boundary ASCII; some Max builds replace astral
    # Unicode characters passed directly to python.Execute.
    bootstrap = safe_string(f"exec({ascii(source)}, {{}})")
    return f'''(
    if theHold.Holding() then
        "{{\\"status\\":\\"error\\",\\"code\\":\\"USER_BUSY\\",\\"error\\":\\"Finish the active undo operation before executing Python.\\"}}"
    else (
        global MCP_ExecutePython_Result
        local previous = MCP_ExecutePython_Result
        local reply
        MCP_ExecutePython_Result = undefined
        try (
            undo "MCP execute_python" on (
                python.Execute "{bootstrap}" clearUndoBuffer:false
            )
            reply = MCP_ExecutePython_Result
        ) catch (
            MCP_ExecutePython_Result = previous
            throw()
        )
        MCP_ExecutePython_Result = previous
        if classof reply != String do throw "Python execution returned no result."
        reply
    )
)'''
