"""Compare frozen v2/v3 adapters on the fresh v3 holdout; no training or selection."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from voicehub.rex_planner_worker import Engine
from training.rex_runtime import evaluate_joint

def main():
    output=Path('../diagnostics/2026-10-29/rex-slots-v3');output.mkdir(parents=True,exist_ok=True)
    rows=[json.loads(line) for line in Path('data/training/router-slots-v3/test.jsonl').read_text().splitlines()]
    summary={}
    for version in ('v2','v3'):
        engine=Engine(f'data/adapters/rex-mcp-router-r16-slots-{version}')
        result=evaluate_joint(engine.model,engine.trainer,rows)
        result['by_label']={label:{'count':sum(r['label']==label for r in result['rows']),
            'correct':sum(r['label']==label and r['correct'] for r in result['rows'])}
            for label in ('null','weather','news','system_info')}
        (output/(version+'-fresh-test.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        summary[version]={k:v for k,v in result.items() if k!='rows'}
        print(version,json.dumps(summary[version],ensure_ascii=False),flush=True)
        del engine
    (output/'fresh-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
