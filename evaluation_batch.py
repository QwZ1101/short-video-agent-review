"""Audit all recorded attempts and prepare latest real paired cases without API calls."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from evaluate_reviews import prepare, read, sha
from local_storage import atomic_json
from workflow_logs import redact


def audit(workspace, output):
    if output.exists():
        raise ValueError('不会覆盖已有评测记录')
    output.mkdir(parents=True)
    attempts, invalid = [], []
    for path in sorted((workspace / 'reports/workflows').glob('*/workflow.json')):
        try:
            state = read(path)
            attempts.append({'run': path.parent.name, 'state_sha256': sha(path),
                'mode': state.get('mode'), 'sample_id': state.get('sample_id'),
                'status': state.get('status'), 'started_at': state.get('started_at'),
                'model_stage_attempts': state.get('model_stage_attempts'),
                'failure_stage': next((s['id'] for s in state.get('stages', []) if s.get('status') == 'failed'), None)})
        except (ValueError, OSError, KeyError, TypeError) as error:
            invalid.append({'run': path.parent.name, 'error': redact(str(error))})
    live = [a for a in attempts if a['mode'] == 'live']
    successful = [a for a in live if a['status'] in ('awaiting_human_review', 'human_reviewed')]
    failed = [a for a in live if a['status'] == 'failed']
    terminal = len(successful) + len(failed)
    latest = {}
    for item in sorted(successful, key=lambda a: a['started_at'] or ''):
        latest[item['sample_id']] = item
    cases = []
    for sample_id, item in latest.items():
        root = workspace / 'reports/workflows' / item['run']
        try:
            state = read(root / 'workflow.json')
            snapshot = read(root / 'sample.json')
            current = read(workspace / 'data/samples' / (sample_id + '.json'))
            if snapshot['tutorial_goal'] != current['tutorial_goal']:
                raise ValueError('目标已改变，旧目标结果不进入当前案例包')
            metric_id = (snapshot.get('platform_metrics') or {}).get('source', {}).get('record_id')
            if metric_id:
                from screenshot_metrics import ScreenshotStore
                if ScreenshotStore(workspace).read(metric_id)['status'] == 'invalidated':
                    raise ValueError('截图归属已被人工纠正，该历史输入错配，不能作为正常质量案例')
            paths = state['artifacts']
            packet = output / sample_id
            prepare(Path(paths['analysis_dir']), Path(paths['verification_dir']), packet, snapshot)
            comparison = read(packet / 'comparison.json')
            before = read(packet / 'single_analysis.json')
            after = read(packet / 'analysis_plus_verification.json')
            old = {f['id']: f for f in before['findings']}
            new = {f['id']: f for f in after['findings']}
            cases.append({'sample_id': sample_id, 'run': item['run'], 'status': 'prepared',
                'packet': sample_id + '/review-packet.md', 'resources': {
                    k: v['resources'] for k, v in comparison['arms'].items()},
                'quality_status': comparison['status'], 'input_scope': 'historical_run_snapshot',
                'observed_changes': {'summary_changed': before['summary'] != after['summary'],
                    'findings_before': len(old), 'findings_after': len(new),
                    'modified_findings': sum(old[k] != new[k] for k in old.keys() & new.keys()),
                    'added_findings': len(new.keys() - old.keys()), 'removed_findings': len(old.keys() - new.keys())}})
        except (ValueError, OSError, KeyError, TypeError) as error:
            cases.append({'sample_id': sample_id, 'run': item['run'], 'status': 'excluded', 'reason': redact(str(error))})
    result = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'design': 'retrospective_local_development_audit', 'new_model_calls': 0,
        'attempts': attempts, 'unreadable_records': invalid, 'cases': cases,
        'live_attempts': len(live), 'terminal_live_attempts': terminal,
        'successful_live_attempts': len(successful), 'failed_live_attempts': len(failed),
        'recorded_technical_completion_rate': len(successful) / terminal if terminal else None,
        'failure_stages': dict(Counter(a['failure_stage'] or 'unknown' for a in failed)),
        'limits': ['仅统计当前磁盘保留记录，无法证明涵盖所有历史尝试；不同代码版本和目标混合，不代表当前版本成功率。',
                   '完成指产生结构有效、待人工复核的结果，不代表建议准确或互动提升。',
                   '每个样本仅准备最新成功案例；全部成功失败尝试另行保留，不用筛选后的案例计算完成率。',
                   '组别公开，使用运行时快照；非盲评、非独立留出集，人工质量标注未完成。']}
    atomic_json(output / 'audit.json', result)
    lines = ['# 真实案例对照评测', '', '本次新增模型调用：0。质量结论待实际人工评判。', '',
             f'本地保留的 live 记录：{len(live)} 条；完成 {len(successful)} 条；失败 {len(failed)} 条；其余 {len(live)-terminal} 条。',
             '', '历史技术完成比例：' + (f'{len(successful)}/{terminal} = {len(successful)/terminal:.1%}' if terminal else '未知'),
             '', '该比例混合旧版本，仅描述磁盘记录，不能作为当前版本性能宣传。', '',
             '| 样本 | 对照材料 | 质量结果 |', '| --- | --- | --- |']
    for case in cases:
        lines.append(f"| {case['sample_id']} | " +
                     (f"[评判材料]({case['packet']}) · [资源对比]({case['sample_id']}/comparison.md) | 待人工标注 |" if case['status'] == 'prepared'
                      else f"排除：{case['reason']} | 未计分 |"))
    lines += ['', '## 可复算的资源与文本变化', '',
              '下表只记录历史调用及实际文字变化。修改数量不表示修改正确，也不表示质量提升。', '',
              '| 样本 | 初稿建议数 | 改动建议数 | 初稿模型秒数 | 分析加验证模型秒数 | 初稿 tokens | 分析加验证 tokens |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    for case in cases:
        if case['status'] != 'prepared':
            continue
        a, b = case['resources']['single_analysis'], case['resources']['analysis_plus_verification']
        change = case['observed_changes']
        def shown(value):
            return '未知' if value is None else str(value)
        lines.append('| ' + ' | '.join(map(str, [case['sample_id'], change['findings_before'], change['modified_findings'],
            shown(a['model_seconds']), shown(b['model_seconds']), shown(a['tokens']['total_tokens']), shown(b['tokens']['total_tokens'])])) + ' |')
    lines += ['', '## 统计边界', ''] + ['- ' + text for text in result['limits']]
    lines += ['', '完整尝试清单、源记录指纹及失败阶段见 [audit.json](audit.json)。每个对照包保留原图、输出和独立人工草稿；已有人工复核不能自动当作两组质量评分。']
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.workspace / 'reports/evaluations' / ('batch-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    result = audit(args.workspace, output)
    print(str(output / 'report.md'))
    print(json.dumps({'attempts': len(result['attempts']), 'prepared': sum(c['status'] == 'prepared' for c in result['cases'])}))
