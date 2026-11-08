"""Summarize original packets once, honoring explicitly documented exclusions."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from evaluate_reviews import ARMS, read, score
from local_storage import atomic_json


def summarize(batch, output):
    exclusion_path = batch / 'case-exclusions.json'
    exclusions = read(exclusion_path)['cases'] if exclusion_path.exists() else {}
    packets = {p.name: score(p) for p in sorted(batch.glob('tutorial-*')) if p.is_dir()}
    valid = {k: v for k, v in packets.items() if k not in exclusions}
    completed = [k for k, v in valid.items() if v['annotation_coverage_complete']]
    aggregate = {}
    for arm in ARMS:
        aggregate[arm] = {}
        for kind in ('findings', 'citations'):
            values = [v['arms'][arm][kind] for v in valid.values()]
            counts = {key: sum(v[key] for v in values) for key in ('total', 'judged', 'pending', 'supported', 'revise', 'insufficient_evidence')}
            counts.update(coverage=counts['judged'] / counts['total'] if counts['total'] else None,
                          supported_proportion=counts['supported'] / counts['judged'] if counts['judged'] else None)
            aggregate[arm][kind] = counts
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'source_batch': str(batch.resolve()),
              'completed_packets': completed, 'excluded_packets': exclusions, 'eligible_packets': list(valid),
              'aggregate': aggregate, 'packets': {k: {'status': v['status'], 'annotation_coverage_complete': v['annotation_coverage_complete']} for k, v in packets.items()},
              'selection': 'original packets only; excludes snapshots and separately listed invalid-input packets', 'new_model_calls': 0}
    atomic_json(output / 'human-evaluation-summary.json', result)
    lines = ['# 真实人工质量评测进度', '',
             f'原始 {len(packets)} 个案例中，{len(exclusions)} 个输入错配已单列；其余 {len(valid)} 个正常质量案例中，{len(completed)} 个已完成完整人工评判并冻结。新增模型调用为 0。', '',
             '## 完整案例的建议支持比例', '', '| 案例 | 初稿 | 验证稿 | 建议及引用覆盖 | 总结 |', '| --- | --- | --- | --- | --- |']
    for name in completed:
        v = packets[name]
        a, b = [v['arms'][arm]['findings'] for arm in ARMS]
        lines.append('| %s | %s/%s（%.1f%%） | %s/%s（%.1f%%） | 100%% | 初稿：%s；验证稿：%s |' % (
            name, a['supported'], a['judged'], 100*a['supported_proportion'], b['supported'], b['judged'],
            100*b['supported_proportion'], v['arms'][ARMS[0]]['summary_verdict'], v['arms'][ARMS[1]]['summary_verdict']))
    lines += ['', '## 已判断部分汇总与正常案例覆盖', '',
              '| 对照组 | 建议支持 / 已判断 | 已评建议 / 总建议 | 引用支持 / 已判断 | 已评引用 / 总引用 |', '| --- | --- | --- | --- | --- |']
    def percent(value):
        return '未知' if value is None else '%.1f%%' % (100*value)
    for arm, label in zip(ARMS, ['初稿', '验证稿']):
        a, b = aggregate[arm]['findings'], aggregate[arm]['citations']
        lines.append('| %s | %s/%s（%s） | %s/%s（%s） | %s/%s（%s） | %s/%s（%s） |' % (
            label, a['supported'], a['judged'], percent(a['supported_proportion']), a['judged'], a['total'], percent(a['coverage']),
            b['supported'], b['judged'], percent(b['supported_proportion']), b['judged'], b['total'], percent(b['coverage'])))
    lines += ['', '## 输入错配记录', '']
    for name, exclusion in exclusions.items():
        lines += ['- %s：%s。原始证据和已确认判断保留，不纳入正常输出质量比例；应使用正确数据另建新运行。' % (name, exclusion['reason'])]
    lines += ['', '## 解读范围', '',
              '- 引用支持按人工确认的可见内容或字幕文字口径计分，不表示引用证明了整条因果、实际功能或观众行为。',
              '- 这是组别公开、助手辅助整理证据的回顾性开发评估。已完成案例不能代表未评案例、泛化准确率或互动提升。',
              '- 旧局部快照不作为独立案例累计。输入错配只从正常质量分母移出，原始批次清单与技术尝试审计保留。', '']
    for name in completed:
        lines.append('[%s 完整计分](<%s>)' % (name, (batch / name / 'comparison.md').resolve().as_posix()))
    (output / 'human-evaluation-summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.batch.resolve(), args.output.resolve())
    print('completed=%s excluded=%s eligible=%s' % (len(result['completed_packets']), len(result['excluded_packets']), len(result['eligible_packets'])))
