"""Concurrent route isolation, stable selection and cancellation during silent APIs."""
from copy import deepcopy
import json
import threading
import time
from unittest.mock import patch

import pytest

from core.config import CONFIG, runtime_config
from llm.cancellation import RequestCancelled, interruptible_request
from llm.client import LLMTuner
from thermal_pid.config import DEFAULTS, validate
from thermal_pid.route_runner import run_routes


def test_parallel_routes_overlap_and_isolate_config_and_input():
    cfg = deepcopy(DEFAULTS)
    cfg['tuning']['max_parallel_routes'] = 3
    original = deepcopy(CONFIG)
    barrier = threading.Barrier(3)
    routes = [dict(name=str(i), label=str(i), nested={'value':i}) for i in range(3)]
    events = []
    def execute(route, client, stop, emit):
        with runtime_config({'marker':route['name']}):
            barrier.wait(timeout=3)
            assert CONFIG['marker'] == route['name']
            route['nested']['value'] = -1
            # Reverse completion order while preserving the original result order.
            time.sleep((2-int(route['name']))*.02)
            return route['name']
    results, errors, metadata = run_routes(cfg, None, routes, execute, progress=events.append)
    assert results == ['0','1','2'] and not errors
    assert metadata['effective_max_parallel_routes'] == 3
    assert [r['nested']['value'] for r in routes] == [0,1,2]
    assert CONFIG == original
    assert sum(e['status']=='completed' for e in events) == 3


@pytest.mark.parametrize('parallel,limit,expected', [(False,5,1),(True,2,2)])
def test_route_concurrency_obeys_config(parallel, limit, expected):
    cfg=deepcopy(DEFAULTS)
    cfg['tuning'].update(parallel_routes=parallel,max_parallel_routes=limit)
    running=peak=0
    lock=threading.Lock()
    def execute(*args):
        nonlocal running,peak
        with lock:
            running+=1
            peak=max(peak,running)
        time.sleep(.04)
        with lock: running-=1
        return 'ok'
    routes=[dict(name=str(i),label=str(i)) for i in range(5)]
    results, errors, metadata=run_routes(cfg,None,routes,execute)
    assert len(results)==5 and not errors and peak==expected


def test_serial_and_parallel_keep_identical_formula_selection(tmp_path):
    from thermal_pid.workflow import plan
    cfg=deepcopy(DEFAULTS)
    parallel=plan(cfg,tmp_path)
    cfg['tuning']['parallel_routes']=False
    serial=plan(cfg,tmp_path)
    assert parallel['selection']==serial['selection']
    assert parallel['recommended']['pid']==serial['recommended']['pid']
    assert [(a['name'],a['final']['metrics']) for a in parallel['arms']] == [
        (a['name'],a['final']['metrics']) for a in serial['arms']]


def test_rejected_route_does_not_cancel_other_routes():
    cfg=deepcopy(DEFAULTS)
    routes=[dict(name=n,label=n) for n in ['bad','good']]
    def execute(route,*args):
        if route['name']=='bad': raise ValueError('fixture failure')
        return 'good'
    results, errors, _=run_routes(cfg,None,routes,execute)
    assert results==['good'] and errors[0]['status']=='FAILED_ROUTE'


def test_cancellation_does_not_wait_for_silent_request_or_blocking_cleanup():
    cancel=threading.Event()
    release=threading.Event()
    entered=threading.Event()
    errors=[]
    def operation():
        entered.set()
        release.wait(5)
        return 'late'
    def run():
        try: interruptible_request(operation,cancel.is_set,lambda:release.wait(5))
        except RequestCancelled: errors.append('cancelled')
    thread=threading.Thread(target=run)
    thread.start()
    assert entered.wait(2)
    started=time.monotonic()
    cancel.set()
    thread.join(1)
    release.set()
    assert not thread.is_alive() and errors==['cancelled']
    assert time.monotonic()-started<1


def test_cancelled_sdk_call_never_falls_back_or_retries():
    cancel=threading.Event()
    tuner=LLMTuner.__new__(LLMTuner)
    tuner.abort_check=cancel.is_set
    tuner.emit_console=False
    tuner.log_callback=None
    tuner.stream_callback=None
    tuner.use_sdk=True
    tuner.max_attempts=4
    calls=[]
    class Provider:
        def execute_request(self,**kwargs):
            calls.append('sdk')
            cancel.set()
            raise RuntimeError('connection closed')
        def close(self): pass
    tuner.llm_client=Provider()
    with patch('llm.client.HTTPFallbackProvider') as fallback:
        with pytest.raises(RequestCancelled):
            tuner._call_with_retry(tuner._execute_request,[],[],'test')
    assert calls==['sdk'] and not fallback.called


@pytest.mark.parametrize('transport',['sdk','http'])
def test_reasoning_only_stream_obeys_cancel_without_answer_text(transport):
    from types import SimpleNamespace
    from llm.providers import OpenAISDKProvider, HTTPFallbackProvider
    cancelled=threading.Event()
    consumed=[]
    answers=[]
    def stream():
        for i in range(10):
            consumed.append(i)
            cancelled.set()
            if transport=='sdk':
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content='thinking',content=None),finish_reason=None)])
            else:
                yield b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}'
    if transport=='sdk':
        provider=OpenAISDKProvider.__new__(OpenAISDKProvider)
        provider.model='test';provider.request_options={}
        provider.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:stream())))
        provider.execute_request([],[],'test',answers.append,cancelled.is_set)
    else:
        provider=HTTPFallbackProvider.__new__(HTTPFallbackProvider)
        provider.response_metadata={}
        provider._parse_stream(SimpleNamespace(iter_lines=stream),answers.append,cancelled.is_set,provider._extract_openai)
    assert consumed==[0] and not answers


def test_gui_stop_cancels_all_five_routes_and_persists_status(tmp_path):
    from thermal_pid.desktop_state import DesktopState
    from thermal_pid.desktop_jobs import JobManager
    cfg=deepcopy(DEFAULTS)
    cfg['llm']['enabled']=True
    release=threading.Event()
    all_started=threading.Event()
    lock=threading.Lock()
    calls=[]
    providers=[]
    class SilentProvider:
        response_metadata={}
        def __init__(self): providers.append(self)
        def execute_request(self,**kwargs):
            with lock:
                calls.append(self)
                if len(calls)==5: all_started.set()
            release.wait(10)
            kwargs['on_chunk']('{"p":1,"i":0.01,"d":0,"status":"DONE"}')
        def close(self): release.wait(10)
    store=DesktopState(tmp_path)
    store.save({'project':cfg,'api_key':'offline-test-placeholder'})
    manager=JobManager(store)
    with patch.object(LLMTuner,'_initialize_provider',side_effect=SilentProvider):
        view=manager.start({'project':cfg})
        job=manager.jobs[view['id']]
        try:
            assert all_started.wait(5), job.error
            # Parent and each route own separate transports.
            assert len(providers)==6 and len({id(p) for p in calls})==5
            started=time.monotonic()
            stopped=manager.control(job.id,'stop')
            assert stopped['status'] in ('stopping','cancelled')
            job.worker.join(2)
            assert not job.worker.is_alive() and job.status=='cancelled'
            assert time.monotonic()-started<2
            assert len(calls)==5  # no fallback or retries after stop
            assert job.result is None and job.output is None
            count=len(job.events)
        finally: release.set()
        time.sleep(.1)
        assert len(job.events)==count  # late replies are discarded
    saved=json.loads((job.directory/'job.json').read_text(encoding='utf-8'))
    assert saved['status']=='cancelled'
    assert all(r['status']=='cancelled' for r in saved['route_progress'].values())
    assert JobManager(DesktopState(tmp_path)).get(job.id).view()['status']=='cancelled'


@pytest.mark.parametrize('value', [0,6,True,1.5])
def test_parallel_limit_validation(value):
    cfg=deepcopy(DEFAULTS)
    cfg['tuning']['max_parallel_routes']=value
    with pytest.raises(ValueError): validate(cfg)
