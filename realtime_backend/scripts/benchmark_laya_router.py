"""Offline, synthetic Chinese tool-routing evaluation; never executes a tool."""
import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

CASES = [
    ('你好呀', 'null'),
    ('你叫什么名字？', 'null'),
    ('给我讲个笑话', 'null'),
    ('一加一等于多少？', 'null'),
    ('给我解释一下什么叫相对湿度', 'null'),
    ('天气真好，心情也不错', 'null'),
    ('新闻是什么意思？', 'null'),
    ('CPU和内存有什么区别？', 'null'),
    ('你会查天气吗？', 'null'),
    ('不要查新闻了，给我讲个故事', 'null'),
    ('你刚才的回答太长了，简短一点', 'null'),
    ('把音量调小一点', 'null'),
    ('写一首描写春雨的诗', 'null'),
    ('电脑内存一般买多大合适？', 'null'),
    ('什么是新闻客观性？', 'null'),
    ('上海明天天气怎么样？', 'weather'),
    ('北京现在下雨吗？', 'weather'),
    ('杭州后天最高温度多少？', 'weather'),
    ('帮我查一下广州今天的天气', 'weather'),
    ('深圳明天出门需要带伞吗？', 'weather'),
    ('成都今天的降水概率是多少？', 'weather'),
    ('明天天气怎么样？', 'weather'),
    ('帮我播报最新新闻', 'news'),
    ('读两条BBC国际新闻', 'news'),
    ('最近有什么新闻？', 'news'),
    ('查一下人工智能相关新闻', 'news'),
    ('今天有哪些新闻头条？', 'news'),
    ('从中新网找三条最新消息', 'news'),
    ('这台电脑的内存用了多少？', 'system_info'),
    ('现在CPU占用率是多少？', 'system_info'),
    ('本机磁盘还有多少空间？', 'system_info'),
    ('帮我看看运行后端的机器状态', 'system_info'),
    ('这台机器开机多久了？', 'system_info'),
    ('查询系统内存和磁盘使用情况', 'system_info'),
]

CRITERIA = {
    'null': '日常聊天、常识问答、写作、解释概念、询问能力、取消查询或不支持的操作；不需要查询实时信息。',
    'weather': '用户要求查询某地当前或未来三天天气、温度、降水或是否带伞；不包括闲聊天气或解释天气术语。',
    'news': '用户要求获取或播报新闻源的最新新闻或消息；不包括解释新闻概念或取消读新闻。',
    'system_info': '用户要求读取运行语音后端这台电脑当前CPU、内存、磁盘使用情况或开机时长；不包括电脑知识或购买建议。',
}

QUESTIONS = {'tool': {'type':'choice',
    'instructions':'根据当前用户请求选择最合适的工具。只判断当前意图，不要因为出现某个关键词就调用。无需工具选null。',
    'criteria':CRITERIA}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, help='Audited Laya package directory')
    parser.add_argument('--model', required=True, help='Downloaded local checkpoint directory')
    parser.add_argument('--device', default='cpu', choices=['cpu','mps'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--labels', choices=['codes','chinese'], default='codes')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.runtime).resolve()))
    import torch
    import laya
    torch.set_num_threads(args.threads)
    label_to_tool = {'直接回答，无需工具':'null', '查询实时天气':'weather',
                     '获取新闻报道':'news', '查询这台电脑状态':'system_info'}
    questions = QUESTIONS
    if args.labels == 'chinese':
        questions = {'tool': {**QUESTIONS['tool'], 'criteria':{
            label:CRITERIA[tool] for label, tool in label_to_tool.items()}}}
    started = time.monotonic()
    agent = laya.load(str(Path(args.model).resolve()), device=args.device)
    load_seconds = time.monotonic()-started
    # Warm separately; never include cold model loading in the hot latency numbers.
    warm = agent.predict({'user_message':'你好'}, questions)
    print('loaded', round(load_seconds, 2), 'warm_answer', json.dumps(warm, ensure_ascii=False)[:1400], flush=True)
    rows = []
    for text, expected in CASES:
        started = time.monotonic()
        response = agent.predict({'user_message':text}, questions)
        elapsed = time.monotonic()-started
        answer = response['answers']['tool']
        actual = label_to_tool[answer['choice']] if args.labels == 'chinese' else answer['choice']
        row = {'text':text, 'expected':expected, 'actual':actual,
               'correct':actual==expected, 'elapsed_ms':round(elapsed*1000, 2),
               'answer':answer}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    latencies = sorted(row['elapsed_ms'] for row in rows)
    summary = {'laya_version':laya.__version__, 'model':str(Path(args.model).resolve()),
        'device':args.device, 'labels':args.labels, 'torch_threads':args.threads, 'load_seconds':round(load_seconds, 2),
        'cases':len(rows), 'correct':sum(row['correct'] for row in rows),
        'accuracy':round(sum(row['correct'] for row in rows)/len(rows), 4),
        'p50_ms':statistics.median(latencies), 'p95_ms':latencies[math.ceil(len(latencies)*.95)-1],
        'mean_ms':statistics.mean(latencies),
        'p95_method':'nearest rank',
        'note':'固定合成样例，小规模零样本试验；不是生产准确率或置信度校准保证。'}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'summary':summary, 'questions':questions, 'rows':rows},ensure_ascii=False,indent=2))
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
