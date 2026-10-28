"""Read-only tool catalogue and the boundary between model plans and MCP calls."""
import json
from dataclasses import dataclass


from .mcp_tools import TOOLS

SCHEMAS = {name:module.SCHEMA for name,module in TOOLS.items()}
ACKNOWLEDGEMENTS = {name:module.ACKNOWLEDGEMENT for name,module in TOOLS.items()}


def catalogue():
    return [dict(name=name, description=description, inputSchema={
        'type':'object', 'properties':properties, 'additionalProperties':False,
        **({'required':TOOLS[name].REQUIRED} if TOOLS[name].REQUIRED else {})},
        annotations={'readOnlyHint':True, 'destructiveHint':False})
        for name, (description, properties) in SCHEMAS.items()]


def validate_arguments(name, arguments):
    if not isinstance(name, str) or name not in SCHEMAS or not isinstance(arguments, dict):
        raise ValueError('unknown_tool')
    properties = SCHEMAS[name][1]
    if set(arguments) - set(properties):
        raise ValueError('unknown_argument')
    for key, value in arguments.items():
        schema = properties[key]
        if schema['type'] == 'string':
            if not isinstance(value, str) or len(value) > schema.get('maxLength', 80):
                raise ValueError('invalid_argument')
            if 'enum' in schema and value not in schema['enum']:
                raise ValueError('invalid_argument')
        elif schema['type']=='array':
            if (not isinstance(value,list) or not schema['minItems']<=len(value)<=schema['maxItems']
                or any(not isinstance(item,str) or item not in schema['items']['enum'] for item in value)
                or len(set(value))!=len(value)):
                raise ValueError('invalid_argument')
        elif type(value) is not int or not schema['minimum'] <= value <= schema['maximum']:
            raise ValueError('invalid_argument')
    if name == 'weather' and not arguments.get('city', '').strip():
        raise ValueError('city_required')
    return arguments


def parse_plan(text, config):
    if len(text) > 4096:
        raise ValueError('plan_too_large')
    # Accept a single fenced JSON object, never eval or repair model-generated code.
    text = text.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    plan = json.loads(text)
    if not isinstance(plan, dict) or set(plan) != {'tool', 'arguments'}:
        raise ValueError('invalid_plan')
    name = plan['tool']
    if name is None and plan['arguments'] == {}:
        return None, {}
    if not isinstance(name, str) or name not in SCHEMAS or not config[name]:
        raise ValueError('tool_disabled_or_unknown')
    return name, validate_arguments(name, plan['arguments'])


@dataclass
class ToolNotice:
    name: str
    text: str


@dataclass
class ToolStatus:
    name: str
    phase: str
    elapsed_ms: int = 0
    code: str = ''
    arguments: dict | None = None
    planner: str = ''
