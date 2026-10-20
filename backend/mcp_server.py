"""MCP server that lets an agent (e.g. Claude Code) play the Rubik's Cube benchmark.

Standalone on purpose (stdlib + mcp + httpx only, no imports from this package) so it
can be launched by absolute path from any working directory:

    /path/to/.venv/bin/python /path/to/backend/mcp_server.py

It is a thin client of the benchmark backend's /api/agent/* endpoints, so the cube
you see changing in the web UI is the one the agent is moving. Environment:

    CUBE_API_URL         backend base URL (default http://127.0.0.1:8000)
    CUBE_SESSION_TOKEN   set only when the backend launches the agent itself
"""
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("CUBE_API_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("CUBE_SESSION_TOKEN")

mcp = FastMCP("cuberace")


def _headers():
    return {"X-Session-Token": TOKEN} if TOKEN else {}


def _call(method, path, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", headers=_headers(), **kw)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        return {"status": "error", "message": f"Could not reach the benchmark backend at {API}: {e}"}


@mcp.tool()
def get_cube_state() -> dict[str, Any]:
    """Get the current state of the 3x3 Rubik's Cube you are solving.

    Returns cube_facelets: a 54-character string in the order U(0-8) R(9-17) F(18-26)
    D(27-35) L(36-44) B(45-53), each face read row by row as seen from outside the cube.
    Each letter is the face the sticker belongs to, so the solved cube is
    'UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB'. Also returns your most
    recent moves, how many moves you have left, and the allowed moves.
    If status is "waiting", no puzzle is active yet: call this tool again.
    """
    return _call("GET", "/api/agent/state", params={"wait": 25}, timeout=60)


@mcp.tool()
def make_move(move: str) -> dict[str, Any]:
    """Make exactly one move on the cube and get the resulting state.

    move must be one of the 18 allowed moves: U U' U2 R R' R2 F F' F2 D D' D2 L L' L2 B B' B2.
    A letter alone turns that face 90 degrees clockwise (seen from outside), a trailing '
    means counterclockwise, and 2 means 180 degrees. An invalid move is rejected but still
    uses up one of your limited moves. Keep going until status is "solved" or
    "limit_reached".
    """
    return _call("POST", "/api/agent/move", json={"move": move}, timeout=None)


if __name__ == "__main__":
    mcp.run()
