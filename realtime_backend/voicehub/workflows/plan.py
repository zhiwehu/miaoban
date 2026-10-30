"""Validate finite tool DAGs and resolve explicit data references without eval."""
import copy
import json
import re
from jsonschema import Draft202012Validator,ValidationError
ID=re.compile(r'[a-z][a-z0-9_]{0,23}')
FIELD=re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}')

def reference(value):
 if not isinstance(value,dict) or 'from' not in value:return None
 if set(value)!={'from'} or not isinstance(value['from'],str):raise ValueError('workflow_reference_invalid')
 parts=value['from'].split('.')
 if not 2<=len(parts)<=6 or not ID.fullmatch(parts[0]) or any(not FIELD.fullmatch(p) for p in parts[1:]):raise ValueError('workflow_reference_invalid')
 return parts

def resolve_arguments(value,results,depth=0):
 if depth>10:raise ValueError('workflow_argument_depth')
 path=reference(value)
 if path:
  actual=results
  for part in path:
   if not isinstance(actual,dict) or part not in actual:raise ValueError('workflow_result_missing')
   actual=actual[part]
  return copy.deepcopy(actual)
 if isinstance(value,dict):return {k:resolve_arguments(v,results,depth+1) for k,v in value.items()}
 if isinstance(value,list):return [resolve_arguments(v,results,depth+1) for v in value]
 return value

def sample(kind,schema):
 if schema.get('enum'):return schema['enum'][0]
 if kind=='string':
  if schema.get('pattern')=='^[a-f0-9]{32}$':return 'a'*32
  if schema.get('pattern')==r'^[^\s@]+@[^\s@]+\.[^\s@]+$':return 'sample@example.com'
  return 'x'*max(1,schema.get('minLength',1))
 if kind=='integer':return schema.get('minimum',0)
 if kind=='number':return schema.get('minimum',0)
 if kind=='boolean':return False
 if kind=='array':return [sample(schema.get('items',{}).get('type','string'),schema.get('items',{})) for _ in range(schema.get('minItems',0))]
 if kind=='object':return {}
 return None

def validate_arguments(tool,args):
 if not isinstance(args,dict) or len(json.dumps(args,ensure_ascii=False).encode())>20000:raise ValueError('workflow_arguments_limit')
 try:Draft202012Validator(tool['inputSchema']).validate(args)
 except ValidationError:raise ValueError('workflow_arguments_invalid') from None
 return args

def validate_plan(plan,tools):
 if not isinstance(plan,dict) or set(plan)!={'steps'} or len(json.dumps(plan,ensure_ascii=False).encode())>32000:raise ValueError('workflow_plan_invalid')
 steps=plan['steps'];registry={t['name']:t for t in tools}
 if not isinstance(steps,list) or not 1<=len(steps)<=8:raise ValueError('workflow_step_limit')
 indexed={}
 for step in steps:
  if not isinstance(step,dict) or set(step)!={'id','tool','arguments'} or not isinstance(step['id'],str) or not ID.fullmatch(step['id']) or step['id'] in indexed:raise ValueError('workflow_step_invalid')
  if not isinstance(step['tool'],str) or step['tool'] not in registry:raise ValueError('workflow_tool_unavailable')
  if not isinstance(step['arguments'],dict):raise ValueError('workflow_arguments_invalid')
  indexed[step['id']]=copy.deepcopy(step)
 dependencies={key:set() for key in indexed}
 def substitute(value,schema,owner,depth=0):
  if depth>10:raise ValueError('workflow_argument_depth')
  path=reference(value)
  if path:
   if path[0] not in indexed:raise ValueError('workflow_reference_unknown_step')
   source=registry[indexed[path[0]]['tool']];outputs=source.get('outputs',{})
   if path[1] not in outputs:raise ValueError('workflow_reference_unknown_output')
   dependencies[owner].add(path[0]);kind=outputs[path[1]] if len(path)==2 else schema.get('type','string')
   expected=schema.get('type')
   if expected and kind!='any' and expected!=kind and not (expected=='number' and kind=='integer'):raise ValueError('workflow_reference_type')
   return sample(kind if kind!='any' else expected,schema)
  if isinstance(value,dict):return {k:substitute(v,schema.get('properties',{}).get(k,{}),owner,depth+1) for k,v in value.items()}
  if isinstance(value,list):
   if len(value)>100:raise ValueError('workflow_argument_limit')
   return [substitute(v,schema.get('items',{}),owner,depth+1) for v in value]
  return value
 for identity,step in indexed.items():
  tool=registry[step['tool']];validate_arguments(tool,substitute(step['arguments'],tool['inputSchema'],identity))
 ordered=[];remaining=list(indexed)
 while remaining:
  ready=[key for key in remaining if dependencies[key].issubset({s['id'] for s in ordered})]
  if not ready:raise ValueError('workflow_cycle')
  for key in ready:ordered.append(indexed[key]);remaining.remove(key)
 return ordered
