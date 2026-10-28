"""Explicit registry for the bundled read-only MCP tools.

Each module owns its schema, acknowledgement and async execute(http, **arguments).
"""
from . import weather, news, system_info

TOOLS = {'weather':weather, 'news':news, 'system_info':system_info}
