"""Local tool-intent decision only; does not execute MCP or generate arguments."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--text',required=True)
    parser.add_argument('--runtime',default='data/rex-runtime')
    parser.add_argument('--model',default='data/models/rex-uninlu-chinese-base')
    parser.add_argument('--adapter',default='data/adapters/rex-mcp-router-r16-v1')
    args=parser.parse_args()
    if not 1<=len(args.text)<=1000:raise ValueError('Text must contain 1–1000 characters')
    config=json.loads((Path(args.adapter)/'adapter_config.json').read_text())
    if hashlib.sha256((Path(args.model)/'pytorch_model.bin').read_bytes()).hexdigest()!=config['base_weight_sha256']:
        raise ValueError('Base checkpoint does not match adapter')
    from training.rex_runtime import load,predict
    model,_,_,trainer=load(args.runtime,args.model)
    from training.lora import inject,load_adapter,merge
    from safetensors.torch import load_file
    targets=inject(model,config['rank'],config['alpha'],config['dropout'])
    if targets!=config['targets']:raise ValueError('Adapter target mismatch')
    load_adapter(model,load_file(str(Path(args.adapter)/'adapter_model.safetensors')))
    merge(model);model.eval()
    result=predict(model,trainer,args.text)
    print(json.dumps({'tool':None if result in ('null','invalid') else result,
                      'classification_valid':result!='invalid', 'confidence':None,
                      'note':'Classifies intent only; parameters and permissions still require validation.'},ensure_ascii=False))


if __name__=='__main__':main()
