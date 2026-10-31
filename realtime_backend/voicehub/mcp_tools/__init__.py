"""Explicit registry for the bundled MCP tools.

Each module owns its schema, acknowledgement and async execute(http, **arguments).
"""
from . import weather, news, system_info, music, file_organizer, calendar, shopping_list, timer, item_memory, device_volume

TOOLS = {'weather':weather, 'news':news, 'system_info':system_info,'music':music,'file_organizer':file_organizer,'calendar':calendar,'shopping_list':shopping_list,'timer':timer,'item_memory':item_memory,'device_volume':device_volume}
