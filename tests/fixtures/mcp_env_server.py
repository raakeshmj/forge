"""A stdio MCP server that reports which environment variables it can see."""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("env-probe")


@server.tool(description="Names of the environment variables visible to this server")
def env_names() -> str:
    return ",".join(sorted(os.environ))


if __name__ == "__main__":
    server.run("stdio")
