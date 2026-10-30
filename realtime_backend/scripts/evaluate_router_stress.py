"""Evaluate the actual local decision layer offline; never calls MCP or changes weights."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from router_stress_cases import cases, normalized
from voicehub.local_calendar import calendar_answer


def error_kind(expected, actual):
    if all(expected.get(k)==actual.get(k) for k in ('status','tool','arguments')):return None
    if expected['tool'] is None and actual.get('status')=='ready' and actual.get('tool'):return 'false_call'
    if expected['tool'] is None and actual.get('tool'):return 'false_route_or_clarification'
    if expected['status']=='ready' and actual.get('status')=='clarify':return 'unnecessary_clarification'
    if expected['tool'] and actual.get('tool')!=expected['tool']:return 'wrong_or_missed_tool'
    if expected['status']=='clarify' and actual.get('status')=='ready':return 'unsafe_parameter_acceptance'
    if expected['status']=='calendar':return 'calendar_not_handled'
    return 'wrong_parameters_or_status'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--adapter',default='data/adapters/rex-mcp-router-r16-slots-v2')
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    training=set()
    for directory in ('router-v1','router-slots-v2'):
        path=Path('data/training')/directory/'train.jsonl'
        if path.exists():
            training.update(normalized(json.loads(line)['text']) for line in path.read_text().splitlines())
    generated=cases()
    fresh=[row for row in generated if normalized(row['text']) not in training]
    oldpath=Path('data/training/router-slots-v2/test.jsonl')
    legacy=[{**json.loads(line),'category':'original_joint_test','family':'original.'+json.loads(line)['family']}
            for line in oldpath.read_text().splitlines()]
    rows=fresh+legacy
    dataset=''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows)
    (output/'cases.jsonl').write_text(dataset)
    from voicehub.rex_planner_worker import Engine
    from training.rex_runtime import predict
    engine=Engine(args.adapter)
    totals=defaultdict(Counter);raw=defaultdict(Counter);durations=[];results=[]
    with (output/'results.jsonl').open('w') as file:
        for index,row in enumerate(rows):
            defaults={'default_city':'','news_source':'bbc','news_limit':3,**row.get('defaults',{})}
            previous_user=row.get('previous_user','')
            # Existing held-out contextual examples use the same history/current framing as training.
            if row.get('classification_text','').startswith('历史用户：'):
                previous_user=row['classification_text'].split('历史用户：',1)[1].split('\n当前用户：',1)[0]
            request={'text':row['text'],'previous_user':previous_user,'defaults':defaults,'previous':row.get('previous')}
            classifier=('历史用户：'+previous_user+'\n当前用户：'+row['text']) if previous_user else row['text']
            model_label=predict(engine.model,engine.trainer,classifier)
            started=time.monotonic()
            calendar=calendar_answer(row['text'])
            plan={'status':'calendar','tool':None,'arguments':{}} if calendar is not None else engine.plan(request)
            elapsed=(time.monotonic()-started)*1000;durations.append(elapsed)
            expected=row['expected'];error=error_kind(expected,plan)
            for group in ('all',row['category']):
                totals[group]['count']+=1
                totals[group]['correct']+=error is None
                if error:totals[group][error]+=1
                raw[group]['count']+=1
                raw[group]['correct']+=model_label==(expected['tool'] or 'null')
            result={**row,'actual':plan,'calendar_answer':calendar,'raw_model_label':model_label,
                    'error':error,'elapsed_ms':round(elapsed,2)}
            file.write(json.dumps(result,ensure_ascii=False)+'\n');file.flush();results.append(result)
            if index%100==0:print(f'{index+1}/{len(rows)} evaluated',flush=True)
    def metrics(counter):
        return {**counter,'accuracy':counter['correct']/counter['count']}
    summary={'evaluation_only':True,'synthetic':True,'fresh_cases':len(fresh),'original_test_cases':len(legacy),
             'excluded_training_text_matches':len(generated)-len(fresh),
             'families':len({r['family'] for r in rows}),
             'dataset_sha256':hashlib.sha256(dataset.encode()).hexdigest(),
             'adapter_sha256':hashlib.sha256((Path(args.adapter)/'adapter_model.safetensors').read_bytes()).hexdigest(),
             'production_layer':{k:metrics(v) for k,v in totals.items()},
             'raw_neural_classifier':{k:metrics(v) for k,v in raw.items()},
             'mean_ms':statistics.mean(durations),'p95_ms':sorted(durations)[int(len(durations)*.95)],
             'mcp_executed':False,'weights_changed':False}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    (output/'failures.json').write_text(json.dumps([r for r in results if r['error']],ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
