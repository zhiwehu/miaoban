"""Frozen test/challenge evaluation, separate from validation-based model selection."""
import argparse,json,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--adapter',default='data/adapters/rex-everyday-v4');args=parser.parse_args()
 from voicehub.rex_planner_worker import Engine
 from training.everyday_eval import evaluate
 engine=Engine(args.adapter)
 paths={'test':'data/training/router-everyday-v4/test.jsonl','challenge':'training/everyday_challenge.jsonl','core_v2_test':'data/training/router-slots-v2/test.jsonl','core_v3_test':'data/training/router-slots-v3/test.jsonl'}
 output=Path(args.adapter)/'evaluation';output.mkdir(exist_ok=True);summaries={}
 for name,path in paths.items():
  source=Path(path)
  if not source.exists():continue
  rows=[json.loads(line) for line in source.read_text().splitlines()];result=evaluate(engine.model,engine.trainer,rows)
  (output/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
  summary={key:value for key,value in result.items() if key!='rows'};summary['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();summaries[name]=summary
  print(name,json.dumps(summary,ensure_ascii=False),flush=True)
 (output/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
