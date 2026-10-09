from copy import deepcopy
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import pytest

from thermal_pid.config import DEFAULTS, _merge, ConfigError, validate
from thermal_pid.process import public_config, Cancelled
from thermal_pid.models import Plant
from thermal_pid.workflow import plan
from thermal_pid.devices import SimulatedDevice, validate_state, DeviceError, Gateway
from thermal_pid.desktop_state import DesktopState, field_metadata, legacy_defaults
from thermal_pid.desktop_server import DesktopServer
from thermal_pid.desktop_jobs import JobManager

def pressure():
    return _merge(DEFAULTS, dict(process=dict(kind='pressure',name='压力',unit='MPa'),
        model=dict(K=.02,tau_s=12,theta_s=1,operating_value=.3,operating_output=20),
        task=dict(initial_value=.3,target_value=.5,initial_output=20),
        controller=dict(sample_time_s=.1),
        actuator=dict(max_rate_per_s=20),
        simulation=dict(duration_s=120,stop_min=0,stop_max=1),
        evaluation=dict(max_value=.52,min_value=0,max_tail_error=.003,settling_band=.005,
                        max_settling_time_s=80,min_settled_observation_s=5),
        identification=dict(probe_duration_s=120,probe_output_change=10),
        device=dict(monitor_min_value=0,monitor_max_value=1)))

def test_pressure_roundtrip_and_report_units(tmp_path):
    from thermal_pid.report import write_report
    cfg=pressure();validate(cfg)
    assert _merge(DEFAULTS, public_config(cfg)) == cfg
    result=plan(cfg,tmp_path)
    assert result['recommended']
    assert result['process']['unit']=='MPa'
    assert all(r['value']==r['temperature_c'] for r in result['recommended']['samples'])
    write_report(result,tmp_path)
    doc=(tmp_path/'report.html').read_text(encoding='utf-8')
    assert '压力 PID 对比报告' in doc and 'IAE MPa·s' in doc and '°C' not in doc
    assert 'value' in (tmp_path/'selected.csv').read_text(encoding='utf-8-sig').splitlines()[0]

def test_alias_conflict_and_heating_units():
    with pytest.raises(ConfigError,match='conflicting'):
        _merge(DEFAULTS,dict(task=dict(initial_value=2,initial_temperature_c=3)))
    cfg=pressure();cfg['model']['type']='heating';cfg['model']['source']='probe'
    with pytest.raises(ConfigError,match='Celsius'):validate(cfg)

def test_integrating_model_and_simc(tmp_path):
    cfg=pressure()
    cfg['process']=dict(kind='level',name='液位',unit='m')
    cfg['model'].update(type='integrating',K=.015,operating_output=50,theta_s=1)
    cfg['task'].update(initial_temperature_c=1,target_temperature_c=2,initial_output=50)
    cfg['algorithms']['simc_lambda_s']=5
    cfg['controller']['initialization']='tracking'
    cfg['simulation'].update(duration_s=200,temperature_stop_max_c=4)
    cfg['evaluation'].update(max_temperature_c=2.1,min_temperature_c=0,max_tail_error_c=.02,
                             settling_band_c=.02,max_settling_time_s=150)
    cfg['device']['monitor_max_temperature_c']=4
    result=plan(cfg,tmp_path)
    assert result['selected_method']=='SIMC_PI'
    assert [c['name'] for c in result['candidates']]==['SIMC_PI']
    assert result['candidates'][0]['formula']['Ti']==24
    assert result['recommended']

def test_custom_file_generic_value_manual_and_cancel(tmp_path):
    source=tmp_path/'plant.py'
    source.write_text("from dataclasses import dataclass\n@dataclass\nclass Model:\n    value:float=.3\n    def step(self,u,dt):\n        self.value+=(.3+.02*(u-20)-self.value)*dt/12\n        return self.value\ndef create_model(cfg):return Model()\n")
    cfg=pressure();cfg['model'].update(type='custom',source='manual',custom_factory=str(source)+':create_model')
    result=plan(cfg,tmp_path)
    assert result['selected_method']=='USER_PID' and len(result['arms'])==1
    with pytest.raises(Cancelled):plan(cfg,tmp_path,cancelled=lambda:True)

def test_generic_device_units_and_payload():
    cfg=pressure();cfg['mode']='use';cfg['device'].update(adapter='simulated',write_enabled=True)
    device=SimulatedDevice(cfg)
    state=device.read_state()
    assert state['value_unit']=='MPa' and 'temperature_c' not in state
    assert validate_state(cfg,state)['temperature_c']==state['value']
    state['value_unit']='bar'
    with pytest.raises(DeviceError,match='unit'):validate_state(cfg,state)
    gateway=Gateway.__new__(Gateway);gateway.cfg=cfg
    with patch.object(gateway,'request',return_value={'ok':True}) as request:
        gateway.apply({'Kp':2},.5,0)
        assert request.call_args.kwargs['setpoint']==.5
        assert 'setpoint_c' not in request.call_args.kwargs

def test_csv_mapping_pressure(tmp_path):
    from thermal_pid.models import identify
    cfg=pressure();plant=Plant(cfg)
    rows=['t,valve,pressure']
    for i in range(1201):
        u=20 if i<50 else 30
        rows.append(f'{plant.time},{u},{plant.temp}')
        plant.pwm=u;plant.update()
    path=tmp_path/'history.csv';path.write_text('\n'.join(rows))
    cfg['model']['source']='csv';cfg['history'].update(file=str(path),columns=dict(time='t',output='valve',temperature='pressure'))
    fitted,info,_=identify(cfg,tmp_path)
    assert info['model']['K']==pytest.approx(.02,rel=.03)

@pytest.fixture
def server(tmp_path):
    app=DesktopServer(tmp_path);app.start()
    yield app
    app.close()

def request(app,path,body=None,token=True,headers=None):
    hdr={'X-Session-Token':app.token} if token else {}
    hdr.update(headers or {})
    data=json.dumps(body).encode() if body is not None else None
    with urlopen(Request(app.url+path,data=data,headers=hdr),timeout=5) as response:
        return json.loads(response.read())

def test_server_secrets_csrf_export_and_files(server):
    state=server.state.snapshot()
    data=request(server,'/api/settings',dict(project=state['project'],legacy=state['legacy'],api_key='test-private-secret'))
    assert data['key_configured'] and 'test-private-secret' not in json.dumps(data)
    assert 'test-private-secret' not in json.dumps(request(server,'/api/export'))
    with pytest.raises(HTTPError) as error:request(server,'/api/settings',dict(project=DEFAULTS),token=False)
    assert error.value.code==403
    with pytest.raises(HTTPError):request(server,'/api/state',headers={'Origin':'https://evil.example'})
    with pytest.raises(HTTPError):request(server,'/api/state',headers={'Host':'evil.example'})
    with pytest.raises(HTTPError):request(server,'/result/fake/config.json')
    checks=request(server,'/api/diagnostics',{})
    assert checks and 'test-private-secret' not in json.dumps(checks)

def test_job_persistence_confirm_write_and_private_config(server):
    cfg=pressure()
    job=request(server,'/api/start',dict(project=cfg,legacy=server.state.legacy()))
    identifier=job['id'];deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        value=request(server,'/api/job/'+identifier)
        if value['status']!='running':break
        time.sleep(.02)
    assert value['status']=='completed', value
    assert value['process']['unit']=='MPa' and value['candidates']
    assert request(server,'/api/history')[0]['id']==identifier
    assert server.state.root.joinpath('jobs',identifier,'project.json').is_file()
    cfg['mode']='use';cfg['device'].update(adapter='simulated',write_enabled=True)
    with pytest.raises(HTTPError):request(server,'/api/start',dict(project=cfg))
    assert JobManager(server.state).get(identifier).view()['status']=='completed'

def test_fields_cover_all_defaults_and_legacy():
    def leaves(obj,path=''):
        out=[]
        for k,v in obj.items():
            loc=path+'.'+k if path else k
            out.extend(leaves(v,loc) if isinstance(v,dict) else [loc])
        return out
    expected=set(leaves(DEFAULTS)+leaves({'legacy':legacy_defaults()}))-{'name','schema_version'}
    assert expected=={f['path'] for f in field_metadata()}

def test_upload_preview_and_trusted_python(tmp_path):
    state=DesktopState(tmp_path)
    value=state.upload_csv('中文.csv','秒,阀位,压力\n0,20,.3\n'.encode('gb18030'))
    assert value['columns']==['秒','阀位','压力']
    with pytest.raises(ValueError):state.upload_model('model.py',b'x=1')
    assert state.upload_model('model.py',b'x=1',True)['factory'].endswith(':create_model')

def test_native_save_dialog_export(tmp_path):
    from thermal_pid.desktop import NativeDialogs
    from types import SimpleNamespace
    # The dialog is mocked; the actual export encoding and chosen path are exercised.
    import sys
    target=tmp_path/'pid.json'
    dialog=NativeDialogs();dialog._window=SimpleNamespace(create_file_dialog=lambda *a,**kw:(str(target),))
    with patch.dict(sys.modules,{'webview':SimpleNamespace(FileDialog=SimpleNamespace(SAVE=1))}):
        assert dialog.save_file('pid.json','{"Kp":2}')==str(target)
    assert json.loads(target.read_text(encoding='utf-8'))['Kp']==2

@pytest.mark.parametrize('workflow',['python','hardware'])
def test_original_gui_workflows_without_llm(tmp_path,workflow):
    from thermal_pid.desktop_jobs import Job
    from core.config import CONFIG
    state=DesktopState(tmp_path)
    legacy=state.legacy();legacy.update(MAX_TUNING_ROUNDS=1,BUFFER_SIZE=5,WARM_START_METHOD='none',SERIAL_PORT='DEMO')
    original=deepcopy(CONFIG)
    job=Job(state,dict(project=DEFAULTS,legacy=legacy,workflow=workflow))
    job.execute()
    assert job.status=='completed', (job.error,job.events)
    assert job.result['rounds_completed']==1
    assert any(e['type']=='sample' for e in job.events)
    assert CONFIG==original

def test_simulink_gui_dispatch_and_stop(tmp_path):
    from thermal_pid.desktop_jobs import Job,JobManager
    from sim.runtime import SimulationController
    state=DesktopState(tmp_path)
    legacy=state.legacy();legacy['MATLAB_MODEL_PATH']='example.slx'
    job=Job(state,dict(project=DEFAULTS,legacy=legacy,workflow='simulink'))
    with patch('simulator._run_simulink_simulation',return_value=dict(round_num=1,completed_reason='stable_rounds_reached',final_pid=dict(p=1,i=.1,d=0))) as bridge:
        job.execute()
        assert job.status=='completed'
        assert bridge.call_args.kwargs['prompt_context_overrides']['process']['kind']=='temperature'
    job.status='running';job.controller=SimulationController()
    manager=JobManager(state);manager.jobs[job.id]=job
    manager.control(job.id,'pause');assert job.controller.is_paused
    manager.control(job.id,'resume');assert not job.controller.is_paused
    manager.control(job.id,'stop');assert job.controller.should_stop

def test_complete_llm_workflow_via_local_api(tmp_path):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            trial=json.loads(body['messages'][-1]['content'])['current_trial']
            content=json.dumps(dict(**trial['current_pid'],status='TUNING',analysis_summary='保持参数并验证完整任务'))
            chunk=dict(id='chat-test',object='chat.completion.chunk',created=1,model='mock',
                choices=[dict(index=0,delta=dict(role='assistant',content=content),finish_reason=None)])
            value=('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        cfg=pressure();cfg['llm'].update(enabled=True,base_url=f'http://127.0.0.1:{server.server_port}/v1',model='mock',timeout_s=3,max_attempts=1)
        cfg['tuning']['rounds']=1
        (tmp_path/'config.json').write_text(json.dumps({'LLM_API_KEY':'test-only-key'}))
        result=plan(cfg,tmp_path)
        assert len(requests)==3
        assert all(a['history'] and a['history'][0].get('applied_pid') for a in result['arms'])
        assert 'test-only-key' not in json.dumps(result)
    finally:server.shutdown();server.server_close()
