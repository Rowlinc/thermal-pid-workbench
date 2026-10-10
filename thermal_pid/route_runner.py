"""Concurrent offline routes with isolated clients and stable result ordering."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import threading

from core.config import DEFAULT_CONFIG, runtime_config
from .process import Cancelled, checkpoint


def run_routes(cfg, tuner, routes, execute, cancelled=None, progress=None):
    independent = tuner is None or callable(getattr(tuner, 'fork', None))
    workers = min(len(routes) or 1, cfg['tuning']['max_parallel_routes'])
    if not cfg['tuning']['parallel_routes'] or not independent:
        workers = 1
    metadata = dict(configured_parallel=cfg['tuning']['parallel_routes'],
        effective_max_parallel_routes=workers, route_count=len(routes),
        independent_clients=independent,
        scope='offline route planning only; selected device write remains singular',
        reason='独立客户端并行运行' if workers > 1 else
               '注入的共享调优器不支持独立客户端，依次运行' if not independent else '依次运行')
    stopped = threading.Event()
    def is_cancelled(): return stopped.is_set() or bool(cancelled and cancelled())
    def update(route, status, message, **values):
        if progress:
            progress(dict(type='route_status', name=route['name'], label=route['label'],
                status=status, message=route['label']+'：'+message, **values))
    def run(route):
        client = tuner
        own_client = False
        def emit(event):
            if progress:
                progress(dict(event, route=route['name']))
        def log(label, message):
            if not is_cancelled() and progress:
                progress(dict(type='progress', route=route['name'],
                    message=route['label']+'：'+message))
        try:
            checkpoint(is_cancelled)
            update(route, 'running', '开始离线调优')
            if tuner is not None and independent:
                client = tuner.fork(abort_check=is_cancelled, log_callback=log,
                    waiting_callback=lambda elapsed: update(route, 'waiting_llm',
                        f'等待模型回复 {elapsed:.0f} 秒', waiting_s=round(elapsed), heartbeat=True))
                own_client = True
            with runtime_config(DEFAULT_CONFIG):
                result = execute(deepcopy(route), client, is_cancelled, emit)
            checkpoint(is_cancelled)
            update(route, 'completed', '已完成仿真评价，等待统一选优')
            return result
        except Cancelled:
            update(route, 'cancelled', '已停止，不采用未完成建议')
            raise
        except Exception as exc:
            update(route, 'failed', '路线执行失败：'+type(exc).__name__)
            raise
        finally:
            if own_client and callable(getattr(client, 'close', None)):
                client.close()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pid-route')
    futures = {}
    completed = {}
    errors = []
    try:
        for route in routes:
            checkpoint(is_cancelled)
            update(route, 'queued', '等待调优工作线程')
            futures[pool.submit(run, route)] = route
        for future in as_completed(futures):
            checkpoint(is_cancelled)
            route = futures[future]
            try:
                completed[route['name']] = future.result()
            except Cancelled:
                stopped.set()
                raise
            except Exception as exc:
                errors.append(dict(name=route['name'], reason=type(exc).__name__, status='FAILED_ROUTE'))
    except BaseException:
        stopped.set()
        for future, route in futures.items():
            if future.cancel():
                update(route, 'cancelled', '排队任务已取消')
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    # Completion order must not change tie breaking or route provenance.
    return [completed[r['name']] for r in routes if r['name'] in completed], errors, metadata
