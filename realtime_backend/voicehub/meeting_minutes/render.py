"""Deterministic Markdown/structured rendering; no model output goes to HTML."""
import datetime
import re


def _clock(ms):
    if not isinstance(ms, (int, float)) or ms < 0:
        return ''
    seconds = int(ms//1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def _when(timestamp):
    if not isinstance(timestamp, (int, float)):
        return ''
    return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')


def _quote(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def _evidence_text(evidence):
    parts = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        stamp = item.get('stamp')
        speaker = item.get('speaker')
        line = item.get('line')
        where = f'[{stamp}]' if stamp else (f'第 {line} 行' if line else '')
        who = f' {speaker}' if speaker else ''
        quote = _quote(item.get('quote'))
        parts.append(f'{where}{who}：「{quote}」' if where else f'「{quote}」')
    return '；'.join(parts)


def _markdown_escape(value):
    return str(value or '').replace('|', '\\|').replace('\n', ' ')


def build_result(merged, template, recording, model_id, transcript_sha256):
    lines = int(recording.get('transcript_lines') or 0)
    speakers = int(recording.get('speaker_count') or 0)
    topics = merged.get('topics') or []
    key_points = merged.get('key_points') or []
    decisions = merged.get('decisions') or []
    actions = merged.get('actions') or []
    risks = merged.get('risks') or []
    questions = merged.get('questions') or []
    agreed = [item for item in decisions if item.get('status') == 'agreed']
    proposals = [item for item in decisions if item.get('status') != 'agreed']
    coverage = merged.get('coverage') or {}
    overview = (
        f'本场录音时长 {_clock(recording.get("duration_ms") or 0)}，'
        f'共有 {lines} 行转写' + (f'、{speakers} 位匿名发言人' if speakers else '') + '。'
    )
    if topics:
        overview += '主要讨论：' + '；'.join(topic.get('title') for topic in topics[:5]) + '。'
    overview += (f'提取到已确认决策 {len(agreed)} 条、提议/未决 {len(proposals)} 条、'
                 f'行动项 {len(actions)} 条、风险/待确认 {len(risks)+len(questions)} 条。')
    if coverage.get('chunks_failed'):
        overview += f' 有 {coverage["chunks_failed"]} 个片段生成失败，结果可能不完整。'
    # The model is told not to invent owners or names.  Keep this note visible.
    if any(action.get('due_date_relative') for action in actions):
        overview += ' 相对日期（如“下周五”）保留原文，未换算为绝对日期。'

    sections = []
    for key in template.get('sections') or []:
        if key == 'overview':
            sections.append({'key': 'overview', 'title': '会议概览', 'kind': 'text',
                             'text': overview})
        elif key == 'topics':
            sections.append({'key': 'topics', 'title': '议题与讨论', 'kind': 'topics',
                             'items': topics, 'key_points': key_points})
        elif key == 'decisions':
            sections.append({'key': 'decisions', 'title': '已确认决策', 'kind': 'decisions',
                             'items': agreed})
        elif key == 'proposals':
            sections.append({'key': 'proposals', 'title': '提议与未决', 'kind': 'proposals',
                             'items': proposals, 'questions': questions})
        elif key == 'action_items':
            sections.append({'key': 'action_items', 'title': '行动项', 'kind': 'actions',
                             'items': actions})
        elif key == 'risks':
            sections.append({'key': 'risks', 'title': '风险与待确认', 'kind': 'risks',
                             'items': risks,
                             'questions': [] if 'proposals' in (template.get('sections') or []) else questions})
    result = {
        'overview': overview,
        'sections': sections,
        'stats': {
            'duration_ms': recording.get('duration_ms') or 0,
            'transcript_lines': lines,
            'speakers': speakers,
            'topics': len(topics),
            'decisions': len(agreed),
            'proposals': len(proposals),
            'actions': len(actions),
            'risks': len(risks),
            'questions': len(questions),
        },
        'coverage': coverage,
        'model_id': model_id,
        'template_id': template['template_id'],
        'template_version': template['version'],
        'template_name': template['name'],
        'transcript_sha256': transcript_sha256,
        'generated_at': datetime.datetime.now().timestamp(),
    }
    result['markdown'] = render_markdown(result, template, recording)
    return result


def render_markdown(result, template, recording):
    out = [f"# {template['name']} · 会议纪要", '']
    meta = [
        f"- 录音时间：{_when(recording.get('started_at'))}",
        f"- 录音时长：{_clock(recording.get('duration_ms') or 0)}",
        f"- 转写：{result['stats']['transcript_lines']} 行"
        + (f"，{result['stats']['speakers']} 位匿名发言人"
           if result['stats']['speakers'] else ''),
        f"- 生成模型：`{result['model_id']}`（本地）",
        f"- 转写 SHA-256：`{result['transcript_sha256'][:16]}…`",
        f"- 生成时间：{_when(result['generated_at'])}",
    ]
    out.extend(meta)
    out.append('')
    for section in result['sections']:
        out.extend([f"## {section['title']}", ''])
        if section['kind'] == 'text':
            out.extend([section['text'], ''])
        elif section['kind'] == 'topics':
            if not section['items'] and not section.get('key_points'):
                out.extend(['未提取到明确议题。', ''])
            for index, topic in enumerate(section['items'], 1):
                out.append(f"### {index}. {topic.get('title') or '未命名议题'}")
                if topic.get('summary'):
                    out.extend([topic['summary'], ''])
                for evidence in topic.get('evidence') or []:
                    out.append(f"> 证据：{_evidence_text([evidence])}")
                    out.append('')
            if section.get('key_points'):
                out.append('### 关键点')
                for point in section['key_points']:
                    out.append(f"- {point.get('text')}")
                    if point.get('evidence'):
                        out.append(f"  > 证据：{_evidence_text(point['evidence'])}")
                out.append('')
        elif section['kind'] == 'decisions':
            if not section['items']:
                out.extend(['本段没有已确认决策。', ''])
            for item in section['items']:
                out.append(f"- **已确认**：{item.get('text')}")
                if item.get('evidence'):
                    out.append(f"  > 证据：{_evidence_text(item['evidence'])}")
            out.append('')
        elif section['kind'] == 'proposals':
            if not section['items'] and not section.get('questions'):
                out.extend(['没有提议、否决或未决事项。', ''])
            for item in section['items']:
                status = {'rejected': '已否决', 'open': '未决',
                          'proposed': '提议'}.get(item.get('status'), '未决')
                out.append(f"- **{status}**：{item.get('text')}")
                if item.get('evidence'):
                    out.append(f"  > 证据：{_evidence_text(item['evidence'])}")
            for question in section.get('questions') or []:
                out.append(f"- **待确认**：{question.get('text')}")
                if question.get('evidence'):
                    out.append(f"  > 证据：{_evidence_text(question['evidence'])}")
            out.append('')
        elif section['kind'] == 'actions':
            if not section['items']:
                out.extend(['没有提取到行动项。', ''])
            else:
                out.extend(['| 任务 | 负责人 | 截止 | 状态 | 证据 |',
                            '|---|---|---|---|---|'])
                for item in section['items']:
                    owner = item.get('owner') or '未明确'
                    due = item.get('due_date') or '未明确'
                    if item.get('due_date_relative'):
                        due += '（待确认）'
                    status = '已确认' if item.get('status') == 'agreed' else '提议'
                    if item.get('needs_confirmation'):
                        status += '/待确认'
                    out.append(
                        f"| {_markdown_escape(item.get('task'))} | "
                        f"{_markdown_escape(owner)} | {_markdown_escape(due)} | "
                        f"{status} | {_markdown_escape(_evidence_text(item.get('evidence')))} |")
                out.append('')
        elif section['kind'] == 'risks':
            if not section['items'] and not section.get('questions'):
                out.extend(['没有提取到风险或待确认事项。', ''])
            for item in section['items']:
                out.append(f"- **风险/阻塞**：{item.get('text')}")
                if item.get('evidence'):
                    out.append(f"  > 证据：{_evidence_text(item['evidence'])}")
            for question in section.get('questions') or []:
                out.append(f"- **待确认**：{question.get('text')}")
                if question.get('evidence'):
                    out.append(f"  > 证据：{_evidence_text(question['evidence'])}")
            out.append('')
    coverage = result.get('coverage') or {}
    if coverage.get('chunks_failed'):
        out.extend(['## 生成说明', '',
                    f"本次生成有 {coverage['chunks_failed']} 个转写片段失败，"
                    '纪要可能遗漏该片段内容；可以重新生成。', ''])
    out.append('> 本纪要由本机模型根据录音转写生成，匿名说话人标签不代表真实姓名；'
               '证据时间戳可回到录音播放器核对。')
    return '\n'.join(out).strip() + '\n'


def plain_text(markdown):
    text = markdown or ''
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = text.replace('**', '').replace('`', '')
    text = re.sub(r'^\s*>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\|[\s:-]+\|[\s|:-]*$', '', text, flags=re.MULTILINE)
    text = text.replace('|', ' ')
    return text
