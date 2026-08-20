"""Optional MCP server for Claude Desktop and other MCP clients.

Install with::

    pip install echr-py[mcp]

Launch::

    python -m hudoc_py.mcp           # or: echr-py mcp

The default server exposes read-only HUDOC acquisition, segmentation,
citation, graph, thesaurus, execution-document, and local-index tools. The
complete generated inventory is documented in ``docs/mcp.md``.
"""

__all__ = ["build_server", "run"]


def __getattr__(name: str):
    """Load the optional MCP SDK only when server functionality is requested."""
    if name in __all__:
        from .server import build_server, run

        return {"build_server": build_server, "run": run}[name]
    raise AttributeError(name)
