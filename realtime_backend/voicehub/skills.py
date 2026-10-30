"""Reusable, typed task templates suitable for a small local model."""
import copy
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator,FormatChecker,ValidationError,SchemaError
from .workflows.plan import validate_plan,sample
from .workflows.storage import atomic_json,read_json
from .workflows.mail import digest

def parameters(properties,required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
def text(limit=1000):return {'type':'string','minLength':1,'maxLength':limit}
PRESETS=[
 {'id':'system_report','name':'系统报告','description':'查询本机 CPU 和内存，生成 Markdown 报告。','triggers':['生成系统报告','制作系统报告'],'enabled':True,'parameters':parameters({},[]),'steps':[
  {'id':'system','tool':'system_info','arguments':{'sections':['cpu','memory']}},
  {'id':'compose','tool':'text.compose','arguments':{'instruction':'根据来源整理简洁的系统报告。','sources':[{'from':'system.data'}]}},
  {'id':'file','tool':'document.create','arguments':{'title':'系统报告','format':'md','content':{'from':'compose.content'}}}]},
 {'id':'quick_note','name':'保存笔记','description':'把提供的标题和内容保存为 Markdown 文件。','triggers':['保存笔记','记一份笔记'],'enabled':True,'parameters':parameters({'title':text(100),'content':text(4000)},['title','content']),'steps':[
  {'id':'file','tool':'document.create','arguments':{'title':{'input':'title'},'content':{'input':'content'},'format':'md'}}]},
 {'id':'document_email','name':'文档邮件','description':'生成文档和邮件草稿，等待用户确认后发送。','triggers':['执行文档邮件','文档邮件'],'enabled':True,'parameters':parameters({'title':text(100),'content':text(4000),'to':{**text(254),'format':'email'}},['title','content','to']),'steps':[
  {'id':'file','tool':'document.create','arguments':{'title':{'input':'title'},'format':'md','content':{'input':'content'}}},
  {'id':'draft','tool':'email.prepare','arguments':{'to':{'input':'to'},'subject':{'input':'title'},'body':'请查收附件。','attachments':[{'from':'file.artifact_id'}]}},
  {'id':'send','tool':'email.send','arguments':{'draft_id':{'from':'draft.draft_id'}}}]}]
class Skills:
 def __init__(self,root,workflows):
  self.path=Path(root)/'skills';self.path.mkdir(parents=True,exist_ok=True,mode=0o700);self.workflows=workflows
  for item in PRESETS:
   if not self.file(item['id']).exists():atomic_json(self.file(item['id']),item)
 def file(self,identity):
  if not isinstance(identity,str) or not re.fullmatch('[a-z][a-z0-9_]{0,39}',identity):raise ValueError('skill_id_invalid')
  return self.path/(identity+'.json')
 def get(self,identity):
  item=read_json(self.file(identity));return {**item,'version':digest(item)}
 def list(self):return [self.get(p.stem) for p in sorted(self.path.glob('*.json'))][:50]
 def bind(self,value,args):
  if isinstance(value,dict):
   if 'input' in value:
    if set(value)!={'input'} or value['input'] not in args:raise ValueError('skill_input_missing')
    return copy.deepcopy(args[value['input']])
   return {k:self.bind(v,args) for k,v in value.items()}
  if isinstance(value,list):return [self.bind(v,args) for v in value]
  return value
 def save(self,item):
  if not isinstance(item,dict):raise ValueError('skill_invalid')
  item=copy.deepcopy(item);version=item.pop('version',None)
  if set(item)!={'id','name','description','triggers','enabled','parameters','steps'} or len(json.dumps(item).encode())>30000:raise ValueError('skill_invalid')
  path=self.file(item['id'])
  if version and (not path.exists() or self.get(item['id'])['version']!=version):raise ValueError('skill_version_changed')
  if not path.exists() and len(self.list())>=50:raise ValueError('skill_limit')
  if type(item['enabled']) is not bool or not isinstance(item['name'],str) or not 1<=len(item['name'])<=60 or not isinstance(item['description'],str) or len(item['description'])>300:raise ValueError('skill_invalid')
  if not isinstance(item['triggers'],list) or not 1<=len(item['triggers'])<=5 or any(not isinstance(v,str) or not 2<=len(v)<=60 for v in item['triggers']):raise ValueError('skill_triggers_invalid')
  schema=item['parameters']
  if not isinstance(schema,dict) or set(schema)-{'type','properties','required','additionalProperties'} or schema.get('type')!='object' or schema.get('additionalProperties') is not False or not isinstance(schema.get('properties'),dict) or len(schema['properties'])>8:raise ValueError('skill_schema_invalid')
  if len(json.dumps(schema,ensure_ascii=False))>6000:raise ValueError('skill_schema_limit')
  examples={}
  for key,definition in schema['properties'].items():
   if not re.fullmatch('[a-z][a-z0-9_]{0,30}',key) or not isinstance(definition,dict) or set(definition)-{'type','minLength','maxLength','minimum','maximum','enum','description','default','format'} or definition.get('type') not in ('string','integer','number','boolean'):raise ValueError('skill_schema_invalid')
   examples[key]=definition.get('default','sample@example.com' if definition.get('format')=='email' else sample(definition['type'],definition))
  try:
   Draft202012Validator.check_schema(schema);Draft202012Validator(schema,format_checker=FormatChecker()).validate(examples)
   validate_plan({'steps':self.bind(item['steps'],examples)},self.workflows.tools())
  except (ValidationError,SchemaError,TypeError,KeyError) as exc:raise ValueError('skill_schema_invalid') from None
  atomic_json(path,item);return self.get(item['id'])
 def instantiate(self,identity,args,scope):
  item=self.get(identity)
  if not item['enabled']:raise ValueError('skill_disabled')
  if not isinstance(args,dict) or len(json.dumps(args).encode())>12000:raise ValueError('skill_arguments_invalid')
  values={key:value['default'] for key,value in item['parameters']['properties'].items() if 'default' in value};values.update(args)
  try:Draft202012Validator(item['parameters'],format_checker=FormatChecker()).validate(values)
  except ValidationError:raise ValueError('skill_arguments_invalid') from None
  plan={'steps':self.bind(item['steps'],values)};validate_plan(plan,self.workflows.tools())
  task=self.workflows.create('技能：'+item['name'],scope);self.workflows.set_plan(task['id'],plan)
  task=self.workflows._load(task['id']);task['skill']={'id':identity,'name':item['name'],'version':item['version'],'arguments':values};self.workflows._save(task)
  return self.workflows.get(task['id'])
