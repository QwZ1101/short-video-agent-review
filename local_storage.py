"""原子写入本机 JSON；短暂 Windows 文件占用只重试替换，不重复业务操作。"""
import json
from pathlib import Path
import time
import uuid


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.02 * 2**attempt)
    finally:
        if temporary.exists():
            temporary.unlink()
