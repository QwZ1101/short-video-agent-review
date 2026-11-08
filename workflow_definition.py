"""Versioned, validated definitions for the supported local workflow executor."""
import hashlib
import json
from workflow_scheduler import validate_graph
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / 'workflows/tutorial-review.json'
TOOLS = {'materials': 'check_materials.py', 'video': 'prepare_video.py',
         'metrics': 'platform_metrics.calculate_metrics', 'analysis': 'review_tutorial.py',
         'verification': 'verify_tutorial.py'}
PROMPTS = {'content_reviewer': 'prompts/tutorial_review.md',
           'evidence_verifier': 'prompts/tutorial_verify.md',
           'review_reviser': 'prompts/tutorial_revise.md'}


def load_template(path=None):
    definition = json.loads(Path(path or DEFAULT).read_text(encoding='utf-8'))
    if definition.get('schema_version') != 1 or type(definition.get('version')) is not int or definition['version'] < 1:
        raise ValueError('工作流模板版本无效')
    if not isinstance(definition.get('id'), str) or not definition['id'].strip():
        raise ValueError('工作流模板缺少名称')
    tasks, agents = definition.get('tasks'), definition.get('agents')
    validate_graph(tasks)
    if {t['id'] for t in tasks} != set(TOOLS):
        raise ValueError('当前执行器要求五个已注册阶段')
    if not isinstance(agents, dict):
        raise ValueError('缺少 Agent 定义')
    expected_deps = {'materials': [], 'video': ['materials'], 'metrics': ['materials'], 'analysis': ['video', 'metrics'], 'verification': ['analysis']}
    by_id = {t['id']: t for t in tasks}
    for task in tasks:
        deps = expected_deps[task['id']]
        agent = agents.get(task.get('role'), {})
        if task.get('tool') != TOOLS[task['id']] or task['tool'] not in agent.get('tools', []):
            raise ValueError('工具未注册或不属于该角色')
        if not set(deps) <= set(task['depends_on']):
            raise ValueError('工作流依赖与执行器不兼容')
        for field in ('inputs', 'outputs'):
            if not isinstance(task.get(field), list) or not task[field] or not all(isinstance(v, str) and v for v in task[field]):
                raise ValueError('任务输入输出定义无效')
        if not isinstance(task.get('name'), str) or not task['name'].strip():
            raise ValueError('任务缺少显示名称')
    for role, prompt in PROMPTS.items():
        if agents.get(role, {}).get('prompt') != prompt or agents[role].get('kind') != 'model':
            raise ValueError('模型角色与提示词绑定无效')
        if not (ROOT / prompt).is_file():
            raise ValueError('角色提示词文件不存在')
    if by_id['analysis']['role'] != 'content_reviewer' or by_id['verification']['role'] != 'evidence_verifier':
        raise ValueError('分析与验证必须由各自角色执行')
    policy = definition.get('feedback_policy', {})
    if (type(policy.get('max_rounds')) is not int or not 0 <= policy['max_rounds'] <= 2
            or policy.get('calls_per_round') != 2 or policy.get('stop_on_insufficient_evidence') is not True):
        raise ValueError('反馈策略必须保留补证停止、双调用预算和最多两轮限制')
    workers = definition.get('scheduler', {}).get('max_workers', 1)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('工作线程数必须在 1 到 4 之间')
    return definition


def template_identity(definition):
    serialized = json.dumps(definition, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return {'id': definition['id'], 'version': definition['version'],
            'sha256': hashlib.sha256(serialized).hexdigest(),
            'prompt_sha256': {role: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                              for role, path in PROMPTS.items()}}


def role_prompt(role):
    definition = load_template()
    return (ROOT / definition['agents'][role]['prompt']).read_text(encoding='utf-8')
