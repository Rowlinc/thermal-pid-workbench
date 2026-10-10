"""Rejections never become clipped trials, consume valid rounds, or reach devices."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from pid_safety import check_pid_guardrails, apply_pid_guardrails
from thermal_pid.config import DEFAULTS, ConfigError, validate
from thermal_pid.control import simulate, export_pid
from thermal_pid.workflow import plan, refine
from thermal_pid.report import write_report
from thermal_pid.devices import SimulatedDevice, DeviceError, deploy


def config():
    cfg = deepcopy(DEFAULTS)
    cfg['tuning'].update(compare_original=False, include_legacy_route=False,
                         include_corrected_legacy_route=False, stable_rounds=99)
    return cfg


class Tuner:
    def __init__(self, rejected=0):
        self.rejected = rejected
        self.histories = []

    def analyze(self, current, history, **kwargs):
        self.histories.append(json.loads(history))
        if len(self.histories) <= self.rejected:
            return dict(p=10000, i=.02, d=0, status='DONE')
        return json.loads(current)['current_pid']


def test_atomic_rejection_keeps_all_current_values():
    current = dict(p=1, i=.01, d=0)
    request = dict(p=10, i=.02, d=0)
    checked = check_pid_guardrails(current, request)
    assert not checked.accepted and checked.pid is None
    retained, notes = apply_pid_guardrails(current, request)
    assert retained == current and notes
    assert request == dict(p=10, i=.02, d=0)


def test_formula_rejection_continues_other_methods(tmp_path):
    cfg = config()
    cfg['controller']['limits']['p']['max'] = 3
    result = plan(cfg, tmp_path)
    assert [c['name'] for c in result['candidates']] == ['SIMC_PI']
    assert [c['name'] for c in result['rejected_candidates']] == ['ZN_PID', 'ZN_PI']
    assert all(c['status']=='REJECTED_GUARDRAIL' and not c['simulated']
               for c in result['rejected_candidates'])
    assert result['candidates'][0]['pid'] == dict(p=2.4, i=.02, d=0)


def test_all_rejected_persists_report_without_simulation_or_api(tmp_path):
    cfg = config()
    cfg['controller']['limits']['p']['max'] = 1
    cfg['llm']['enabled'] = True
    with patch('thermal_pid.workflow.simulate') as sim, patch('thermal_pid.workflow.make_tuner') as llm:
        result = plan(cfg, tmp_path)
    sim.assert_not_called()
    llm.assert_not_called()
    assert result['recommended'] is None and result['arms'] == []
    write_report(result, tmp_path)
    assert json.loads((tmp_path/'pid.json').read_text(encoding='utf-8'))['pid'] is None
    report = (tmp_path/'report.html').read_text(encoding='utf-8')
    assert 'REJECTED_GUARDRAIL' in report and '未仿真' in report
    assert (tmp_path/'metrics.csv').exists()


def test_nonfinite_formula_rejection_can_be_saved(tmp_path):
    cfg=config();cfg['algorithms']['include']=['ZN_PID']
    with patch('thermal_pid.workflow.tuning_candidates', return_value={
        'ZN_PID':dict(Kp=float('inf'), Ki=.1, Kd=0)}):
        result=plan(cfg,tmp_path)
    assert result['rejected_candidates'][0]['status']=='REJECTED_GUARDRAIL'
    write_report(result,tmp_path)
    assert json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))['recommended'] is None


def test_rejections_do_not_spend_valid_simulation_rounds():
    cfg = config()
    initial = simulate(cfg, dict(p=14.4, i=.72, d=72))
    tuner = Tuner(rejected=2)
    with patch('thermal_pid.workflow.simulate', wraps=simulate) as sim:
        best, history = refine(cfg, initial, tuner)
    assert len(tuner.histories) == 6 and sim.call_count == 4
    assert len([h for h in history if h.get('status')=='REJECTED_GUARDRAIL']) == 2
    assert tuner.histories[1][0]['applied_pid'] is None
    assert tuner.histories[1][0]['guard_notes']
    assert best['pid'] == initial['pid']


def test_rejected_done_exhausts_retries_and_keeps_best():
    cfg = config()
    initial = simulate(cfg, dict(p=14.4, i=.72, d=72))
    tuner = Tuner(rejected=100)
    with patch('thermal_pid.workflow.simulate') as sim:
        best, history = refine(cfg, initial, tuner)
    sim.assert_not_called()
    assert len(tuner.histories) == 3
    assert history[-1]['event']=='guardrail_retry_limit'
    assert history[-1]['valid_simulations']==0
    assert best is initial


def test_request_cap_can_stop_before_retry_cap():
    cfg = config()
    cfg['tuning'].update(max_llm_requests_per_route=2, max_guardrail_retries_per_round=8)
    tuner = Tuner(rejected=100)
    _, history = refine(cfg, simulate(cfg, dict(p=14.4, i=.72, d=72)), tuner)
    assert len(tuner.histories)==2
    assert history[-1]['event']=='request_budget_exhausted'


def test_failed_simulation_does_not_end_other_formulas(tmp_path):
    cfg = config()
    def run(cfg, pid, **kwargs):
        if pid['d'] > 0:
            raise ValueError('model solver failed')
        return simulate(cfg, pid, **kwargs)
    with patch('thermal_pid.workflow.simulate', side_effect=run):
        result = plan(cfg, tmp_path)
    assert result['failed_candidates'][0]['status']=='FAILED_SIMULATION'
    assert result['failed_candidates'][0]['guard_status']=='ACCEPTED'
    assert len(result['candidates'])==2
    write_report(result, tmp_path)
    assert 'FAILED_SIMULATION' in (tmp_path/'report.html').read_text(encoding='utf-8')


def test_parameter_acceptance_does_not_imply_performance_pass():
    cfg = config()
    pid = dict(p=14.4, i=.72, d=72)
    assert check_pid_guardrails(cfg['controller']['initial_pid'], pid, limit_increase=False).accepted
    assert simulate(cfg, pid)['status']=='PASSED'
    cfg['evaluation']['max_settling_time_s']=1
    assert simulate(cfg, pid)['status']=='FAILED_EVALUATION'
    cfg['simulation']['temperature_stop_max_c']=40
    assert simulate(cfg, pid)['status']=='FAILED_SIMULATION'


def test_final_device_check_refuses_forged_out_of_range_recommendation():
    cfg = config()
    cfg['mode']='use'
    cfg['device'].update(adapter='simulated', write_enabled=True, max_planning_output_change=100)
    device = SimulatedDevice(cfg)
    before = device.read_state()
    request = dict(p=10, i=.02, d=0)
    recommendation = dict(pid=request, export_pid=export_pid(cfg, request), metrics=dict(eligible=True))
    audit=[]
    with patch.object(device, 'apply') as write:
        with pytest.raises(DeviceError, match='guardrails'):
            deploy(cfg, device, before, recommendation, audit)
    write.assert_not_called()
    assert any(e['event']=='guardrail_rejected' and not e['write_attempted'] for e in audit)


def test_original_core_retries_on_same_window_before_applying():
    from core.config import CONFIG
    from core.tuning_engine import run_tuning_engine
    class Environment:
        def __init__(self):
            self.pid=dict(p=1, i=.1, d=.05)
            self.collect_count=0
            self.applied=[]
        def get_current_pid(self): return dict(self.pid), None
        def get_setpoint(self): return 100
        def get_prompt_context(self): return {}
        def collect_samples(self):
            self.collect_count+=1
            return [dict(timestamp=t, setpoint=100, input=30, error=70, pwm=0, **self.pid)
                    for t in (0, 1000)]
        def apply_pid(self, primary, secondary=None):
            self.applied.append(dict(primary));self.pid=dict(primary)
        def reset_buffer_state(self): pass
    class Requests:
        def __init__(self): self.contexts=[]
        def analyze(self, *args, **kwargs):
            self.contexts.append(deepcopy(kwargs['prompt_context']))
            return dict(p=10000, i=.1, d=.05, status='DONE') if len(self.contexts)==1 else dict(p=1.2, i=.1, d=.05)
    env, tuner=Environment(), Requests()
    with patch.dict(CONFIG, dict(BUFFER_SIZE=2, MAX_TUNING_ROUNDS=2)):
        result=run_tuning_engine(env, tuner, 'python_sim', emit_console=False, disable_early_exit=True)
    assert result['llm_requests']==3 and env.collect_count==2
    assert len(env.applied)==2 and all(p['p']==1.2 for p in env.applied)
    assert tuner.contexts[1]['last_guardrail_rejection']['requested_pid']['p']==10000


def test_rejections_keep_their_status_in_desktop_history(tmp_path):
    from thermal_pid.desktop_state import DesktopState
    from thermal_pid.desktop_jobs import JobManager
    cfg=config();cfg['controller']['limits']['p']['max']=1
    store=DesktopState(tmp_path)
    manager=JobManager(store)
    # Exercise the same persistence path as a user run without API or hardware.
    job=manager.start(dict(project=cfg, workflow='project'))
    import time
    deadline=time.monotonic()+10
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.01)
        job=manager.get(job['id']).view()
    assert job['status']=='completed' and job['final_pid'] is None
    assert len(job['rejected_candidates'])==3
    assert all(c['status']=='REJECTED_GUARDRAIL' for c in job['rejected_candidates'])


@pytest.mark.parametrize('key,value', [('max_guardrail_retries_per_round', -1),
    ('max_guardrail_retries_per_round', True), ('max_llm_requests_per_route', 0)])
def test_invalid_retry_budgets_rejected(key, value):
    cfg=config();cfg['tuning'][key]=value
    with pytest.raises(ConfigError):
        validate(cfg)
