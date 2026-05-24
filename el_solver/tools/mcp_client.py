"""
MCP Client — spawn MCP server subprocess, communicate via stdio.

Usage:
    config = load_servers_config(TOOLS_DIR / "mcp-servers.yaml")
    client = McpClient("filesystem", config.servers["filesystem"])
    tools  = client.list_tools_sync()         # tanpa harus booting server sendiri
    result = client.call_tool_sync("read_file", {"path": "/foo/bar.txt"})
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp import types as mcp_types
from pydantic import BaseModel, Field

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"


# ── Config models ─────────────────────────────────────────────────────────────

class McpServerConfig(BaseModel):
    """Konfigurasi satu MCP server dari tools/mcp-servers.yaml."""
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class McpServersConfig(BaseModel):
    """Root config dari tools/mcp-servers.yaml."""
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)


# ── Tool info ─────────────────────────────────────────────────────────────────

class McpToolInfo(BaseModel):
    """Informasi satu tool dari MCP server."""
    server_name: str
    tool_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


# ── Config loader ─────────────────────────────────────────────────────────────

def load_servers_config(yaml_path: Path | None = None) -> McpServersConfig:
    """Parse tools/mcp-servers.yaml."""
    path = yaml_path or (TOOLS_DIR / "mcp-servers.yaml")
    if not path.exists():
        logger.warning(f"mcp-servers.yaml tidak ditemukan: {path}")
        return McpServersConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return McpServersConfig.model_validate(raw)


# ── MCP Client ────────────────────────────────────────────────────────────────

class McpClient:
    """
    Client untuk satu MCP server.

    Tiap operasi (list_tools / call_tool) membuka koneksi baru ke server,
    karena server MCP di project ini dipakai on-demand bukan always-running.
    """

    def __init__(self, server_name: str, config: McpServerConfig) -> None:
        self.server_name = server_name
        self.config = config

    def _build_server_params(self) -> StdioServerParameters:
        env = {**os.environ, **{
            k: os.path.expandvars(v) for k, v in self.config.env.items()
        }}
        return StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=env,
        )

    async def list_tools(self) -> list[McpToolInfo]:
        """Hubungkan ke server, list tools, disconnect. Async version."""
        params = self._build_server_params()
        results: list[McpToolInfo] = []
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    for tool in response.tools:
                        results.append(McpToolInfo(
                            server_name=self.server_name,
                            tool_name=tool.name,
                            description=tool.description or "",
                            input_schema=tool.inputSchema or {},
                        ))
        except Exception as e:
            logger.error(f"McpClient[{self.server_name}] list_tools failed: {e}")
            raise
        return results

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Hubungkan ke server, panggil tool, return hasil. Async version."""
        params = self._build_server_params()
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    if result.isError:
                        raise RuntimeError(
                            f"MCP tool error [{tool_name}]: {result.content}"
                        )
                    return result.content
        except Exception as e:
            logger.error(
                f"McpClient[{self.server_name}] call_tool({tool_name}) failed: {e}"
            )
            raise

    def list_tools_sync(self) -> list[McpToolInfo]:
        """Sync wrapper untuk list_tools."""
        return asyncio.run(self.list_tools())

    def call_tool_sync(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Sync wrapper untuk call_tool."""
        return asyncio.run(self.call_tool(tool_name, arguments))
