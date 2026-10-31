"""Built-in meeting-minute templates and validation for user templates."""
import re
import secrets

SECTIONS = ('overview', 'topics', 'decisions', 'proposals', 'action_items', 'risks')
SECTION_TITLES = {
    'overview': '会议概览',
    'topics': '议题与讨论',
    'decisions': '已确认决策',
    'proposals': '提议与未决',
    'action_items': '行动项',
    'risks': '风险与待确认',
}
DEFAULT_SECTIONS = list(SECTIONS)

BUILTINS = (
    {
        'template_id': 'general', 'version': 1, 'name': '通用详细纪要',
        'description': '适合大多数会议，完整记录议题、决策、行动项和待确认事项。',
        'sections': DEFAULT_SECTIONS,
        'instructions': '完整保留讨论脉络。重点区分已确认决策、提议/否决/未决观点，以及没有明确负责人或截止时间的行动项。',
        'evidence_required': True, 'language': 'zh-CN', 'builtin': True,
    },
    {
        'template_id': 'project_weekly', 'version': 1, 'name': '项目周会',
        'description': '关注各项目进展、阻塞、负责人和下一步计划。',
        'sections': ['overview', 'topics', 'decisions', 'action_items', 'risks', 'proposals'],
        'instructions': '重点关注每个项目的进展、阻塞、负责人、下一步计划和跨团队依赖。不要把一个提议当成已经确认的决定。',
        'evidence_required': True, 'language': 'zh-CN', 'builtin': True,
    },
    {
        'template_id': 'tech_review', 'version': 1, 'name': '技术评审',
        'description': '关注方案目标、架构取舍、风险、结论和后续动作。',
        'sections': ['overview', 'topics', 'decisions', 'risks', 'action_items', 'proposals'],
        'instructions': '重点关注技术目标、方案取舍、兼容性/性能/安全风险、评审结论和待验证问题。保留明确的技术名词。',
        'evidence_required': True, 'language': 'zh-CN', 'builtin': True,
    },
    {
        'template_id': 'product_requirements', 'version': 1, 'name': '产品需求讨论',
        'description': '关注用户问题、需求范围、优先级、决策和待确认项。',
        'sections': ['overview', 'topics', 'decisions', 'proposals', 'action_items', 'risks'],
        'instructions': '重点关注用户问题、需求边界、优先级、交互取舍、验收标准和待确认项。区分需求讨论与已确认范围。',
        'evidence_required': True, 'language': 'zh-CN', 'builtin': True,
    },
    {
        'template_id': 'client_communication', 'version': 1, 'name': '客户沟通',
        'description': '关注客户诉求、承诺、后续跟进和风险。',
        'sections': ['overview', 'topics', 'decisions', 'action_items', 'risks', 'proposals'],
        'instructions': '重点关注客户诉求、已承诺事项、负责人、跟进时间和风险。不要把内部提议写成对客户的承诺。',
        'evidence_required': True, 'language': 'zh-CN', 'builtin': True,
    },
)


def builtin_templates():
    return [{**template, 'sections': list(template['sections'])} for template in BUILTINS]


def find_builtin(template_id):
    return next((template for template in builtin_templates()
                 if template['template_id'] == template_id), None)


def _text(value, field, low=1, high=1000):
    if not isinstance(value, str):
        raise ValueError(f'{field}_invalid')
    value = value.strip()
    if not low <= len(value) <= high:
        raise ValueError(f'{field}_invalid')
    return value


def validate_custom(data, *, template_id=None, version=1):
    if not isinstance(data, dict):
        raise ValueError('template_invalid')
    name = _text(data.get('name'), 'name', 1, 40)
    instructions = _text(data.get('instructions'), 'instructions', 1, 1000)
    sections = data.get('sections', DEFAULT_SECTIONS)
    if (not isinstance(sections, list) or not sections
            or len(sections) > len(SECTIONS)
            or any(section not in SECTIONS for section in sections)
            or len(set(sections)) != len(sections)):
        raise ValueError('sections_invalid')
    return {
        'template_id': template_id or 'custom-'+secrets.token_hex(4),
        'version': int(version),
        'name': name,
        'description': _text(data.get('description', ''), 'description', 0, 200),
        'sections': sections,
        'instructions': instructions,
        'evidence_required': True,
        'language': 'zh-CN',
        'builtin': False,
    }


def clean_template_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9_-]{1,40}', value):
        raise ValueError('template_invalid')
    return value
