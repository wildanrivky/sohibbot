"""
Tool Registry — map capability names ke MCP server tools.

Dipakai oleh factory saat membuat agent baru:
    registry = ToolRegistry.from_yaml()
    matches  = registry.select_tools(["read_file", "search", "unknown_cap"])
    # → [ToolMatch(...), ToolMatch(...), MissingTool(...)]

Registry bersifat statik (dari YAML) — tidak butuh MCP server live.
Live introspeksi tersedia via McpClient jika dibutuhkan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from el_solver.tools.mcp_client import (
    McpClient,
    McpServerConfig,
    McpServersConfig,
    McpToolInfo,
    TOOLS_DIR,
    load_servers_config,
)
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ToolMatch:
    """Capability berhasil di-match ke MCP tool."""
    capability: str
    server_name: str
    tool_name: str
    server_config: McpServerConfig


@dataclass
class MissingTool:
    """Capability tidak ditemukan di registry manapun."""
    capability: str
    suggestion: str = ""


SelectResult = Union[ToolMatch, MissingTool]


# ── Registry ──────────────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Registry tool berbasis YAML config.

    Index internal: capability → ToolMatch (first-match wins, berdasarkan
    urutan server di YAML).
    """

    def __init__(self, config: McpServersConfig) -> None:
        self._config = config
        # capability_name → ToolMatch
        self._index: dict[str, ToolMatch] = {}
        self._build_index()

    def _build_index(self) -> None:
        for server_name, server_cfg in self._config.servers.items():
            for cap in server_cfg.capabilities:
                if cap not in self._index:
                    self._index[cap] = ToolMatch(
                        capability=cap,
                        server_name=server_name,
                        tool_name=cap,
                        server_config=server_cfg,
                    )
        logger.debug(f"ToolRegistry: {len(self._index)} capabilities indexed")

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, yaml_path: Path | None = None) -> "ToolRegistry":
        """Buat registry dari tools/mcp-servers.yaml (atau path custom)."""
        config = load_servers_config(yaml_path)
        return cls(config)

    @classmethod
    def from_config(cls, config: McpServersConfig) -> "ToolRegistry":
        return cls(config)

    # ── Query methods ─────────────────────────────────────────────────────────

    def find(self, capability: str) -> ToolMatch | None:
        """Cari capability di registry. Return None kalau tidak ditemukan."""
        return self._index.get(capability)

    def select_tools(self, capabilities: list[str]) -> list[SelectResult]:
        """
        Map list capability ke tool matches.
        Capability yang tidak ditemukan jadi MissingTool.
        """
        results: list[SelectResult] = []
        for cap in capabilities:
            match = self.find(cap)
            if match:
                results.append(match)
            else:
                results.append(MissingTool(capability=cap))
                logger.warning(f"ToolRegistry: capability '{cap}' tidak ditemukan")
        return results

    def list_capabilities(self) -> list[str]:
        """Semua capability yang tersedia di registry."""
        return sorted(self._index.keys())

    def list_servers(self) -> list[str]:
        """Semua server yang terdaftar."""
        return list(self._config.servers.keys())

    def server_config(self, server_name: str) -> McpServerConfig | None:
        """Ambil config server by name."""
        return self._config.servers.get(server_name)

    def make_client(self, server_name: str) -> McpClient | None:
        """Buat McpClient untuk server by name (None kalau tidak ada)."""
        cfg = self.server_config(server_name)
        if cfg is None:
            return None
        return McpClient(server_name, cfg)

    def missing_capabilities(self, capabilities: list[str]) -> list[str]:
        """Return capability yang tidak ada di registry."""
        return [c for c in capabilities if c not in self._index]

    def __len__(self) -> int:
        return len(self._index)

    def __repr__(self) -> str:
        return (
            f"ToolRegistry(servers={len(self._config.servers)}, "
            f"capabilities={len(self._index)})"
        )
