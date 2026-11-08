"""Prepare readable evidence-linked review queues without changing human annotations."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_reviews import ARMS, LABELS, load_packet, read, sha
from local_storage import atomic_json


def prepare_queue(batch, output):
    if output.exists():
        raise ValueError('核对材料目录已存在，不覆盖历史记录')
    packets = []
    for root in sorted(batch.glob('tutorial-*')):
        if not root.is_dir():
            continue
        manifest, reviews = load_packet(root)
        bundle = read(root / 'evidence-catalog.json')
        packets.append((root, manifest, reviews, bundle))
    if not packets:
        raise ValueError('未找到可校验的对照包')
    output.mkdir(parents=True)
    queue, counts = [], {'summaries': 0, 'findings': 0, 'citations': 0}
    index = ['# 人工质量核对清单', '', '每次在聊天里核对一项，不需要编辑 JSON。助手可以整理证据和提出待确认意见；只有你实际确认的判断才写入人工记录。', '',
             '三个判断：有证据支持、需要修改、证据不足。每项需记录理由。资料不足是有效判断；尚未评判保持空白。', '',
             '| 案例 | 核对材料 | 建议项（两组） | 引用项（两组） |', '| --- | --- | --- | --- |']
    for root, manifest, reviews, bundle in packets:
        lines = ['# ' + root.name + ' 人工核对', '', '目标：' + bundle['task']['tutorial_goal'], '',
                 '本页仅整理冻结内容。引用存在不表示引用正确；修改过不表示质量提高。历史样本可能目标不同，应按本页目标判断。', '',
                 '## 两组完整结果', '']
        local_counts = {'findings': 0, 'citations': 0}
        for arm in ARMS:
            review = reviews[arm]
            lines += ['### ' + LABELS[arm], '', '**总结**：' + review['summary'], '']
            queue.append({'packet': root.name, 'arm': arm, 'kind': 'summary', 'target': 'summary', 'verdict': None})
            counts['summaries'] += 1
            for finding in review['findings']:
                lines += ['#### ' + finding['id'], '']
                for key, label in [('observation', '观察'), ('interpretation', '影响判断'), ('suggestion', '建议'), ('validation_plan', '验证方法')]:
                    lines += ['**' + label + '**：' + finding[key], '']
                lines += ['引用：' + '、'.join('[%s](#%s)' % (ref, ref) for ref in finding['evidence_ids']), '',
                          '待判断：整条建议是否准确、符合目标、推断有边界、验证方式可执行？每个引用是否支持对应说法？', '']
                queue.append({'packet': root.name, 'arm': arm, 'kind': 'finding', 'target': finding['id'], 'verdict': None})
                local_counts['findings'] += 1
                for ref in dict.fromkeys(finding['evidence_ids']):
                    queue.append({'packet': root.name, 'arm': arm, 'kind': 'citation', 'target': finding['id'], 'evidence_id': ref, 'verdict': None})
                    local_counts['citations'] += 1
        lines += ['## 冻结证据', '', '画面时间是抽帧请求位置，不是精确帧时间；采样不能代替连续观看，也没有听取音频。', '']
        for identifier, item in bundle['catalog'].items():
            lines += ['### ' + identifier, '']
            if 'local_path' in item:
                image_path = (root / item['local_path']).resolve()
                lines += ['![%s](<%s>)' % (identifier, image_path.as_posix()), '']
            if 'requested_time_seconds' in item:
                lines += ['抽帧请求位置：%s 秒。' % item['requested_time_seconds'], '']
            if item.get('type') == 'calculated_metrics':
                lines += ['已记录指标：`' + json.dumps(item.get('reported_metrics', item.get('source')), ensure_ascii=False) + '`', '']
                lines += ['- ' + s for s in item.get('limitations', [])]
                lines += ['']
            elif 'local_path' not in item:
                lines += ['```json', json.dumps(item, ensure_ascii=False, indent=2), '```', '']
        (output / (root.name + '.md')).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        index += ['| %s | [打开](%s.md) | %s | %s |' % (root.name, root.name, local_counts['findings'], local_counts['citations'])]
        for key in local_counts:
            counts[key] += local_counts[key]
    session = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
               'status': 'awaiting_human', 'batch': str(batch.resolve()),
               'counts': counts, 'quality_scores': None, 'queue': queue,
               'source_annotation_hashes': {root.name: sha(root / 'annotations.draft.json') for root, *_ in packets},
               'first_item': {'packet': 'tutorial-005', 'arm': 'single_analysis', 'kind': 'finding',
                              'target': 'finding-2', 'status': 'awaiting_human',
                              'assistant_suggested_verdict': 'revise',
                              'assistant_reason': '汇总完播率无法证明技术过程导致流失；采样画面包含成品和修正说明，不能全部概括为代码细节。'}}
    index += ['', '总计：%s 项总结、%s 项建议、%s 项引用。逐项确认，支持比例只使用已提交的人工判断，并同时报告覆盖率。' % tuple(counts.values()), '',
              '建议从 tutorial-005 的初稿 finding-2 开始。先核对初稿，再单独判断验证候选稿；不会因为你确认初稿有错就自动认定候选稿正确。']
    (output / 'index.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    atomic_json(output / 'review-session.json', session)
    return session


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = prepare_queue(args.batch.resolve(), args.output.resolve())
    print(json.dumps({'counts': result['counts'], 'status': result['status']}, ensure_ascii=False))
    print(str(args.output.resolve() / 'index.md'))
