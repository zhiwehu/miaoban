"""Private local planner process. Heavy/legacy dependencies stay out of the server."""
import argparse
import json
import sys
from pathlib import Path


class Engine:
    def __init__(self,adapter):
        root=Path(__file__).resolve().parents[1]
        from training.rex_runtime import load,LABELS
        from training.lora import inject,load_adapter,merge
        adapter=Path(adapter)
        config=json.loads((adapter/'adapter_config.json').read_text())
        if not config.get('joint_slots') or config.get('labels')!=LABELS:
            raise ValueError('A joint decision/slot adapter is required')
        self.model,self.tokenizer,_,self.trainer=load(root/'data/rex-runtime',root/'data/models/rex-uninlu-chinese-base')
        from safetensors.torch import load_file
        targets=inject(self.model,config['rank'],config['alpha'],config['dropout'])
        if targets!=config['targets']:raise ValueError('Target mismatch')
        load_adapter(self.model,load_file(str(adapter/'adapter_model.safetensors')))
        merge(self.model);self.model.eval()
        from training.rex_runtime import predict
        predict(self.model,self.trainer,'你好')

    def plan(self,request):
        from training.rex_runtime import predict,extract
        from .slot_normalizer import normalize_spans
        text=request.get('text')
        if not isinstance(text,str) or not 1<=len(text)<=300:
            return {'status':'clarify','tool':None,'arguments':{},'message':'请把请求拆成简短的一句话。'}
        from .local_calendar import calendar_answer
        if calendar_answer(text) is not None:
            return {'status':'chat','tool':None,'arguments':{}}
        previous_user=request.get('previous_user','')
        if not isinstance(previous_user,str) or len(previous_user)>80:previous_user=''
        classifier=('历史用户：'+previous_user+'\n当前用户：'+text) if previous_user else text
        if len(self.tokenizer(classifier,add_special_tokens=False)['input_ids'])>140:
            return {'status':'clarify','tool':None,'arguments':{},'message':'这次内容较长，请一次说一个简短的查询。'}
        tool=predict(self.model,self.trainer,classifier)
        if tool=='null':return {'status':'chat','tool':None,'arguments':{}}
        if tool=='invalid':
            return {'status':'clarify','tool':None,'arguments':{},'message':'我没确定你想查询什么，请再说具体一点。'}
        spans=extract(self.model,self.trainer,tool,text)
        if len(spans)>12:
            return {'status':'clarify','tool':tool,'arguments':{},'message':'这次参数较多，请一次查询一个具体问题。'}
        try:
            result=normalize_spans(tool,text,spans,request.get('defaults',{}),request.get('previous'))
        except ValueError:
            result={'status':'clarify','tool':tool,'arguments':{},'message':'查询参数还不明确，请换个说法。'}
        result['slots']=spans
        return result


def main():
    parser=argparse.ArgumentParser()
    root=Path(__file__).resolve().parents[1]
    parser.add_argument('--adapter',default=str(root/'data/adapters/rex-mcp-router-r16-slots-v2'))
    args=parser.parse_args()
    engine=Engine(args.adapter)
    print(json.dumps({'type':'ready','joint_slots':True}),flush=True)
    for raw in sys.stdin.buffer:
        if len(raw)>16384:return
        request=None
        try:
            request=json.loads(raw)
            if not isinstance(request,dict) or type(request.get('id'))is not int:raise ValueError('Invalid request')
            result=engine.plan(request)
            print(json.dumps({'id':request['id'],'result':result},ensure_ascii=False),flush=True)
        except (ValueError,TypeError,KeyError):
            print(json.dumps({'id':request.get('id') if isinstance(request,dict) else None,
                              'error':'planner_invalid_request'}),flush=True)


if __name__=='__main__':main()
