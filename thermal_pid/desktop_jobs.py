"""One active GUI job, upstream event capture, result persistence and cancellation."""

from copy import deepcopy
from datetime import datetime, timezone
import html
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .config import _merge, DEFAULTS, validate


class EventSink:
    def __init__(self, job):
        self.job = job
        self.pid = {'p':1, 'i':0.1, 'd':0.05}
        self.secondary = None
    def publish(self, kind, **values):
        if kind == 'sample':
            self.pid = {k:float(values.get(k, self.pid[k])) for k in ('p','i','d')}
            if 'p2' in values:
                self.secondary = {k:float(values[k+'2']) for k in ('p','i','d')}
        self.job.emit({'type':kind, **values})
    def snapshot_sequence(self):
        return len(self.job.events)


class ObserveOnlyTuner:
    def __init__(self, sink): self.sink = sink
    def analyze(self, *args, **kwargs):
        value = dict(self.sink.pid, status='TUNING', tuning_action='HOLD_PID',
                     analysis_summary='LLM关闭：保持当前参数，记录原回路响应。')
        if self.sink.secondary:
            value.update(controller_1=dict(self.sink.pid), controller_2=dict(self.sink.secondary))
        return value


class Job:
    def __init__(self, store, payload):
        self.store = store
        self.id = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6]
        self.directory = store.root / 'jobs' / self.id
        self.directory.mkdir()
        self.workflow = payload.get('workflow','project')
        self.project = _merge(DEFAULTS, payload['project'])
        validate(self.project)
        self.legacy = deepcopy(payload.get('legacy',store.legacy()))
        self.preferences = deepcopy(payload.get('prompt_overrides', {}))
        self.preferences.setdefault('process',deepcopy(self.project['process']))
        self.status, self.error = 'running', ''
        self.events, self.output, self.result, self.chart = [], None, None, None
        self.started = time.time()
        self.ended = None
        self.legacy_target = self.legacy.get('MATLAB_SETPOINT') if self.workflow in ('python','simulink') else None
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.controller = None
        self.key = store.api_key(self.project)
        from .process import public_config
        store._write(self.directory/'project.json', public_config(self.project))
        legacy = deepcopy(self.legacy)
        legacy.pop('LLM_API_KEY', None)
        legacy.pop('API_KEY', None)
        store._write(self.directory/'legacy.json', legacy)
        self.emit({'type':'lifecycle','phase':'starting','message':'开始运行，配置和结果会独立保存。'})

    def redact(self, value):
        text = json.dumps(value, ensure_ascii=False, default=str)
        if self.key: text = text.replace(self.key, '[已隐藏密钥]')
        return json.loads(text)

    def emit(self, event):
        with self.lock:
            self.events.append(self.redact(event))
            # Persist useful milestones so a crash leaves an inspectable record.
            if event.get('type') in ('candidate','arm'):
                self.persist()

    def persist(self):
        self.store._write(self.directory/'job.json', dict(
            id=self.id, workflow=self.workflow, status=self.status, error=self.error,
            started=self.started, ended=self.ended, output=str(self.output) if self.output else None,
            result=self.redact(self.result), events=self.events,
        ))
        project=getattr(self,'project',None)
        if project:
            self.store._write(self.directory/'record.json',dict(id=self.id,workflow=self.workflow,
                status=self.status,started=self.started,name=project['name'],process=project['process'],
                target=project['task']['target_temperature_c'],
                recommendation_status=(self.result or {}).get('recommendation_status')))

    def view(self):
        with self.lock:
            result = self.result
            project = getattr(self,'project',None) or self.store.project()
            if self.workflow == 'project':
                if result:
                    metrics = [dict(name=a['name'], initial_method=a['initial_method'],
                                    pid=a['final']['pid'], **a['final']['metrics']) for a in result['arms']]
                    series = {a['name']:a['final']['samples'][::max(1,len(a['final']['samples'])//700)]
                              for a in result['arms']}
                else:
                    snapshots={e['name']:e for e in self.events if e['type'] in ('candidate','arm')}
                    metrics=[dict(name=n,initial_method='运行中／保留快照',pid=e['pid'],**e['metrics']) for n,e in snapshots.items()]
                    series={n:e['samples'] for n,e in snapshots.items()}
            else:
                metrics = []
                samples = [e for e in self.events if e['type']=='sample']
                samples = samples[::max(1,len(samples)//1000)]
                series = {'原回路': [dict(time_s=e.get('timestamp',0)/1000,
                            temperature_c=e.get('input',0), output=e.get('pwm',0),
                            setpoint=e.get('setpoint',0)) for e in samples]}
            return dict(id=self.id, workflow=self.workflow, status=self.status,
                        error=self.error, elapsed_s=round((self.ended or time.time())-self.started,1),
                        events=self.events[-120:], metrics=metrics, series=series,
                        result_available=self.output is not None,
                        selected_method=result.get('selected_method') if result else None,
                        recommendation_status=result.get('recommendation_status') if result else None,
                        final_pid=(result.get('recommended',{}).get('pid') if result.get('recommended')
                                   else result.get('final_pid')) if result else None,
                        final_export_pid=result['recommended']['export_pid'] if result and result.get('recommended') else None,
                        device_status=result.get('device_status') if result else None,
                        event_count=len(self.events),
                        original_final_metrics=result.get('final_metrics') if result and self.workflow!='project' else None,
                        original_rounds=result.get('rounds_completed') if result and self.workflow!='project' else None,
                        candidates=[dict(name=c['name'],requested_pid=c['requested_pid'],
                                         pid=c['pid'],guard_notes=c['guard_notes'],**c['metrics'])
                                    for c in result.get('candidates',[])] if result else
                                   [dict(name=e['name'],requested_pid=e['requested_pid'],pid=e['pid'],guard_notes=e['guard_notes'],**e['metrics'])
                                    for e in self.events if e['type']=='candidate'],
                        histories=[dict(name=a['name'],rounds=a['history']) for a in result.get('arms',[])] if result else [],
                        process=result.get('process',project['process']) if result else project['process'],
                        target=(result.get('config',{}).get('task',{}).get('target_temperature_c',project['task']['target_temperature_c']) if result else project['task']['target_temperature_c']) if self.workflow=='project' else self.legacy_target,
                        experiment_name=project['name'],
                        config=__import__('thermal_pid.process',fromlist=['public_config']).public_config(result['config'] if result and self.workflow=='project' else project))

    def execute(self):
        proxy_saved={key:os.environ.get(key) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY') for key in (name,name.lower())}
        try:
            for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'):
                if self.legacy.get(name):
                    os.environ[name]=os.environ[name.lower()]=str(self.legacy[name])
            if self.project['llm']['enabled'] and not self.key:
                raise ValueError('已开启LLM，请先保存API密钥或配置环境变量。')
            if self.workflow == 'project':
                from pid_project import run
                cfg = deepcopy(self.project)
                # Relative model/history paths resolve against the persistent workspace.
                result, output = run(cfg, self.store.root, cancelled=self.cancel.is_set, progress=self.emit)
                self.result, self.output = result, output
            else:
                self._execute_upstream()
            if self.cancel.is_set(): outcome = 'cancelled'
            elif self.result and self.result.get('completed_reason') == 'error':
                raise RuntimeError('原接口运行失败，请查看日志和设备/模型设置。')
            else: outcome = 'completed'
        except BaseException as exc:
            outcome = 'cancelled' if self.cancel.is_set() else 'failed'
            # API exceptions may contain credentials; only expose known local errors.
            self.error = self.redact(str(exc) if isinstance(exc,(ValueError,FileNotFoundError)) else type(exc).__name__)
            self.emit({'type':'log','message':self.error})
        finally:
            for name,value in proxy_saved.items():
                if value is None:os.environ.pop(name,None)
                else:os.environ[name]=value
            # Publish a terminal status only after its record has been flushed.
            # Readers use the same lock, so a completed UI cannot race persistence.
            with self.lock:
                self.status=outcome
                self.ended=time.time()
                self.emit({'type':'lifecycle','phase':self.status,'message':'运行已结束。'})
                self.persist()

    def _execute_upstream(self):
        from core import config as upstream
        from core.i18n import set_language, get_language
        from sim.runtime import SimulationController
        from sim.model import HeatingSimulator
        saved_config, saved_path = deepcopy(upstream.CONFIG), upstream.CONFIG_PATH
        saved_classes = None
        saved_language = get_language()
        self.controller = SimulationController()
        sink = EventSink(self)
        runtime = deepcopy(upstream.DEFAULT_CONFIG)
        runtime.update(self.legacy)
        runtime.update(LLM_API_KEY=self.key,
                       LLM_API_BASE_URL=self.project['llm']['base_url'],
                       LLM_MODEL_NAME=self.project['llm']['model'],
                       LLM_PROVIDER=self.project['llm']['provider'],
                       LLM_REQUEST_TIMEOUT=self.project['llm']['timeout_s'])
        runtime['CSV_EXPORT_PATH'] = runtime.get('CSV_EXPORT_PATH') or str(self.directory/'samples.csv')
        if self.workflow == 'simulink' and not runtime.get('MATLAB_MODEL_PATH'):
            raise ValueError('请在原项目配置中选择Simulink模型文件。')
        try:
            upstream.CONFIG_PATH = str(self.directory/'config.json')
            self.store._write(Path(upstream.CONFIG_PATH), runtime)
            upstream.CONFIG.clear(); upstream.CONFIG.update(runtime)
            # Simulator initializes config at import time. Point it at this job
            # before importing so it cannot consume an unrelated working-directory key.
            import simulator, tuner
            saved_classes = (simulator.LLMTuner, tuner.LLMTuner)
            upstream.CONFIG.clear(); upstream.CONFIG.update(runtime)
            set_language(self.legacy.get('UI_LANGUAGE','zh'))
            if not self.project['llm']['enabled']:
                simulator.LLMTuner = tuner.LLMTuner = lambda *a,**k:ObserveOnlyTuner(sink)
            else:
                original = saved_classes[0]
                def make_tuner(*a,**kw):
                    options={'max_tokens':self.project['llm']['max_output_tokens']}
                    from urllib.parse import urlparse
                    anthropic=self.project['llm']['provider']=='anthropic' or (self.project['llm']['provider']=='auto' and urlparse(self.project['llm']['base_url']).hostname=='api.anthropic.com')
                    if self.project['llm']['json_output'] and not anthropic:
                        options['response_format']={'type':'json_object'}
                    if urlparse(self.project['llm']['base_url']).hostname=='api.deepseek.com' and self.project['llm']['deepseek_thinking']!='provider_default':
                        options['extra_body']={'thinking':{'type':self.project['llm']['deepseek_thinking']}}
                    kw.update(max_attempts=self.project['llm']['max_attempts'],request_options=options)
                    return original(*a,**kw)
                simulator.LLMTuner = tuner.LLMTuner = make_tuner
            common=dict(event_sink=sink,controller=self.controller,emit_console=False,
                        prompt_context_overrides=self.preferences or None)
            if self.workflow == 'python':
                setpoint=simulator._get_configured_setpoint()
                model=HeatingSimulator(setpoint=setpoint)
                python_common={k:v for k,v in common.items() if k!='prompt_context_overrides'}
                context=simulator.default_prompt_context_for_mode(model,'python_sim')
                context.update(self.preferences)
                self.result=simulator._run_tuning_loop(model,setpoint,'Python',llm_mode='python_sim',
                             warm_start=runtime.get('WARM_START_METHOD')!='none',doctor_checks=[],
                             prompt_context=context,**python_common)
            elif self.workflow == 'simulink':
                self.result=simulator._run_simulink_simulation(doctor_checks=[],**common)
                if self.result is None:
                    raise RuntimeError('Simulink连接或配置失败')
            elif self.workflow == 'hardware':
                port=str(runtime.get('SERIAL_PORT','')).strip()
                if not port or port=='AUTO':
                    raise ValueError('请选择实际串口，或填写DEMO运行原硬件模拟桥。')
                self.result=tuner._run_hardware_tuning_loop(port,**common)
            else:
                raise ValueError('未知工作流')
            self.output=self.directory
            self.store._write(self.directory/'legacy_result.json',self.redact(self.result))
            self.store._write(self.directory/'events.json',self.events)
            body=html.escape(json.dumps(self.redact(self.result),ensure_ascii=False,indent=2))
            (self.directory/'report.html').write_text(
                '<!doctype html><meta charset="utf-8"><title>原流程结果</title><h1>原项目兼容运行记录</h1>'
                '<p>曲线见应用结果页；原流程指标与完整记录如下。</p><pre>'+body+'</pre>',encoding='utf-8')
        finally:
            if saved_classes is not None:
                simulator.LLMTuner,tuner.LLMTuner=saved_classes
            upstream.CONFIG.clear();upstream.CONFIG.update(saved_config)
            upstream.CONFIG_PATH=saved_path
            set_language(saved_language)


class JobManager:
    def __init__(self, store):
        self.store, self.jobs, self.lock = store, {}, threading.RLock()

    def start(self, payload):
        with self.lock:
            if any(j.status=='running' or getattr(j,'worker',None) and j.worker.is_alive() for j in self.jobs.values()):
                raise ValueError('已有任务正在运行，请先结束或等待完成。')
            mode=payload.get('workflow','project')
            if mode not in ('project','python','simulink','hardware'):
                raise ValueError('不支持的工作流')
            project=_merge(DEFAULTS,payload['project']);validate(project)
            if mode == 'python' and project['process']['kind'] != 'temperature':
                raise ValueError('原 Python 仿真是专用温控模型；压力、流量等请使用通用工作台或 Simulink。')
            if payload.get('prompt_overrides') is not None and not isinstance(payload['prompt_overrides'],dict):
                raise ValueError('提示上下文须为 JSON 对象')
            if (mode=='hardware' or project['mode']=='use' and mode=='project') and payload.get('confirm_write') is not True:
                raise ValueError('请明确确认本次设备参数写入。')
            job=Job(self.store,payload)
            self.jobs[job.id]=job
            job.persist()
            job.worker=threading.Thread(target=job.execute,daemon=True)
            job.worker.start()
            return job.view()

    def get(self, identifier):
        if not isinstance(identifier,str) or not __import__('re').fullmatch(r'[\w\-]+',identifier):
            raise ValueError('运行编号无效')
        if identifier in self.jobs:return self.jobs[identifier]
        path=self.store.root/'jobs'/identifier/'job.json'
        if not path.is_file():raise FileNotFoundError('运行记录不存在')
        data=json.loads(path.read_text(encoding='utf-8'))
        job=Job.__new__(Job)
        job.store=self.store;job.id=identifier;job.directory=path.parent
        job.workflow=data['workflow'];job.status=data['status'];job.error=data['error']
        job.started=data['started'];job.output=Path(data['output']) if data['output'] else None
        job.ended=data.get('ended')
        job.result=data['result'];job.events=data['events'];job.lock=threading.RLock()
        job.key='';job.cancel=threading.Event();job.controller=None
        job.project=_merge(DEFAULTS,json.loads((path.parent/'project.json').read_text(encoding='utf-8'))) if (path.parent/'project.json').is_file() else self.store.project()
        legacy_path=path.parent/'legacy.json'
        old=json.loads(legacy_path.read_text(encoding='utf-8')) if legacy_path.is_file() else {}
        job.legacy_target=old.get('MATLAB_SETPOINT') if job.workflow in ('python','simulink') else None
        if job.status=='running':
            job.status='interrupted';job.error='上次应用退出时任务未确认完成，请查看现场状态和审计。'
            job.persist()
        self.jobs[identifier]=job
        return job

    def history(self):
        rows=[]
        for p in sorted((self.store.root/'jobs').glob('*/job.json'),reverse=True):
            try:
                record=p.with_name('record.json')
                if record.is_file():
                    rows.append(json.loads(record.read_text(encoding='utf-8')))
                    continue
                value=json.loads(p.read_text(encoding='utf-8'))
                row={k:value[k] for k in ('id','workflow','status','started')}
                cfg=json.loads((p.parent/'project.json').read_text(encoding='utf-8'))
                row.update(name=cfg['name'],process=cfg.get('process'),
                           target=cfg['task'].get('target_value',cfg['task'].get('target_temperature_c')),
                           recommendation_status=(value.get('result') or {}).get('recommendation_status'))
                rows.append(row)
            except (OSError,ValueError,KeyError):continue
        indexed={row['id']:row for row in rows}
        with self.lock:
            jobs=list(self.jobs.values())
        # A Windows rename can briefly prevent a file read. Active/cached jobs
        # remain visible and their current status takes precedence over disk.
        for job in jobs:
            with job.lock:
                cfg=job.project
                indexed[job.id]=dict(id=job.id,workflow=job.workflow,status=job.status,
                    started=job.started,name=cfg['name'],process=cfg['process'],
                    target=cfg['task']['target_temperature_c'],
                    recommendation_status=(job.result or {}).get('recommendation_status'))
        return sorted(indexed.values(),key=lambda r:r['id'],reverse=True)

    def control(self, identifier, action):
        job=self.get(identifier)
        if job.status!='running':return job.view()
        if action=='stop':
            job.cancel.set()
            if job.controller:job.controller.stop()
            job.emit({'type':'lifecycle','phase':'stopping','message':'已请求停止，当前请求结束后退出；设备模式按审计记录确认状态。'})
        elif job.controller and action in ('pause','resume'):
            getattr(job.controller,action)()
        else:raise ValueError('此工作流仅支持停止，原项目工作流支持暂停/继续。')
        return job.view()
