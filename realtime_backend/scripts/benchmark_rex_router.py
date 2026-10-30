"""Offline RexUniNLU classification using its original model and prediction code."""
import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from benchmark_laya_router import CASES, CRITERIA


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--labels', choices=['short','descriptions'], default='short')
    args = parser.parse_args()
    model_path = Path(args.model).resolve()
    os.environ.update(USE_TF='0', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HOME=str(model_path.parent/'rex-hf-cache'), TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0, str(Path(args.runtime).resolve()))
    sys.path.insert(0, str(model_path))
    import torch
    import transformers
    from transformers import AutoConfig, AutoTokenizer
    from rex.model.model import RexModel
    from rex.arguments import DataArguments, UIEArguments, ModelArguments
    from rex.data_utils.data_loader import UIEDataLoader
    from rex.Trainer.trainer import RexModelTrainer
    from rex.data_utils import token_config

    torch.set_num_threads(args.threads)
    transformers.logging.set_verbosity_error()
    label_to_tool = {'无需工具':'null', '天气查询':'weather',
                     '新闻查询':'news', '本机状态查询':'system_info'}
    if args.labels == 'descriptions':
        label_to_tool = {label+'：'+CRITERIA[tool]:tool for label,tool in label_to_tool.items()}
    schema = {label:None for label in label_to_tool}
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    tokenizer.add_special_tokens({'additional_special_tokens':[
        token_config.PREFIX_TOKEN, token_config.TYPE_TOKEN,
        token_config.CLASSIFY_TOKEN, token_config.MULTI_CLASSIFY_TOKEN]})
    with tempfile.TemporaryDirectory(prefix='rex-router-eval-') as work:
        training = UIEArguments(output_dir=work, bert_model_dir=str(model_path),
            load_checkpoint=str(model_path), no_cuda=True, fp16=False,
            report_to=[], disable_tqdm=True)
        model = RexModel(AutoConfig.from_pretrained(model_path, local_files_only=True), training, ModelArguments())
        state = torch.load(model_path/'pytorch_model.bin', map_location='cpu', weights_only=True)
        ignored_buffers = []
        key = 'plm.embeddings.position_ids'
        if key in state and key not in model.state_dict():
            # This old Transformers buffer is a fixed arange, not learned weights;
            # current DeBERTa receives explicit position_ids from Rex's preprocessor.
            expected = torch.arange(state[key].shape[-1]).unsqueeze(0)
            if not torch.equal(state[key], expected):
                raise ValueError('Unexpected legacy position_ids buffer')
            state.pop(key)
            ignored_buffers.append(key)
        # The official wrapper uses strict=False. Require an exact match here so a
        # partially/randomly initialized model cannot accidentally enter the comparison.
        model.load_state_dict(state, strict=True)
        del state
        model.eval()
        data_args = DataArguments()
        loader = UIEDataLoader(data_args, tokenizer, '', training.local_rank,
                               training.world_size, True)
        trainer = RexModelTrainer(model, training, loader.get_collate_fn(), tokenizer=tokenizer)
        trainer.rex_dl, trainer.data_args = loader, data_args
        def predict(text):
            with torch.inference_mode():
                return trainer.prediction_step(model, {'text':'[CLASSIFY]'+text,'schema':schema},
                                               prediction_loss_only=False, do_pred=True)
        load_seconds = time.monotonic()-started
        print('loaded', round(load_seconds,2), 'strict_state_dict_match', True, flush=True)
        print('warm_answer', json.dumps(predict('你好'),ensure_ascii=False), flush=True)
        rows = []
        for text, expected in CASES:
            started = time.monotonic()
            result = predict(text)
            elapsed = time.monotonic()-started
            labels = [item['type'] for group in result for item in group]
            # An empty/unknown result is an error, never silently count it as null.
            actual = label_to_tool.get(labels[0], 'invalid') if len(labels)==1 else 'invalid'
            row = {'text':text,'expected':expected,'actual':actual,'correct':actual==expected,
                   'elapsed_ms':round(elapsed*1000,2),'raw_result':result}
            rows.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
    times = sorted(row['elapsed_ms'] for row in rows)
    summary = {'model':'iic/nlp_deberta_rex-uninlu_chinese-base','adapter':None,
        'device':'cpu','threads':args.threads,'labels':args.labels,'cases':len(rows),
        'correct':sum(row['correct'] for row in rows),
        'accuracy':round(sum(row['correct'] for row in rows)/len(rows),4),
        'load_seconds':round(load_seconds,2),'p50_ms':statistics.median(times),
        'mean_ms':statistics.mean(times),'p95_ms':times[math.ceil(len(times)*.95)-1],
        'p95_method':'nearest rank','transformers':transformers.__version__,
        'strict_state_dict_match':True,
        'ignored_legacy_constant_buffers':ignored_buffers,
        'parameters':sum(parameter.numel() for parameter in model.parameters()),
        'note':'Same 34 synthetic cases as Laya; no adapter or training. Original Rex prediction code.'}
    output = Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'summary':summary,'schema':schema,'rows':rows},ensure_ascii=False,indent=2))
    print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
