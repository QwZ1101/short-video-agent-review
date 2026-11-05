"""Three-video synthetic browser acceptance fixture. No model calls."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_creation_cycles as fixtures
from human_review import atomic_json,digest
from run_workflow import new_state,run_replay,create_handoff
from create_sample import create_sample
from review_server import ReviewServer

f=fixtures.CycleTests();f.setUp();root=f.fixture.workspace
def review(sample,run):
    folder=root/('source-'+sample);folder.mkdir()
    for name in ('original-review.json','evidence-catalog.json','verification.json','revised-review.json','run.json'):
        value=json.loads((f.fixture.source/name).read_text(encoding='utf-8'))
        if name=='evidence-catalog.json':value['task']['sample_id']=sample
        elif name!='run.json':
            value['sample_id']=sample
            if name=='verification.json':value['revised_review']['sample_id']=sample
        atomic_json(folder/name,value)
    meta=json.loads((folder/'run.json').read_text(encoding='utf-8'));meta.update(review_sha256=digest(folder/'original-review.json'),evidence_sha256=digest(folder/'evidence-catalog.json'));atomic_json(folder/'run.json',meta)
    for name in ('prompt.md','model-output.txt'):(folder/name).write_text('synthetic fixture')
    dest=root/'reports/workflows'/run;dest.mkdir();state=new_state('replay',0,sample);paths=run_replay(dest,state,folder);create_handoff(dest,state,paths)
    form=f.reviews.detail(run)['feedback'];form['summary']['decision']='accept'
    for item in form['findings']:item['decision']='accept'
    f.reviews.save(run,form,True)

review('test-b','run-b')
video=root/'星云花.mp4';video.write_bytes(b'synthetic')
create_sample(str(video),'运行','观众','无',sample_id='test-c',root=root);review('test-c','run-c')
first=f.applied();payload=f.comparison_payload(first);first=f.store.compare(payload)
first=f.store.decide(dict(id=first['id'],revision=first['revision'],decisions=[dict(finding_id='f1',decision='inconclusive',note='synthetic')]))
child=f.store.create(dict(f.create,source_run='run-b',parent_id=first['id'],title='合成验收：第三条星云花'))
child=f.store.implement(dict(id=child['id'],revision=child['revision'],sample_id='test-c',executions=[dict(finding_id='f1',status='applied',note='前两秒展示成品',evidence='')]))
c=f.screenshot('test-c',likes='80')
child=f.store.compare(dict(id=child['id'],revision=child['revision'],record_a=payload['record_b'],record_b=c['id'],published_a=payload['published_b'],published_b=payload['published_b'],screenshots_match=True,same_goal_confirmed=True,confounders='synthetic acceptance'))
server=ReviewServer(workspace=root)
Path(sys.argv[1]).write_text(json.dumps(dict(url='http://127.0.0.1:%s'%server.server_port,workspace=str(root),cycle=first['id'],child=child['id'])),encoding='utf-8')
try:server.serve_forever()
finally:server.server_close();f.doCleanups()
