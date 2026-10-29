"""本机启动器：动态端口、身份检查、隐藏后台进程、可重复打开。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
from urllib.parse import urlparse

from check_materials import PROJECT_ROOT
from human_review import atomic_json


def previous_port(state):
    """Reuse this workspace's old loopback origin without trusting arbitrary URLs."""
    try:
        if Path(state.get("workspace", "")).resolve() != PROJECT_ROOT.resolve():
            return 0
        parsed = urlparse(state.get("url", ""))
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and not parsed.username and not parsed.password:
            return parsed.port or 0
    except (ValueError, TypeError):
        pass
    return 0


def healthy(url):
    try:
        if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=2) as response:
            data = json.load(response)
        return data.get("app") == "tutorial-human-review" and Path(data.get("workspace", "")).resolve() == PROJECT_ROOT.resolve()
    except (ValueError, OSError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    directory = PROJECT_ROOT / "reports/review-server"
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "server.json"
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    url = previous.get("url")
    if not healthy(url):
        stamp = str(time.time_ns())
        out_path, err_path = directory / (stamp + ".stdout.log"), directory / (stamp + ".stderr.log")
        options = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        with out_path.open("w") as stdout, err_path.open("w") as stderr:
            child = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "review_server.py"),
                "--preferred-port", str(previous_port(previous))],
                cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **options)
        for _ in range(40):
            lines = out_path.read_text(encoding="utf-8").splitlines()
            if lines and healthy(lines[0]):
                url = lines[0]
                atomic_json(state_path, {"url": url, "pid": child.pid, "workspace": str(PROJECT_ROOT),
                    "stdout": str(out_path), "stderr": str(err_path)})
                break
            if child.poll() is not None:
                break
            time.sleep(.25)
        else:
            url = None
        if not healthy(url):
            raise SystemExit("Preview failed. PID=%s; HTTP check failed; logs=%s, %s" % (child.pid, out_path, err_path))
    print(url)
    if not args.no_open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
