"""浏览器验收专用模拟样本；与用户真实任务隔离。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_human_review import HumanReviewTests
from review_server import ReviewServer

fixture = HumanReviewTests()
fixture.setUp()
if "--accepted" in sys.argv:
    fixture.store.save("run-test", fixture.approve(), True)
server = ReviewServer(workspace=fixture.workspace)
print("http://127.0.0.1:%s" % server.server_port, flush=True)
print(str(fixture.workspace), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    fixture.doCleanups()
