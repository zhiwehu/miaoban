"""Train rank-16 native Rex LoRA; choose checkpoints using validation only."""
import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',default='data/rex-runtime')
    parser.add_argument('--model',default='data/models/rex-uninlu-chinese-base')
    parser.add_argument('--data',default='data/training/router-v1')
    parser.add_argument('--output',default='data/adapters/rex-mcp-router-r16-v1')
    parser.add_argument('--device',choices=['mps','cpu'],default='mps')
    parser.add_argument('--epochs',type=int,default=6)
    parser.add_argument('--batch-size',type=int,default=16)
    parser.add_argument('--learning-rate',type=float,default=2e-4)
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--joint',action='store_true',help='Train classification and native entity extraction together')
    args=parser.parse_args()
    from training.rex_runtime import load,feature,evaluate,LABELS
    model,tokenizer,loader,trainer=load(args.runtime,args.model)
    import torch
    from torch.utils.data import DataLoader
    from safetensors.torch import save_file
    from training.lora import inject,adapter_state
    random.seed(20261028);torch.manual_seed(20261028)
    if args.device=='mps' and not torch.backends.mps.is_available():raise RuntimeError('MPS unavailable')
    targets=inject(model,rank=16,alpha=32,dropout=.05)
    model.to(args.device)
    train=[json.loads(line) for line in (Path(args.data)/'train.jsonl').read_text().splitlines()]
    valid=[json.loads(line) for line in (Path(args.data)/'validation.jsonl').read_text().splitlines()]
    # Test and regression files are intentionally not read by the training process.
    features=[]
    for row in train:
        features.append(feature(row,tokenizer,loader))
        if args.joint and row['label']!='null' and 'slots' in row:
            features.append(feature({**row,'task':'slots'},tokenizer,loader))
    data=DataLoader(features,batch_size=args.batch_size,shuffle=True,collate_fn=loader.get_collate_fn(),
                    generator=torch.Generator().manual_seed(20261028))
    params=[p for p in model.parameters() if p.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=args.learning_rate,weight_decay=.01)
    steps=args.epochs*len(data);warmup=max(1,int(steps*.05))
    scheduler=torch.optim.lr_scheduler.LambdaLR(optimizer,lambda step:
        min(1.,(step+1)/warmup)*max(.1,(steps-step)/max(1,steps-warmup)))
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    config={'format':'miaoban-rex-lora-slots-v2' if args.joint else 'miaoban-rex-lora-v1','base_model':'iic/nlp_deberta_rex-uninlu_chinese-base',
        'base_weight_sha256':'5be51d21f54d10f2d7741b403058c8e310c42f7b994454cab5ca024aea0382d2',
        'rank':16,'alpha':32,'dropout':.05,'targets':targets,'labels':LABELS,
        'loss':'original_rex_circle_loss_full_matrix','seed':20261028,
        'trainable_parameters':sum(p.numel() for p in params),'arguments':vars(args),
        'joint_slots':args.joint,
        'data_sha256':{split:hashlib.sha256((Path(args.data)/(split+'.jsonl')).read_bytes()).hexdigest()
                       for split in ('train','validation')}}
    (output/'adapter_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
    print('CONFIG',json.dumps(config,ensure_ascii=False),flush=True)
    best_score=(-1.,-1.);history=[];global_step=0;started=time.monotonic()
    for epoch in range(args.epochs):
        model.train();losses=[]
        for batch in data:
            before=time.monotonic();optimizer.zero_grad(set_to_none=True)
            batch={k:v.to(args.device) for k,v in batch.items()}
            loss=model(**batch)['loss']
            if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
            loss.backward();torch.nn.utils.clip_grad_norm_(params,1.)
            optimizer.step();scheduler.step()
            value=loss.item();losses.append(value);global_step+=1
            if global_step%10==0 or args.smoke:
                print(json.dumps({'epoch':epoch+1,'step':global_step,'loss':round(value,5),
                    'step_seconds':round(time.monotonic()-before,3)},ensure_ascii=False),flush=True)
            if args.smoke:
                print('SMOKE_BACKWARD_OK',flush=True);return
        if args.joint:
            from training.rex_runtime import evaluate_joint
            validation=evaluate_joint(model,trainer,valid)
        else:
            validation=evaluate(model,trainer,valid)
        summary={k:v for k,v in validation.items() if k!='rows'}
        summary.update(epoch=epoch+1,loss=sum(losses)/len(losses),elapsed_seconds=time.monotonic()-started)
        history.append(summary)
        (output/'history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
        print('VALIDATION',json.dumps(summary,ensure_ascii=False),flush=True)
        # Prioritize avoiding false tool calls, then classification accuracy.
        score=(summary['accuracy']-summary['null_false_positive_rate']*.5,summary['accuracy'])
        if score>best_score:
            best_score=score
            save_file(adapter_state(model),str(output/'adapter_model.safetensors'))
            (output/'selection.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print('TRAINING_COMPLETE',str(output),flush=True)


if __name__=='__main__':main()
