"""One bounded LLM planning call; only validated registered actions are executable."""
import copy
import json
import time
from pathlib import Path

from workflow_scheduler import validate_graph

FOCUS = {
    'opening': '优先检查开头可见吸引点和目标观众是否能迅速理解内容；采样不能证明未采样部分不存在。',
    'action': '优先检查与目标一致的行动引导和已提供的评论、简介证据；不要强制下载源码。',
    'clarity': '优先检查讲解顺序和目标观众理解成本；区分可见观察与推测。',
    'balanced': '平衡检查展示、理解与目标行动；所有结论受证据覆盖限制。',
}


def validate_proposal(value, baseline):
    fields = {'sample_id', 'focus', 'sampling_interval', 'max_workers', 'reason', 'tasks'}
    if not isinstance(value, dict) or set(value) != fields or value['sample_id'] != baseline['sample_id']:
        raise ValueError('规划格式或样本 ID 不正确')
    if value['focus'] not in FOCUS or type(value['sampling_interval']) is not int or value['sampling_interval'] not in (2, 4, 6):
        raise ValueError('规划分析重点或采样密度越界')
    if type(value['max_workers']) is not int or value['max_workers'] not in (1, 2):
        raise ValueError('规划并行上限越界')
    if value['max_workers'] > baseline['workflow_definition'].get('scheduler', {}).get('max_workers', 1):
        raise ValueError('规划不能扩大模板的资源上限')
    def reason(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('规划必须提供最多 500 字的理由')
    reason(value['reason'])
    validate_graph(value['tasks'])
    originals = {t['id']: t for t in baseline['tasks']}
    if {t['id'] for t in value['tasks']} != set(originals):
        raise ValueError('规划不能新增或删除必需阶段')
    tasks = []
    for proposed in value['tasks']:
        if set(proposed) != {'id', 'tool', 'action', 'depends_on', 'priority', 'reason'}:
            raise ValueError('规划阶段字段不正确')
        original = originals[proposed['id']]
        if proposed['tool'] != original['tool'] or proposed['action'] != original['action']:
            raise ValueError('规划试图执行未授权工具或跳过必要步骤')
        if not set(original['depends_on']) <= set(proposed['depends_on']):
            raise ValueError('规划不能移除证据依赖')
        if type(proposed['priority']) is not int or not -10 <= proposed['priority'] <= 10:
            raise ValueError('规划优先级越界')
        reason(proposed['reason'])
        tasks.append(dict(original, **proposed))
    result = copy.deepcopy(baseline)
    result.update(planner='llm_constrained_v1', tasks=tasks,
                  intelligent_strategy={k: value[k] for k in ('focus', 'sampling_interval', 'max_workers', 'reason')})
    return result


def plan_with_model(root, sample, baseline, model):
    from review_tutorial import call_dashscope, write_json, ModelNetworkError
    from workflow_definition import role_prompt
    prompt = role_prompt('workflow_planner')
    directory = Path(root) / 'planning'
    directory.mkdir()
    # No paths, images, account identities or credentials enter the planning context.
    context = {'sample_id': sample['sample_id'], 'goal': sample['tutorial_goal'],
               'audience': sample.get('audience'), 'prior_knowledge': sample.get('audience_prior_knowledge'),
               'has_subtitles': bool(sample.get('transcript_path')), 'has_platform_data': baseline['platform_data']['status'] == 'completed',
               'supporting_material_count': len(sample.get('supporting_materials', [])),
               'allowed_tasks': baseline['tasks']}
    payload = {'model': model, 'enable_thinking': False, 'stream': True,
               'stream_options': {'include_usage': True}, 'max_tokens': 2400,
               'messages': [{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]}
    write_json(directory / 'input.json', context)
    (directory / 'prompt.md').write_text(prompt, encoding='utf-8')
    metadata = {'status': 'running', 'model': model, 'automatic_retries': 0}
    write_json(directory / 'run.json', metadata)
    started = time.monotonic()
    try:
        response = call_dashscope(payload)
        choice = response['choices'][0]
        content = choice['message']['content']
        (directory / 'model-output.txt').write_text(content, encoding='utf-8')
        metadata.update(usage=response.get('usage'), response_id=response.get('id'), finish_reason=choice.get('finish_reason'))
        if choice.get('finish_reason') != 'stop':
            raise ValueError('规划回答不完整，未执行工具')
        result = validate_proposal(json.loads(content), baseline)
        write_json(directory / 'validated-plan.json', result)
        metadata['status'] = 'validated'
        return result
    except Exception as error:
        metadata.update(status='failed', error_type=type(error).__name__)
        if isinstance(error, ModelNetworkError):
            metadata['transport'] = error.transport
        raise
    finally:
        metadata['elapsed_seconds'] = round(time.monotonic() - started, 3)
        write_json(directory / 'run.json', metadata)
