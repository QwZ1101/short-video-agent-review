"""Cross-video learning records. Descriptive comparisons, never causal A/B claims."""
import copy
from datetime import datetime
import math
import re
import uuid

from human_review import LOCK, digest, atomic_json, within
from run_workflow import now, read_json
from screenshot_metrics import ScreenshotStore
from workflow_jobs import JobStore, event

METRICS = {'likes_per_100': '每百次播放点赞数', 'plays': '播放量', 'likes': '点赞量',
           'completion_rate': '完播率', 'bounce_2s_rate': '2秒跳出率'}
DECISIONS = {'keep': '继续保留', 'retry': '再试一条', 'drop': '暂不采用', 'inconclusive': '暂不能判断'}
LIMITS = ['这是不同视频的观察性比较，不是随机分流的 A/B 实验。',
         '题材、时长、受众、发布时间和流量来源都可能影响结果；数字变化不证明建议产生了效果。',
         '两项改动一起使用时不能区分各自贡献。这里不做统计显著性判断，也不自动标记建议验证通过。',
         '评论量不能代替索要链接人数；截图没有该人数时保持未知。']


def text(value, label, required=True, limit=5000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('请填写有效的' + label)
    return value.strip()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('发布时间必须包含完整日期、时间和时区') from None


def measured(values, key):
    if key == 'likes_per_100':
        plays, likes = values.get('plays'), values.get('likes')
        return likes / plays * 100 if plays and likes is not None else None
    return values.get(key)


def compare(plan, a, b, published_a, published_b):
    """Inputs are confirmed screenshot snapshots. Require both ages near the frozen target."""
    pa, pb = timestamp(published_a), timestamp(published_b)
    ages = [(timestamp(x['observed_at']) - p).total_seconds() / 3600 for x, p in ((a, pa), (b, pb))]
    reasons = []
    tolerance = plan['tolerance_minutes'] / 60
    for label, item, age in zip(('A', 'B'), (a, b), ages):
        if item['scope'] != 'cumulative':
            reasons.append(label + ' 的截图需确认是自发布起累计；范围不明或指定时段暂不跨视频比较')
        if age < 0:
            reasons.append(label + ' 的截图时间早于发布时间')
        elif abs(age - plan['window_hours']) > tolerance + 1e-9:
            reasons.append('%s 实际观察 %.2f 小时，不在计划的 %s 小时 ± %s 分钟内' %
                           (label, age, plan['window_hours'], plan['tolerance_minutes']))
    if abs(ages[0] - ages[1]) > tolerance + 1e-9:
        reasons.append('A/B 实际观察时长之差超过预先登记的容差')
    primary = plan['primary_metric']
    if any(measured(x['metrics'], primary) is None for x in (a, b)):
        reasons.append('主指标缺失或播放量为零，无法计算；不能以零替代未知')
    retrospective = pb <= timestamp(plan['created_at'])
    rows = []
    if not reasons:
        for key, label in METRICS.items():
            av, bv = measured(a['metrics'], key), measured(b['metrics'], key)
            delta = bv - av if av is not None and bv is not None else None
            rows.append(dict(metric=key, label=label, a=round(av, 4) if av is not None else None,
                b=round(bv, 4) if bv is not None else None, delta=round(delta, 4) if delta is not None else None,
                delta_unit='个百分点' if key.endswith('_rate') else '每百次播放次数' if key == 'likes_per_100' else '次',
                relative_percent=round(delta / av * 100, 2) if av and delta is not None else None))
    limits = list(LIMITS)
    if retrospective:
        limits.append('B 在计划登记前已经发布，本轮属于回顾性比较，不能称为预先设计的改进试验。')
    if a['approximate_fields'] or b['approximate_fields']:
        limits.append('数据含万／亿缩写，差值由展示值换算，仅为近似值。')
    return dict(status='ready' if not reasons else 'blocked', reasons=reasons, rows=rows,
                actual_hours={'a': round(ages[0], 4), 'b': round(ages[1], 4)},
                retrospective=retrospective, limitations=limits)


class CycleStore:
    def __init__(self, reviews):
        self.reviews = reviews
        self.workspace = reviews.workspace
        self.folder = self.workspace / 'reports/creation-cycles'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.jobs = JobStore(self.workspace)
        self.metrics = ScreenshotStore(self.workspace)

    def root(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'cycle-[a-f0-9]{32}', identifier):
            raise ValueError('改进计划编号无效')
        root = (self.folder / identifier).resolve()
        if root.parent != self.folder.resolve():
            raise ValueError('改进计划路径无效')
        return root

    def source(self, run):
        detail = self.reviews.detail(run)
        feedback = detail['feedback']
        if feedback['status'] not in ('accepted', 'needs_evidence'):
            raise ValueError('请先提交人工复核，草稿不能生成下一条计划')
        root = self.reviews.root(run)
        history = within(root / 'human-review-history' / feedback['history_file'], root / 'human-review-history')
        if read_json(history) != feedback:
            raise ValueError('已提交复核与历史版本不一致')
        findings = [copy.deepcopy(f) for f in feedback['findings'] if f['decision'] in ('accept', 'revise')]
        if not findings:
            raise ValueError('请至少明确认可一条建议，暂不确定的建议不能加入计划')
        return dict(run=run, sample_id=feedback['sample_id'], revision=feedback['revision'],
                    sha256=digest(root / 'human-feedback.json'), goal=detail['task'].get('tutorial_goal'),
                    findings=findings)

    def sources(self):
        records = []
        for row in self.reviews.list_runs()['runs']:
            try:
                source = self.source(row['id'])
                records.append(dict(id=row['id'], sample_id=source['sample_id']))
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return dict(runs=records)

    def listing(self):
        records, unreadable = [], 0
        for path in sorted(self.folder.glob('cycle-*/record.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                r = self.load(path.parent.name)
                records.append({k: r.get(k) for k in ('id', 'title', 'source', 'target', 'status', 'revision', 'decisions')})
            except (ValueError, OSError, KeyError, TypeError):
                unreadable += 1
        return dict(records=records, metrics=METRICS, unreadable=unreadable)

    def load(self, identifier):
        with LOCK:
            root = self.root(identifier)
            record = read_json(root / 'record.json')
            history = record.get('history_file', '')
            if not re.fullmatch(r'revision-[0-9]+-[a-f0-9]+\.json', history):
                raise ValueError('改进计划历史路径无效')
            if read_json(root / 'history' / history) != record:
                raise ValueError('改进记录与历史版本不一致')
            if self.source(record['source']['run'])['sha256'] != record['source']['sha256']:
                raise ValueError('来源复核已改变，请重新建立计划')
            return record

    def commit(self, record, operation):
        root = self.root(record['id'])
        history = root / 'history'
        history.mkdir(parents=True, exist_ok=True)
        record = copy.deepcopy(record)
        record['revision'] += 1
        record['saved_at'] = now()
        record['history_file'] = 'revision-%04d-%s.json' % (record['revision'], uuid.uuid4().hex[:8])
        atomic_json(history / record['history_file'], record)
        atomic_json(root / 'record.json', record)
        event(root, operation, revision=record['revision'], status=record['status'])
        return record

    def create(self, payload):
        with LOCK:
            source = self.source(payload.get('source_run'))
            items = payload.get('items')
            if not isinstance(items, list) or not 1 <= len(items) <= 2:
                raise ValueError('每轮请明确选择 1～2 条建议，不会自动全部执行')
            known = {f['id']: f for f in source['findings']}
            selected, seen = [], set()
            for item in items:
                fid = item.get('finding_id') if isinstance(item, dict) else None
                if fid not in known or fid in seen:
                    raise ValueError('建议编号重复或不在已提交复核中')
                seen.add(fid)
                selected.append(dict(finding_id=fid, suggestion=known[fid]['suggestion'],
                                     planned_change=text(item.get('planned_change'), '下一条的具体做法')))
            window, tolerance = payload.get('window_hours'), payload.get('tolerance_minutes')
            if type(window) not in (int, float) or not math.isfinite(window) or not 1 <= window <= 2160:
                raise ValueError('观察窗口应为 1～2160 小时')
            if type(tolerance) is not int or not 0 <= tolerance <= min(60, window * 6):
                raise ValueError('时间容差应为 0～60 分钟，且不能超过窗口的 10%')
            if payload.get('primary_metric') not in METRICS:
                raise ValueError('请选择本轮主指标')
            if payload.get('expected_direction') not in ('increase', 'decrease'):
                raise ValueError('请选择主指标期望升高还是降低')
            parent = payload.get('parent_id') or None
            if parent:
                previous = self.load(parent)
                if previous['status'] != 'decided' or previous['target']['sample_id'] != source['sample_id']:
                    raise ValueError('下一轮必须从已总结计划的视频 B 的复核开始')
            record = dict(schema_version=1, id='cycle-' + uuid.uuid4().hex, revision=0, status='planned',
                created_at=now(), title=text(payload.get('title'), '下一条的名称或题材', limit=300),
                source=source, items=selected, window_hours=window, tolerance_minutes=tolerance,
                primary_metric=payload['primary_metric'], expected_direction=payload['expected_direction'],
                target=None, comparison=None, decisions=[], parent_id=parent)
            return self.commit(record, 'cycle_planned')

    def current(self, payload):
        r = self.load(payload.get('id'))
        if type(payload.get('revision')) is not int or payload['revision'] != r['revision']:
            raise ValueError('记录已更新，请刷新后再保存')
        if r['status'] in ('decided', 'closed'):
            raise ValueError('本轮已总结并冻结，请开启下一轮')
        return r

    def close(self, payload):
        with LOCK:
            r = self.current(payload)
            r['status'] = 'closed'
            r['closed_at'] = now()
            return self.commit(r, 'cycle_closed_by_user')

    def implement(self, payload):
        with LOCK:
            r = self.current(payload)
            sample_id = payload.get('sample_id')
            if sample_id == r['source']['sample_id']:
                raise ValueError('这里记录下一条新视频 B；重剪 A 请使用原有本视频修改记录')
            path = self.jobs.sample_path(sample_id)
            sample = read_json(path)
            executions = payload.get('executions')
            expected = {i['finding_id'] for i in r['items']}
            if not isinstance(executions, list) or len(executions) != len(expected):
                raise ValueError('请逐条说明采用情况')
            clean, seen = [], set()
            for item in executions:
                fid = item.get('finding_id') if isinstance(item, dict) else None
                if fid not in expected or fid in seen or item.get('status') not in ('applied', 'partial', 'not_applied'):
                    raise ValueError('采用情况格式无效')
                seen.add(fid)
                clean.append(dict(finding_id=fid, status=item['status'], note=text(item.get('note'), '实际做法或未采用原因'),
                                  evidence=text(item.get('evidence'), 'B 中对应的时间位置或作品版本', required=item['status'] != 'not_applied')))
            r['target'] = dict(sample_id=sample_id, goal=sample.get('tutorial_goal'), sample_sha256=digest(path),
                               executions=clean, recorded_at=now())
            r.update(comparison=None, decisions=[], status='implemented')
            return self.commit(r, 'implementation_recorded')

    def compare(self, payload):
        with LOCK:
            r = self.current(payload)
            if not r['target']:
                raise ValueError('请先关联 B 并记录实际采用情况')
            if payload.get('same_goal_confirmed') is not True or payload.get('screenshots_match') is not True:
                raise ValueError('请核对截图归属，并确认 A/B 的目标行为和指标定义一致')
            if digest(self.jobs.sample_path(r['target']['sample_id'])) != r['target']['sample_sha256']:
                raise ValueError('B 的样本配置已改变，请重新记录采用情况')
            if not any(e['status'] != 'not_applied' for e in r['target']['executions']):
                raise ValueError('B 没有采用所选建议，不能作为本轮采用后的比较')
            aid, bid = payload.get('record_a'), payload.get('record_b')
            if aid == bid:
                raise ValueError('A/B 不能使用同一张截图记录')
            a = self.metrics.for_workflow(aid, r['source']['sample_id'], True)
            b = self.metrics.for_workflow(bid, r['target']['sample_id'], True)
            result = compare(r, a, b, payload.get('published_a'), payload.get('published_b'))
            r['comparison'] = dict(result=result, a=a, b=b, published_a=payload['published_a'], published_b=payload['published_b'],
                confounders=text(payload.get('confounders'), '题材、时长、发布时间和流量来源等差异（不清楚可写未知）'),
                same_goal_confirmed=True, screenshots_match=True, created_at=now(),
                record_hashes={aid: digest(self.metrics.root(aid) / 'record.json'), bid: digest(self.metrics.root(bid) / 'record.json')})
            r['status'] = 'compared' if result['status'] == 'ready' else 'waiting_data'
            return self.commit(r, 'comparison_recorded')

    def decide(self, payload):
        with LOCK:
            r = self.current(payload)
            if not r['comparison']:
                raise ValueError('请先登记对比数据和观察窗口')
            for identifier, signature in r['comparison']['record_hashes'].items():
                self.metrics.image(identifier)
                if digest(self.metrics.root(identifier) / 'record.json') != signature:
                    raise ValueError('对比来源已变化，请重新比较')
            choices = payload.get('decisions')
            expected = {i['finding_id'] for i in r['items']}
            if not isinstance(choices, list) or len(choices) != len(expected):
                raise ValueError('请逐条给出下一轮决定')
            clean, seen = [], set()
            for choice in choices:
                fid = choice.get('finding_id') if isinstance(choice, dict) else None
                decision = choice.get('decision') if isinstance(choice, dict) else None
                if fid not in expected or fid in seen or decision not in DECISIONS:
                    raise ValueError('下一轮决定格式无效')
                if r['comparison']['result']['status'] != 'ready' and decision != 'inconclusive':
                    raise ValueError('数据未对齐时只能记为暂不能判断，补齐数据后再决定')
                seen.add(fid)
                clean.append(dict(finding_id=fid, decision=decision, note=text(choice.get('note'), '判断理由和下一条行动')))
            r.update(status='decided', decisions=clean)
            return self.commit(r, 'next_action_recorded')

    def defer(self, payload):
        with LOCK:
            r = self.current(payload)
            if not r['target']:
                raise ValueError('请先关联 B 并记录采用情况')
            reason = text(payload.get('reason'), '缺少的数据及下一次如何采集')
            r['comparison'] = dict(result=dict(status='blocked', reasons=[reason], rows=[], limitations=list(LIMITS)),
                                   confounders=reason, record_hashes={}, created_at=now())
            r['status'] = 'waiting_data'
            return self.commit(r, 'comparison_deferred')

    def report(self, identifier):
        r = self.load(identifier)
        lines = ['# 下一条视频改进计划', '', r['title'], '', '来源视频：' + r['source']['sample_id'],
                 '目标行为：' + str(r['source']['goal']),
                 '观察窗口：发布后 %s 小时，容差 %s 分钟。' % (r['window_hours'], r['tolerance_minutes']),
                 '主指标：' + METRICS[r['primary_metric']] + '；期望' + ('升高' if r['expected_direction'] == 'increase' else '降低'), '']
        for item in r['items']:
            lines += ['- ' + item['planned_change']]
        if r['target']:
            lines += ['', '下一条视频：' + r['target']['sample_id']]
            for e in r['target']['executions']:
                lines += ['- ' + e['finding_id'] + '：' + e['status'] + '；' + e['note'] + '；' + e['evidence']]
        if r['comparison']:
            lines += ['', '比较结果：', json_text(r['comparison']['result']), '', '其他差异：' + r['comparison']['confounders']]
        for d in r['decisions']:
            lines += ['', d['finding_id'] + '：' + DECISIONS[d['decision']] + '；' + d['note']]
        return '\n'.join(lines + ['', *LIMITS])


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)
