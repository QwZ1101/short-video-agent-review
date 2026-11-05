"""Read-only lineage projection; never modifies cycle records or calls a model."""
import math
from urllib.parse import urlencode
from creation_cycles import METRICS, measured, timestamp
from human_review import digest


def overview(store, identifier, hours=None):
    selected = store.load(identifier)
    ancestors, seen = [], set()
    record = selected
    while True:
        if record['id'] in seen or len(ancestors) >= 100:
            raise ValueError('创作链包含循环引用或超过100轮')
        seen.add(record['id']); ancestors.append(record)
        if not record.get('parent_id'): break
        parent = store.load(record['parent_id'])
        if not parent.get('target') or parent['target']['sample_id'] != record['source']['sample_id']:
            raise ValueError('创作链前后作品关联不一致')
        record = parent
    path = list(reversed(ancestors))
    warnings, children = [], {}
    files = sorted(store.folder.glob('cycle-*/record.json'))
    if len(files) > 200: warnings.append('只读取前200轮，创作链可能不完整。')
    for file in files[:200]:
        try:
            item = store.load(file.parent.name)
            if item.get('parent_id'): children.setdefault(item['parent_id'], []).append(item)
        except (ValueError, OSError, KeyError, TypeError):
            warnings.append('一条历史轮次无法校验，未并入创作链。')
    branches = []
    while len(path) < 100:
        next_items = sorted(children.get(path[-1]['id'], []), key=lambda r:(r['created_at'],r['id']))
        if len(next_items) != 1:
            branches = [dict(id=r['id'], title=r['title']) for r in next_items]; break
        child = next_items[0]
        if child['id'] in seen: raise ValueError('创作链包含循环引用')
        if not path[-1].get('target') or path[-1]['target']['sample_id'] != child['source']['sample_id']:
            raise ValueError('创作链前后作品关联不一致')
        seen.add(child['id']); path.append(child)
    root = path[0]
    window = root['window_hours'] if hours is None else hours
    if type(window) not in (int,float) or not math.isfinite(window) or not 0 < window <= 2160:
        raise ValueError('观察窗口必须是0到2160之间的小时数')
    works = {s['sample_id']:s for s in store.jobs.samples()['samples']}
    nodes, by_id, candidates = [], {}, {}

    def add(sample_id, cycle, goal, changes=None):
        if sample_id not in by_id:
            node = dict(sample_id=sample_id,title=works.get(sample_id,{}).get('title',sample_id),
                cycle_id=cycle['id'],changes=[],review_run=None)
            by_id[sample_id]=node; nodes.append(node); candidates[sample_id]=[]
        node=by_id[sample_id]
        if changes: node['changes'].extend(c for c in changes if c not in node['changes'])
        if cycle['source']['sample_id']==sample_id: node['review_run']=cycle['source']['run']
        comparison=cycle.get('comparison') or {}
        side='a' if cycle['source']['sample_id']==sample_id else 'b'
        snap=comparison.get(side)
        if snap:
            candidates[sample_id].append(dict(snapshot=snap,published=comparison.get('published_'+side),goal=goal,
                signature=comparison.get('record_hashes',{}).get(snap['source']['record_id'])))

    for cycle in path:
        add(cycle['source']['sample_id'],cycle,cycle['source']['goal'])
        if cycle.get('target'):
            applied=[e['note'] for e in cycle['target']['executions'] if e['status']!='not_applied']
            add(cycle['target']['sample_id'],cycle,cycle['target']['goal'],applied)
    tolerance=root['tolerance_minutes']/60
    review_runs=store.reviews.list_runs()['runs']
    for node in nodes:
        sid=node['sample_id']; choices=candidates[sid]
        if not node['review_run']:
            node['review_run']=next((r['id'] for r in review_runs if r['sample_id']==sid),None)
        node['cover_url']=None
        if node['review_run']:
            try:
                detail=store.reviews.detail(node['review_run'])
                first=next((key for key,value in detail['catalog'].items() if value.get('type')=='video_frame'),None)
                if first: node['cover_url']='/api/evidence?'+urlencode(dict(run=node['review_run'],id=first))
            except (ValueError,OSError,KeyError,TypeError): pass
        if not choices:
            try:
                binding=store.jobs.materials(sid)
                if binding['metrics_record_id']:
                    snap=store.metrics.for_workflow(binding['metrics_record_id'],sid,True)
                    choices=[dict(snapshot=snap,published=snap.get('published_at'),goal=works.get(sid,{}).get('goal'),signature=None)]
            except (ValueError,OSError,KeyError,TypeError):
                warnings.append(node['title']+' 的绑定数据无法校验。')
        valid, excluded = {}, []
        for choice in choices:
            snap=choice['snapshot']; mid=snap['source']['record_id']
            reasons=[]
            try:
                live=store.metrics.for_workflow(mid,sid,True)
                if choice['signature'] and digest(store.metrics.root(mid)/'record.json')!=choice['signature']:
                    raise ValueError('确认记录已变化')
                if live != snap: raise ValueError('截图快照已变化')
            except (ValueError,OSError,KeyError,TypeError):
                excluded.append(dict(reason='数据来源无法校验',snapshot=None)); continue
            age=None
            try: age=(timestamp(snap['observed_at'])-timestamp(choice['published'])).total_seconds()/3600
            except ValueError: reasons.append('缺少已确认的发布时间')
            if snap['scope']!='cumulative': reasons.append('统计范围不是截至截图累计')
            if age is not None and abs(age-window)>tolerance+1e-9: reasons.append('观察窗口不一致')
            if choice['goal']!=root['source']['goal']: reasons.append('目标口径不同')
            candidate=dict(snapshot=snap,published_at=choice['published'],actual_hours=round(age,4) if age is not None else None)
            if reasons: excluded.append(dict(reason='；'.join(reasons),**candidate))
            else: valid[(mid,choice['published'])]=candidate
        node.update(status='missing',reason='待采集已确认数据',values={k:None for k in METRICS},record_id=None,actual_hours=None)
        candidate=None
        if len(valid)==1:
            candidate=next(iter(valid.values()));node.update(status='comparable',reason='同目标、同观察窗口')
        elif len(valid)>1:
            node.update(status='conflict',reason='同一窗口有多份不同记录，请在单轮详情核对；总览未自动挑选')
        elif excluded:
            candidate=next((c for c in excluded if c.get('snapshot')),None)
            node.update(status='unaligned',reason='；'.join(dict.fromkeys(c['reason'] for c in excluded)))
        if candidate:
            snap=candidate['snapshot'];mid=snap['source']['record_id']
            node.update(values={k:round(measured(snap['metrics'],k),4) if measured(snap['metrics'],k) is not None else None for k in METRICS},
                record_id=mid,published_at=candidate['published_at'],observed_at=snap['observed_at'],
                actual_hours=candidate['actual_hours'],approximate=bool(snap['approximate_fields']))
        node['record_options']=[dict(id=v['snapshot']['source']['record_id'],observed_at=v['snapshot']['observed_at']) for v in valid.values()]
        node['previous_compatible']=False
    for previous,node in zip(nodes,nodes[1:]):
        node['previous_compatible']=node['status']==previous['status']=='comparable' and abs(node['actual_hours']-previous['actual_hours'])<=tolerance+1e-9
    if not path[-1].get('target') and path[-1]['status']!='closed':
        nodes.append(dict(sample_id=None,title=path[-1]['title'],status='planned',reason='下一条待创作',values={k:None for k in METRICS},
            cycle_id=path[-1]['id'],record_id=None,review_run=None,changes=[i['planned_change'] for i in path[-1]['items']],previous_compatible=False))
    return dict(root_id=root['id'],selected_id=identifier,window_hours=window,tolerance_minutes=root['tolerance_minutes'],
        goal=root['source']['goal'],windows=sorted(set([r['window_hours'] for r in path]+[window])),metrics=METRICS,nodes=nodes,branches=branches,
        cycles=[dict(id=r['id'],title=r['title'],status=r['status']) for r in path],warnings=list(dict.fromkeys(warnings)),
        limitation='不同视频的观察性趋势，题材、时长和流量来源等差异仍可能影响结果，不能据此认定某项建议产生了效果。')
