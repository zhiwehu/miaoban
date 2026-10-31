"""Build the v5 decision dataset: v4b + real-world hard negatives.

The v4b families cover the seven decision labels well, but they were written
before the assistant met real conversations. Real traffic contains short
social turns, wake words, and evaluative answers that the classifier used to
label as weather/news/system_info. This builder appends those patterns as
``null`` rows without touching any tool family and without reusing a single
sentence from the hold-out test split.

Usage:
    python scripts/build_router_v5_data.py --base data/training/router-everyday-v4b \
        --output data/training/router-everyday-v5
"""
import argparse
import hashlib
import json
import random
import re
from pathlib import Path

# Families observed in real conversations. Kept as templates so no logged
# utterance is copied verbatim into training.
HARD_NEGATIVES = [
    # 唤醒词单独出现：这是一次激活，不是请求
    ['小宝', '小宝同学', '喵伴', '你好喵伴', '小宝小宝', '小宝，小宝，小宝', '小宝同学，小宝同学'],
    # 寒暄与在场确认
    ['你好呀', '哈喽', '嗨', '早上好', '晚上好', '在吗', '你在吗', '你在不在', '好呀'],
    # 反问、搭话、口头语
    ['怎么了', '这样啊', '然后呢', '你说什么', '刚才说到哪了', '你什么意思', '你说呢'],
    # 评价与应答（常跟在「今天过得怎么样」之后）
    ['还可以啊', '还行吧', '挺好的', '一般般', '不错啊', '我很好', '就这样吧', '嗯，可以'],
    # 聊自己的状态，不是天气查询
    ['今天过得好吗', '今天过得怎么样', '你今天开心吗', '最近日子过得怎么样', '我今天有点累',
     '今天上班好忙', '我今天在家休息'],
    # 提到主题但没有请求
    ['天气不错', '今天好热啊', '外面好像出太阳了', '新闻联播开始了', '我刚看到一条新闻',
     '电脑有点卡', '我手机也没电了'],
    # 询问能力/身份，而不是真的执行
    ['你能做什么', '你会干什么', '你是谁', '你是机器人吗', '你能听懂我说话吗', '你会讲故事吗'],
    # 感谢与告别
    ['谢谢你', '谢谢啦', '辛苦了', '拜拜', '再见', '晚安', '我先睡了'],
    # 上一轮的工具话题之后的闲聊承接
    ['哦，原来是这个意思', '好的我知道了', '那就这样吧', '嗯嗯，明白了', '还有别的事吗'],
]

PARAPHRASE = [
    '{}。', '{}呢？', '{}啊。', '{}呀', '嗯，{}。', '好，{}。',
    '那{}。', '唉，{}。', '{}吧。', '{}哈。', '就{}。',
]


def norm(value):
    return re.sub(r'[\W_]+', '', value).lower()


def rows_from_families(families, rng, per_family, split, seen, forbidden):
    rows = []
    for family in families:
        produced = 0
        for base in family:
            for shape in PARAPHRASE:
                text = shape.format(base)
                key = norm(text)
                if key in seen or key in forbidden:
                    continue
                seen.add(key)
                rows.append({'text': text, 'label': 'null',
                             'family': 'hard-negative:' + norm(base)[:12],
                             'synthetic': True,
                             'id': hashlib.sha256((split + text).encode()).hexdigest()[:16]})
                produced += 1
                if produced >= per_family:
                    break
            if produced >= per_family:
                break
        if not produced:
            raise RuntimeError('hard negative family produced no rows: ' + family[0])
    rng.shuffle(rows)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='data/training/router-everyday-v4b')
    parser.add_argument('--output', default='data/training/router-everyday-v5')
    parser.add_argument('--extra-eval', help='JSONL of hold-out texts that must never appear in training')
    args = parser.parse_args()

    base, out = Path(args.base), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261031)

    forbidden = set()
    for split in ('validation', 'test'):
        for line in (base / (split + '.jsonl')).read_text().splitlines():
            forbidden.add(norm(json.loads(line)['text']))
    if args.extra_eval:
        for line in Path(args.extra_eval).read_text().splitlines():
            forbidden.add(norm(json.loads(line)['text']))

    for split, per_family in (('train', 80), ('validation', 20)):
        rows = [json.loads(line) for line in (base / (split + '.jsonl')).read_text().splitlines()]
        seen = {norm(row['text']) for row in rows}
        extra = rows_from_families(HARD_NEGATIVES, rng, per_family, split, seen, forbidden)
        rows.extend(extra)
        rng.shuffle(rows)
        with (out / (split + '.jsonl')).open('w') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'{split}: {len(rows)} rows (+{len(extra)} hard negatives)')

    # The test split stays byte-identical to v4b so scores remain comparable.
    (out / 'test.jsonl').write_text((base / 'test.jsonl').read_text())
    print('test: copied unchanged from', base)


if __name__ == '__main__':
    main()
