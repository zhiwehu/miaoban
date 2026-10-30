"""Extend Rex intents/actions; never read test data or overwrite the active adapter."""
import argparse,hashlib,json,random,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--data',default='data/training/router-everyday-v4');parser.add_argument('--initial',default='data/adapters/rex-mcp-router-r16-slots-v2');parser.add_argument('--learning-rate',type=float,default=1e-4);parser.add_argument('--epochs',type=int,default=4);parser.add_argument('--smoke',action='store_true');parser.add_argument('--output',default='data/adapters/rex-everyday-v4');args=parser.parse_args()
 from training.rex_runtime import load,feature
 from training.everyday_schema import TOOL_LABELS,ACTION_LABELS
 from training.everyday_eval import evaluate
 model,tokenizer,loader,trainer=load('data/rex-runtime','data/models/rex-uninlu-chinese-base',max_len=256)
 import torch
 from torch.utils.data import DataLoader
 from safetensors.torch import load_file,save_file
 from training.lora import inject,load_adapter,adapter_state
 random.seed(20261030);torch.manual_seed(20261030);targets=inject(model,16,32,.05)
 initial=Path(args.initial);config=json.loads((initial/'adapter_config.json').read_text());assert targets==config['targets'] and config['rank']==16
 load_adapter(model,load_file(str(initial/'adapter_model.safetensors')));device='mps' if torch.backends.mps.is_available() else 'cpu';model.to(device)
 root=Path(args.data);train=[json.loads(line) for line in (root/'train.jsonl').read_text().splitlines()];valid=[json.loads(line) for line in (root/'validation.jsonl').read_text().splitlines()]
 features=[]
 for row in train:
  features.append(feature(row,tokenizer,loader,TOOL_LABELS,max_length=256))
  if row.get('action'):features.append(feature({**row,'label':row['action']},tokenizer,loader,ACTION_LABELS[row['label']],max_length=256))
  elif row['label'] in ('weather','news','system_info') and 'slots' in row:features.append(feature({**row,'task':'slots'},tokenizer,loader,max_length=256))
 batches=DataLoader(features,batch_size=16,shuffle=True,collate_fn=loader.get_collate_fn(),generator=torch.Generator().manual_seed(20261030));params=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(params,lr=args.learning_rate,weight_decay=.01)
 total=args.epochs*len(batches);scheduler=torch.optim.lr_scheduler.LambdaLR(optimizer,lambda step:min(1,(step+1)/max(1,total*.05))*max(.1,1-step/max(1,total)))
 output=Path(args.output+('-smoke' if args.smoke and args.output=='data/adapters/rex-everyday-v4' else ''))
 if (output/'adapter_model.safetensors').exists():raise FileExistsError('Use a new output directory; existing adapter is preserved')
 output.mkdir(parents=True,exist_ok=True)
 metadata={**config,'format':'miaoban-rex-everyday-v4','arguments':vars(args),'labels':TOOL_LABELS,'action_labels':ACTION_LABELS,'max_len':256,'joint_slots':True,'seed':20261030,'initial_adapter':str(initial),'data_sha256':{name:hashlib.sha256((root/(name+'.jsonl')).read_bytes()).hexdigest() for name in ('train','validation')},'training_features':len(features),'epochs':args.epochs,'device':device}
 (output/'adapter_config.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2));print('CONFIG',json.dumps(metadata,ensure_ascii=False),flush=True)
 best=-1;history=[];steps=0;started=time.monotonic()
 for epoch in range(args.epochs):
  model.train();losses=[]
  for batch in batches:
   optimizer.zero_grad(set_to_none=True);loss=model(**{key:value.to(device) for key,value in batch.items()})['loss']
   if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
   loss.backward();torch.nn.utils.clip_grad_norm_(params,1);optimizer.step();scheduler.step();steps+=1;losses.append(loss.item())
   if steps%25==0 or args.smoke:print(json.dumps({'epoch':epoch+1,'step':steps,'loss':round(loss.item(),5),'elapsed':round(time.monotonic()-started,1)}),flush=True)
   if args.smoke:print('SMOKE_BACKWARD_OK',flush=True);return
  save_file(adapter_state(model),str(output/('epoch-'+str(epoch+1)+'.safetensors')))
  validation=evaluate(model,trainer,valid);summary={key:value for key,value in validation.items() if key!='rows'};summary.update(epoch=epoch+1,loss=sum(losses)/len(losses),elapsed_seconds=round(time.monotonic()-started,1));history.append(summary)
  (output/'history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2));print('VALIDATION',json.dumps(summary,ensure_ascii=False),flush=True)
  score=summary['accuracy']-.5*summary['null_false_positive_rate']
  if score>best:
   best=score;save_file(adapter_state(model),str(output/'adapter_model.safetensors'));(output/'selection.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 print('TRAINING_COMPLETE',flush=True)
if __name__=='__main__':main()
