"""Dependency-ready scheduling with bounded workers and per-role capacity."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def validate_graph(tasks):
    if not isinstance(tasks, list) or not tasks or any(not isinstance(t, dict) for t in tasks):
        raise ValueError('工作流任务必须为非空对象列表')
    ids = [t.get('id') for t in tasks]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('任务 ID 无效或重复')
    for t in tasks:
        deps = t.get('depends_on')
        if not isinstance(deps, list) or any(d not in ids for d in deps) or len(set(deps)) != len(deps):
            raise ValueError('任务依赖不存在或重复')
        if type(t.get('priority', 0)) is not int:
            raise ValueError('任务优先级必须为整数')
    done = set()
    while len(done) < len(tasks):
        ready = {t['id'] for t in tasks if t['id'] not in done and set(t['depends_on']) <= done}
        if not ready:
            raise ValueError('工作流依赖存在循环')
        done.update(ready)


def schedule(tasks, actions, max_workers=2, on_event=None):
    validate_graph(tasks)
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise ValueError('工作线程数必须在 1 到 4 之间')
    if set(actions) != {t['id'] for t in tasks}:
        raise ValueError('执行工具与任务定义不匹配')
    pending = {t['id']: t for t in tasks}
    done, running, busy_roles = set(), {}, set()
    results, failure = {}, None
    emit = on_event or (lambda **event: None)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending or running:
            ready = sorted((t for t in pending.values() if set(t['depends_on']) <= done),
                           key=lambda t: (-t.get('priority', 0), t['id']))
            for task in ready:
                role = task.get('role', task['id'])
                if failure or len(running) >= max_workers or role in busy_roles:
                    continue
                emit(operation='dispatch', task=task['id'], role=role, priority=task.get('priority', 0),
                     reason='依赖已完成且角色空闲', completed_dependencies=list(task['depends_on']))
                future = executor.submit(actions[task['id']])
                running[future] = (task['id'], role)
                busy_roles.add(role)
                del pending[task['id']]
            if not running:
                if failure:
                    break
                raise ValueError('没有可执行的就绪任务')
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                identifier, role = running.pop(future)
                busy_roles.remove(role)
                try:
                    results[identifier] = future.result()
                    done.add(identifier)
                except Exception as error:
                    failure = failure or error
            # Already running tools are allowed to finish; no downstream task is dispatched after failure.
        if failure:
            emit(operation='stopped', pending=list(pending), reason='工具失败，停止分配新任务')
            raise failure
    return results
