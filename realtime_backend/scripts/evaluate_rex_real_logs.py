"""Score the Rex decision classifier on real conversation logs.

The labels come from what the running backend actually did: a turn that
started a tool is that tool, a turn that started none is ``null``. Only the
four labels the classifier owns are scored; music/calendar/file_organizer and
extension tools are routed by rules or by the LLM planner before Rex, so they
are reported separately as "turns that must not be claimed as a tool".

The evaluation set is never used for training; ``build_router_v5_data.py``
takes it as a forbidden list.

Usage:
    python scripts/evaluate_rex_real_logs.py --database data/history.sqlite3
    python scripts/evaluate_rex_real_logs.py --adapter data/adapters/rex-everyday-v5
"""
import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OWNED = ('null', 'weather', 'news', 'system_info')


def load_rows(database, limit=None):
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "select turn_id,type,payload from events where type in ('transcript.final','tool.started')"
    ).fetchall()
    turns = {}
    for turn, kind, payload in rows:
        data = json.loads(payload or '{}')
        turn_data = turns.setdefault(turn, {'user': None, 'tools': []})
        if kind == 'transcript.final':
            turn_data['user'] = data.get('text')
        else:
            turn_data['tools'].append(data.get('tool'))
    seen, unique = set(), []
    for turn_data in turns.values():
        text = (turn_data['user'] or '').strip()
        if not text:
            continue
        label = turn_data['tools'][0] if turn_data['tools'] else 'null'
        if (text, label) in seen:
            continue
        seen.add((text, label))
        unique.append({'text': text, 'label': label})
    return unique[:limit] if limit else unique


async def score(rows):
    from voicehub.rex_planner import RexPlanner
    defaults = {'default_city': '合肥', 'news_source': 'chinanews', 'news_limit': 3}
    planner = RexPlanner()
    owned_correct = owned_total = 0
    null_false_positive = null_total = 0
    out_of_scope_claimed = out_of_scope_total = 0
    confusions = Counter()
    try:
        for index, row in enumerate(rows):
            try:
                result = await planner.plan([{'role': 'user', 'content': row['text']}], dict(defaults), None)
            except Exception as exc:                      # planner failure is reported, not hidden
                confusions[('error', type(exc).__name__)] += 1
                continue
            predicted = result.get('tool') or 'null'
            expected = row['label']
            if expected in OWNED:
                owned_total += 1
                if predicted == expected:
                    owned_correct += 1
                else:
                    confusions[(expected, predicted)] += 1
                if expected == 'null':
                    null_total += 1
                    if predicted != 'null':
                        null_false_positive += 1
            else:
                out_of_scope_total += 1
                if predicted != 'null':
                    out_of_scope_claimed += 1
            if (index + 1) % 50 == 0:
                print(f'  ...{index + 1}/{len(rows)}', flush=True)
    finally:
        await planner.close()
    return {
        'rows': len(rows),
        'owned_rows': owned_total,
        'accuracy': round(owned_correct / owned_total, 4) if owned_total else None,
        'null_false_positive_rate': round(null_false_positive / null_total, 4) if null_total else None,
        'null_rows': null_total,
        'out_of_scope_rows': out_of_scope_total,
        'out_of_scope_claimed_rate': round(out_of_scope_claimed / out_of_scope_total, 4) if out_of_scope_total else None,
        'confusions': {f'{a}->{b}': n for (a, b), n in confusions.most_common(12)},
    }


def activate(adapter, restore):
    path = Path('data/rex-planner-active.json')
    if adapter:
        restore['content'] = path.read_text() if path.exists() else None
        path.write_text(json.dumps({'adapter': adapter}) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='data/history.sqlite3')
    parser.add_argument('--adapter', help='Evaluate this adapter (temporarily writes data/rex-planner-active.json)')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()

    rows = load_rows(args.database, args.limit)
    path = Path('data/rex-planner-active.json')
    restore = {'content': path.read_text() if path.exists() else None}
    try:
        if args.adapter:
            path.write_text(json.dumps({'adapter': args.adapter}) + '\n')
        print(json.dumps({'adapter': args.adapter or '(default)', 'rows': len(rows)}, ensure_ascii=False))
        result = asyncio.run(score(rows))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if restore['content'] is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(restore['content'])


if __name__ == '__main__':
    main()
