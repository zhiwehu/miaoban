"""Held-out end-to-end decision and argument evaluation for the joint adapter."""
import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--adapter',default='data/adapters/rex-mcp-router-r16-slots-v2')
    parser.add_argument('--data',default='data/training/router-slots-v2')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    from training.rex_runtime import load,evaluate,evaluate_joint
    model,_,_,trainer=load('data/rex-runtime','data/models/rex-uninlu-chinese-base')
    from training.lora import inject,load_adapter,merge
    from safetensors.torch import load_file
    from benchmark_laya_router import CASES
    from voicehub.slot_normalizer import FIELDS
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    test_path=Path(args.data)/'test.jsonl'
    rows=[json.loads(line) for line in test_path.read_text().splitlines()]
    regression=[{'text':text,'label':label} for text,label in CASES]
    baseline=evaluate_joint(model,trainer,rows)
    (output/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    config=json.loads((Path(args.adapter)/'adapter_config.json').read_text())
    if not config['joint_slots']:raise ValueError('Joint adapter required')
    targets=inject(model,config['rank'],config['alpha'],config['dropout'])
    if targets!=config['targets']:raise ValueError('Adapter targets mismatch')
    load_adapter(model,load_file(str(Path(args.adapter)/'adapter_model.safetensors')))
    adapted=evaluate_joint(model,trainer,rows)
    (output/'adapted.json').write_text(json.dumps(adapted,ensure_ascii=False,indent=2))
    reg=evaluate(model,trainer,regression)
    (output/'regression.json').write_text(json.dumps(reg,ensure_ascii=False,indent=2))
    merge(model);model.eval()
    merged=evaluate_joint(model,trainer,rows)
    def key(row):return (row['actual'],row['plan']['status'],row['plan']['tool'],row['plan']['arguments'])
    if [key(row) for row in merged['rows']]!=[key(row) for row in adapted['rows']]:
        raise RuntimeError('Merging changed a decision or argument')
    summary={}
    for name,result in [('baseline',baseline),('adapted',adapted),('merged',merged),('regression',reg)]:
        summary[name]={k:v for k,v in result.items() if k!='rows'}
        summary[name]['mean_ms']=statistics.mean(row['elapsed_ms'] for row in result['rows'])
    summary.update(merged_plan_match=True,test_sha256=hashlib.sha256(test_path.read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256((Path(args.adapter)/'adapter_model.safetensors').read_bytes()).hexdigest(),
        device='cpu',threads=4)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    config['slot_fields']={k:list(v) for k,v in FIELDS.items()}
    (Path(args.adapter)/'adapter_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
