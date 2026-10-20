"""Stand-in for the `claude` CLI used by the tests (not a real Claude).

Mimics what `claude -p ... --mcp-config FILE` does: starts the MCP server named in the
config exactly as written there, then plays the cube through its tools, solving with
the optimal solver. FAKE_CLAUDE_MODE=exit makes it quit immediately with code 3.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.environ["PROJECT_ROOT"])

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from backend.solver import solve_optimal  # noqa: E402


async def play(server):
    params = StdioServerParameters(command=server["command"], args=server["args"],
                                   env={**os.environ, **server.get("env", {})})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        while True:
            st = (await s.call_tool("get_cube_state", {})).structuredContent
            if st["status"] != "active":
                continue
            move = solve_optimal(st["cube_facelets"])[0]
            res = (await s.call_tool("make_move", {"move": move})).structuredContent
            if res["status"] != "active":
                return


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_CLAUDE_ARGV_LOG"], "w") as f:
        json.dump({"argv": argv, "cwd": os.getcwd(), "has_api_key": "ANTHROPIC_API_KEY" in os.environ}, f)
    if os.environ.get("FAKE_CLAUDE_MODE") == "hang":
        # Never connects. Leaves a grandchild behind (like the MCP server) and records both pids.
        import subprocess
        import time
        child = subprocess.Popen(["sleep", "600"])
        with open(os.environ["FAKE_CLAUDE_PIDS"], "w") as f:
            json.dump({"claude": os.getpid(), "grandchild": child.pid}, f)
        time.sleep(600)
        return
    if os.environ.get("FAKE_CLAUDE_MODE") == "exit":
        print("fake claude: not logged in")
        sys.exit(3)
    config = json.load(open(argv[argv.index("--mcp-config") + 1]))
    asyncio.run(play(config["mcpServers"]["cuberace"]))


if __name__ == "__main__":
    main()
