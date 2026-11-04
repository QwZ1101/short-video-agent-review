"""Isolated, synthetic A/B workspace for browser acceptance; never calls a model."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_creation_cycles import CycleTests
from human_review import atomic_json, digest
from run_workflow import new_state, run_replay, create_handoff
from review_server import ReviewServer

fixture = CycleTests()
fixture.setUp()
root = fixture.fixture.workspace
try:
    source = root / 'source-b'
    source.mkdir()
    for name in ('original-review.json','evidence-catalog.json','verification.json','revised-review.json','run.json'):
        value=json.loads((fixture.fixture.source/name).read_text(encoding='utf-8'))
        if name=='evidence-catalog.json': value['task']['sample_id']='test-b'
        elif name!='run.json':
            value['sample_id']='test-b'
            if name=='verification.json': value['revised_review']['sample_id']='test-b'
        atomic_json(source/name,value)
    meta=json.loads((source/'run.json').read_text(encoding='utf-8'))
    meta.update(review_sha256=digest(source/'original-review.json'),evidence_sha256=digest(source/'evidence-catalog.json'))
    atomic_json(source/'run.json',meta)
    for name in ('prompt.md','model-output.txt'): (source/name).write_text('synthetic acceptance fixture')
    b=root/'reports/workflows/run-b';b.mkdir()
    state=new_state('replay',0,'test-b')
    paths=run_replay(b,state,source);create_handoff(b,state,paths)
    # Leave B unreviewed so browser acceptance performs its human review step.
    a=fixture.screenshot('test')
    b=fixture.screenshot('test-b',likes='60')
    server=ReviewServer(workspace=root)
    details=dict(url='http://127.0.0.1:%s'%server.server_port,workspace=str(root),record_a=a['id'],record_b=b['id'])
    Path(sys.argv[1]).write_text(json.dumps(details,indent=2),encoding='utf-8')
    server.serve_forever()
finally:
    fixture.doCleanups()
