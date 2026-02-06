import json
import socket
import time
from typing import Any, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 120.0


class MaxClient:
    """TCP socket client that sends commands to 3ds Max."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send_command(
        self,
        command: str,
        cmd_type: str = "maxscript",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send a command to 3ds Max via TCP and return the parsed JSON response."""
        effective_timeout = timeout or self.timeout

        request = json.dumps({
            "command": command,
            "type": cmd_type,
        })

        # Create socket and connect
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(effective_timeout)

        try:
            sock.connect((self.host, self.port))

            # Send request with newline delimiter
            sock.sendall((request + "\n").encode("utf-8"))

            # Receive response (read until newline)
            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in response_data:
                    break

            response_str = response_data.decode("utf-8").strip()

            if not response_str:
                raise RuntimeError("Empty response from 3ds Max")

            response = json.loads(response_str)

            if not response.get("success", False):
                error_msg = response.get("error", "Unknown error")
                raise RuntimeError(f"MAXScript error: {error_msg}")

            return response

        except socket.timeout:
            raise TimeoutError(
                f"3ds Max did not respond within {effective_timeout}s. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Could not connect to 3ds Max on {self.host}:{self.port}. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        finally:
            sock.close()
