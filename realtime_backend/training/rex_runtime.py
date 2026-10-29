"""Load the original Rex checkpoint and reuse its native token-linking classifier."""
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path
from types import MethodType

LABELS={'无需工具':'null','天气查询':'weather','新闻查询':'news','本机状态查询':'system_info'}
TO_LABEL={value:key for key,value in LABELS.items()}


def load(runtime, model_path, threads=4, max_len=192):
    model_path=Path(model_path).resolve()
    expected='5be51d21f54d10f2d7741b403058c8e310c42f7b994454cab5ca024aea0382d2'
    if hashlib.sha256((model_path/'pytorch_model.bin').read_bytes()).hexdigest()!=expected:
        raise ValueError('Unexpected base model checkpoint')
    os.environ.update(USE_TF='0',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                      HF_HOME=str(model_path.parent/'rex-hf-cache'),TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0,str(Path(runtime).resolve()));sys.path.insert(0,str(model_path))
    import torch
    import transformers
    from transformers import AutoConfig,AutoTokenizer
    from rex.model.model import RexModel
    from rex.arguments import DataArguments,UIEArguments,ModelArguments
    from rex.data_utils.data_loader import UIEDataLoader
    from rex.Trainer.trainer import RexModelTrainer
    from rex.data_utils import token_config
    torch.set_num_threads(threads);transformers.logging.set_verbosity_error()
    tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True)
    tokenizer.add_special_tokens({'additional_special_tokens':[
        token_config.PREFIX_TOKEN,token_config.TYPE_TOKEN,token_config.CLASSIFY_TOKEN,token_config.MULTI_CLASSIFY_TOKEN]})
    training=UIEArguments(output_dir='/tmp/rex-router-inference',bert_model_dir=str(model_path),
                          no_cuda=True,fp16=False,report_to=[],disable_tqdm=True)
    model=RexModel(AutoConfig.from_pretrained(model_path,local_files_only=True),training,ModelArguments())
    state=torch.load(model_path/'pytorch_model.bin',map_location='cpu',weights_only=True)
    key='plm.embeddings.position_ids'
    if key in state and key not in model.state_dict():
        if not torch.equal(state[key],torch.arange(state[key].shape[-1]).unsqueeze(0)):
            raise ValueError('Invalid constant position buffer')
        state.pop(key)
    model.load_state_dict(state,strict=True)
    data_args=DataArguments(max_len=max_len)
    loader=UIEDataLoader(data_args,tokenizer,'',-1,1,True)
    trainer=RexModelTrainer(model,training,loader.get_collate_fn(),tokenizer=tokenizer)
    trainer.rex_dl,trainer.data_args=loader,data_args
    # Original prediction_step is unchanged; move its tensors to whichever device
    # the explicit model currently uses, rather than Trainer's training scheduler.
    def prepare(self, batch):
        device=next(model.parameters()).device
        return [x.to(device) if x is not None else None for x in batch]
    trainer._prepare_inputs=MethodType(prepare,trainer)
    trainer.compute_loss_context_manager=contextlib.nullcontext
    return model,tokenizer,loader,trainer


def feature(row,tokenizer,loader,labels=None,max_length=192):
    labels=LABELS if labels is None else labels
    extraction=row.get('task')=='slots'
    if extraction:
        from voicehub.slot_normalizer import FIELDS
        text=row['text']
        mapping={():{name:[{'span':span['span'],'offset':span['offset']} for span in row['slots']
                           if span['type']==name] for name in FIELDS[row['label']]}}
    else:
        text='[CLASSIFY]'+row.get('classification_text',row['text'])
        mapping={():{name:([{'span':'[CLASSIFY]','offset':[0,10]}]
                            if code==row['label'] else []) for name,code in labels.items()}}
    char_maps,hints=loader.split_hint_by_level(mapping)
    if len(hints)!=1:raise ValueError('Expected one flat schema')
    encoded=tokenizer(hints[0],text,truncation=False,return_token_type_ids=True,return_offsets_mapping=True)
    if len(encoded['input_ids'])>max_length:raise ValueError('Training example too long')
    rows,cols,_=loader._get_labels(text,encoded['input_ids'],encoded['token_type_ids'],
                                   encoded['offset_mapping'],mapping,char_maps[0])
    if not extraction and len(set(zip(rows,cols)))!=3:raise ValueError('Expected three native gold links')
    return {'num_tokens':len(encoded['input_ids']),'input_ids':encoded['input_ids'],
            'attention_masks':encoded['attention_mask'],'token_type_ids':encoded['token_type_ids'],
            'rows':rows,'cols':cols}


def predict(model,trainer,text,labels=None):
    labels=LABELS if labels is None else labels
    import torch
    with torch.inference_mode():
        output=trainer.prediction_step(model,{'text':'[CLASSIFY]'+text,'schema':dict.fromkeys(labels)},
                                       prediction_loss_only=False,do_pred=True)
    schema=labels;found=[item['type'] for group in output for item in group]
    return schema.get(found[0],'invalid') if len(found)==1 else 'invalid'


def extract(model,trainer,tool,text):
    import torch
    from voicehub.slot_normalizer import FIELDS
    with torch.inference_mode():
        output=trainer.prediction_step(model,{'text':text,'schema':dict.fromkeys(FIELDS[tool])},
                                       prediction_loss_only=False,do_pred=True)
    unique={}
    for group in output:
        for item in group:
            key=(item['type'],item['span'],tuple(item['offset']))
            unique[key]={'type':item['type'],'span':item['span'],'offset':item['offset']}
    return list(unique.values())


def evaluate_joint(model,trainer,rows):
    import time
    from voicehub.slot_normalizer import normalize_spans
    model.eval();results=[];tp=fp=fn=0
    defaults={'default_city':'','news_source':'bbc','news_limit':3}
    for row in rows:
        started=time.monotonic();actual=predict(model,trainer,row.get('classification_text',row['text']))
        spans=[]
        if actual in ('null','invalid'):
            plan={'status':'chat' if actual=='null' else 'invalid','tool':None,'arguments':{}}
        else:
            spans=extract(model,trainer,actual,row['text'])
            try:plan=normalize_spans(actual,row['text'],spans,defaults,row.get('previous'))
            except ValueError:plan={'status':'invalid','tool':actual,'arguments':{}}
        expected=row['expected']
        correct=all(plan.get(k)==expected.get(k) for k in ('status','tool','arguments'))
        gold={(s['type'],s['span'],tuple(s['offset'])) for s in row.get('slots',[])}
        predicted={(s['type'],s['span'],tuple(s['offset'])) for s in spans}
        tp+=len(gold&predicted);fp+=len(predicted-gold);fn+=len(gold-predicted)
        results.append({**row,'actual':actual,'plan':plan,'predicted_slots':spans,'correct':correct,
                        'elapsed_ms':round((time.monotonic()-started)*1000,2)})
    null=[r for r in results if r['label']=='null']
    return {'count':len(results),'correct':sum(r['correct'] for r in results),
            'accuracy':sum(r['correct'] for r in results)/len(results),
            'classification_accuracy':sum(r['actual']==r['label'] for r in results)/len(results),
            'null_false_positive_rate':sum(r['actual']!='null' for r in null)/len(null) if null else 0,
            'slot_micro_f1':2*tp/max(1,2*tp+fp+fn),'slot_counts':{'tp':tp,'fp':fp,'fn':fn},'rows':results}


def evaluate(model,trainer,rows):
    import time
    model.eval();results=[]
    for row in rows:
        started=time.monotonic();actual=predict(model,trainer,row['text'])
        results.append({**row,'actual':actual,'correct':actual==row['label'],
                        'elapsed_ms':round((time.monotonic()-started)*1000,2)})
    null=[r for r in results if r['label']=='null']
    counts={label:{'total':sum(r['label']==label for r in results),
                   'correct':sum(r['label']==label and r['correct'] for r in results)}
            for label in TO_LABEL}
    return {'count':len(results),'correct':sum(r['correct'] for r in results),
            'accuracy':sum(r['correct'] for r in results)/len(results),
            'null_false_positive_rate':sum(r['actual']!='null' for r in null)/len(null) if null else None,
            'by_label':counts,'rows':results}
