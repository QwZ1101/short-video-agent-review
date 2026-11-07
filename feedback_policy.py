"""Route validated model feedback under deterministic resource constraints."""


def route_feedback(tasks, rounds, maximum, remaining):
    if not tasks or any(t.get('status') not in ('supported', 'revise', 'insufficient_evidence') for t in tasks):
        raise ValueError('验证反馈为空或包含未知状态，不能判定通过')
    if any(type(n) is not int or n < 0 for n in (rounds, maximum, remaining)):
        raise ValueError('调度轮数及预算必须为非负整数')
    counts = {s: sum(t['status'] == s for t in tasks)
              for s in ('supported', 'revise', 'insufficient_evidence')}
    if counts['insufficient_evidence']:
        action, reason = 'awaiting_evidence', '存在缺证项；继续生成不能补充事实，转人工补证'
    elif counts['supported'] == len(tasks):
        action, reason = 'supported', '所有检查均获证据支持，交人工复核'
    elif rounds >= maximum:
        action, reason = 'round_limit', '达到允许返修轮数，交人工处理剩余项'
    elif remaining < 2:
        action, reason = 'budget_limit', '剩余调用不足以完成修正和独立复验'
    else:
        action, reason = 'repair', '有可修正项且证据足够，预算允许修正后再次验证'
    return {'policy': 'verification_feedback_v1', 'action': action, 'reason': reason,
            'feedback_counts': counts, 'rounds_completed': rounds, 'max_rounds': maximum,
            'remaining_calls': remaining,
            'selected_roles': ['review_reviser', 'evidence_verifier'] if action == 'repair' else [],
            'repair_targets': [t.get('target') for t in tasks if t['status'] == 'revise'] if action == 'repair' else [],
            'human_review_required': True}
