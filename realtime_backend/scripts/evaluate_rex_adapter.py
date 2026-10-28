"""One final held-out comparison; no checkpoint selection or training here."""
import argparse
import copy
import hashlib
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',default='data/rex-runtime')
    parser.add_argument('--model',default='data/models/rex-uninlu-chinese-base')
    parser.add_argument('--data',default='data/training/router-v1')
    parser.add_argument('--adapter',default='data/adapters/rex-mcp-router-r16-v1')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    from training.rex_runtime import load,evaluate,predict
    model,tokenizer,loader,trainer=load(args.runtime,args.model)
    from training.lora import inject,load_adapter,merge
    from safetensors.torch import load_file
    from benchmark_laya_router import CASES
    import torch
    test_file=Path(args.data)/'test.jsonl'
    test=[json.loads(line) for line in test_file.read_text().splitlines()]
    regression=[{'text':text,'label':label,'id':'regression-'+str(i)} for i,(text,label) in enumerate(CASES)]
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    baseline={'test':evaluate(model,trainer,test),'regression':evaluate(model,trainer,regression)}
    (output/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    config=json.loads((Path(args.adapter)/'adapter_config.json').read_text())
    actual_hash=hashlib.sha256((Path(args.model)/'pytorch_model.bin').read_bytes()).hexdigest()
    if actual_hash!=config['base_weight_sha256']:raise ValueError('Base model hash mismatch')
    targets=inject(model,config['rank'],config['alpha'],config['dropout'])
    if targets!=config['targets']:raise ValueError('Target layers mismatch')
    weights=load_file(str(Path(args.adapter)/'adapter_model.safetensors'))
    load_adapter(model,weights);model.eval()
    adapted={'test':evaluate(model,trainer,test),'regression':evaluate(model,trainer,regression)}
    (output/'adapted.json').write_text(json.dumps(adapted,ensure_ascii=False,indent=2))
    expected=[row['actual'] for row in adapted['test']['rows']]
    merge(model);model.eval()
    # Verify every held-out prediction after algebraically merging the deltas.
    merged=[predict(model,trainer,row['text']) for row in test]
    if merged!=expected:raise RuntimeError('Merged adapter predictions changed')
    summary={'adapter':str(Path(args.adapter).resolve()),'test_sha256':hashlib.sha256(test_file.read_bytes()).hexdigest(),
        'base_sha256':actual_hash,'adapter_sha256':hashlib.sha256((Path(args.adapter)/'adapter_model.safetensors').read_bytes()).hexdigest(),
        'merged_prediction_match':True,'evaluation_device':'cpu','threads':4}
    for name,results in [('baseline',baseline),('adapted',adapted)]:
        summary[name]={}
        for split,result in results.items():
            metrics={k:v for k,v in result.items() if k!='rows'}
            metrics['mean_ms']=statistics.mean(row['elapsed_ms'] for row in result['rows'])
            summary[name][split]=metrics
    summary['acceptance_targets_met']=(summary['adapted']['test']['accuracy']>=.9 and
                                      summary['adapted']['test']['null_false_positive_rate']<=.05)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
