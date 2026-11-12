"""Start an isolated synthetic replay demo; no model/API calls or real user data."""
import argparse
import copy
import json
from pathlib import Path
import webbrowser

from check_materials import PROJECT_ROOT
from human_review import atomic_json, digest
from run_workflow import new_state, run_replay, create_handoff
from review_server import ReviewServer


def prepare(workspace):
    workspace = Path(workspace).resolve()
    source = workspace / 'source'
    root = workspace / 'reports/workflows/run-demo'
    if (root / 'workflow.json').exists():
        return root
    source.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    initial = dict(sample_id='demo-tutorial', summary='合成演示：缺少引导导致点赞减少。',
        findings=[dict(id='finding-1', observation='提供的结尾字幕为“谢谢观看”。',
            interpretation='缺少引导导致点赞减少。', suggestion='下一条结尾增加点赞提示。',
            validation_plan='比较下一条与本条在发布后 24 小时的每百次播放点赞数。',
            evidence_ids=['subtitle-demo'], confidence='medium')],
        limitations=['合成演示数据，未调用模型，不代表真实作品或真实评测。'])
    revised = copy.deepcopy(initial)
    revised['summary'] = '合成演示：所提供结尾字幕未见点赞引导，其影响仍需验证。'
    revised['findings'][0]['interpretation'] = '增加引导可能影响点赞意愿，现有字幕不能证明因果。'
    bundle = dict(task=dict(sample_id='demo-tutorial', title='合成演示作品', tutorial_goal='点赞',
        audience='对视觉效果感兴趣的观众', prior_knowledge='无需编程基础', platform_metrics=None),
        coverage=dict(selected_frames=0, available_frames=0, subtitle_count=1, image_count=0),
        catalog={'subtitle-demo': dict(type='text', text='合成字幕：谢谢观看。')})
    check = dict(status='revise', reason='合成反馈：字幕只能说明文字内容，不能证明点赞减少的原因。', evidence_ids=['subtitle-demo'])
    verification = dict(sample_id='demo-tutorial', access_check=dict(creator_side='未提供',
        viewer_side='未提供入口材料，无法判断', evidence_ids=['subtitle-demo']),
        summary_check=check, checks=[dict(check, finding_id='finding-1')],
        revised_review=revised, limitations=initial['limitations'])
    for name, value in [('original-review.json', initial), ('revised-review.json', revised),
                        ('verification.json', verification), ('evidence-catalog.json', bundle)]:
        atomic_json(source / name, value)
    atomic_json(source / 'run.json', dict(status='completed_structure_valid', model='synthetic-offline-demo',
        review_sha256=digest(source / 'original-review.json'), evidence_sha256=digest(source / 'evidence-catalog.json')))
    for name in ['prompt.md', 'model-output.txt']:
        (source / name).write_text('Synthetic fixture. No model called. Not a quality evaluation.', encoding='utf-8')
    state = new_state('replay', 0, 'demo-tutorial')
    paths = run_replay(root, state, source)
    create_handoff(root, state, paths)
    atomic_json(root.parent / 'latest.json', dict(run_dir=str(root), status=state['status']))
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=PROJECT_ROOT / 'reports/offline-demo')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    root = prepare(args.workspace)
    if args.prepare_only:
        print(root)
        return
    server = ReviewServer(workspace=args.workspace.resolve())
    url = 'http://127.0.0.1:%s/?run=run-demo' % server.server_port
    print('Synthetic replay demo; no API calls. Close this process to stop.', flush=True)
    print(url, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
