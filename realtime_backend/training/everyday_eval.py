"""Decision/action evaluation, with old slot checks kept separate from synthetic routing."""
import time
from .rex_runtime import predict,extract
from .everyday_schema import TOOL_LABELS,ACTION_LABELS
from voicehub.slot_normalizer import normalize_spans

def evaluate(model,trainer,rows,engine=None):
 model.eval()
 results=[]
 for row in rows:
  start=time.monotonic();text=row.get('classification_text',row['text']);tool=predict(model,trainer,text,TOOL_LABELS);action=None
  if tool in ACTION_LABELS:action=predict(model,trainer,text,ACTION_LABELS[tool])
  correct=tool==row['label'] and (not row.get('action') or action==row['action']);plan_correct=None
  if row.get('expected') and row['label'] in ('weather','news','system_info','null'):
   plan={'status':'chat','tool':None,'arguments':{}} if tool=='null' else {}
   if tool in ('weather','news','system_info'):
    spans=engine.extract_slots(tool,row['text']) if engine is not None else extract(model,trainer,tool,row['text'])
    try:plan=normalize_spans(tool,row['text'],spans,{'default_city':'','news_source':'bbc','news_limit':3},row.get('previous'))
    except ValueError:plan={}
   plan_correct=all(plan.get(key)==row['expected'].get(key) for key in ('status','tool','arguments'));correct=correct and plan_correct
  results.append({**row,'actual_tool':tool,'actual_action':action,'correct':correct,'plan_correct':plan_correct,'elapsed_ms':round((time.monotonic()-start)*1000,2)})
 null=[r for r in results if r['label']=='null'];new=[r for r in results if 'action' in r];core=[r for r in results if r['plan_correct'] is not None]
 return {'count':len(results),'correct':sum(r['correct'] for r in results),'accuracy':sum(r['correct'] for r in results)/len(results),'intent_accuracy':sum(r['actual_tool']==r['label'] for r in results)/len(results),'new_decision_action_accuracy':sum(r['correct'] for r in new)/max(1,len(new)),'core_plan_accuracy':sum(r['plan_correct'] for r in core)/max(1,len(core)),'null_false_positive_rate':sum(r['actual_tool']!='null' for r in null)/max(1,len(null)),
  'by_tool':{label:{'count':sum(r['label']==label for r in results),'correct':sum(r['label']==label and r['correct'] for r in results)} for label in TOOL_LABELS.values()},'rows':results}
