"""Regression for final cross-route selection, not initial-method selection."""
from copy import deepcopy
import json
from unittest.mock import patch
import pytest

from thermal_pid.config import DEFAULTS
from thermal_pid.control import simulate, rank
from thermal_pid.workflow import plan


class UnavailableTuner:
    def analyze(self, *args, **kwargs): return None


def baseline(cfg, initial, tuner, *args):
    trial = simulate(cfg, {'p':16,'i':.8,'d':60})
    return trial, [], {'completed':True, 'scope':'deterministic test fixture'}


def test_real_evaluation_retains_better_legacy_and_labels_it(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    with patch('thermal_pid.legacy_route.run_legacy_route',side_effect=baseline):
        result=plan(cfg,tmp_path,UnavailableTuner())
    assert result['selection']['used_legacy_route'] is True
    assert result['selection']['selected_route']=='legacy_route'
    assert result['recommended']['pid']=={'p':16,'i':.8,'d':60}
    routes=[a for a in result['arms'] if a.get('role')=='route']
    assert rank(result['recommended'])==min(rank(a['final']) for a in routes)
    assert not result['selection']['strictly_improved_vs_legacy']
    assert '保留旧版' in result['selection']['reason']
    json.dumps(result,allow_nan=False)


def test_initially_unqualified_simc_can_win_after_refinement(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    cfg['tuning']['include_legacy_route']=False
    calls=[]
    def refine(cfg,initial,*args):
        calls.append(initial['name'])
        if initial['name']=='simc_route':return simulate(cfg,{'p':16,'i':.8,'d':60}),[]
        return initial,[]
    with patch('thermal_pid.workflow.refine',side_effect=refine):
        result=plan(cfg,tmp_path,UnavailableTuner())
    assert set(calls)=={'corrected_zn','zn_pi_route','simc_route'}
    assert not next(c for c in result['candidates'] if c['name']=='SIMC_PI')['metrics']['eligible']
    assert result['selection']['selected_route']=='simc_route'
    assert result['selection']['used_legacy_route'] is False
    assert not result['selection']['baseline_comparison_available']


def test_exact_tie_retains_legacy_not_duplicate_new_route(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    def tied(cfg,initial,tuner,*args):
        return simulate(cfg,{'p':14.4,'i':.72,'d':72}),[],{'completed':True}
    with patch('thermal_pid.legacy_route.run_legacy_route',side_effect=tied):
        result=plan(cfg,tmp_path,UnavailableTuner())
    assert result['selection']['selected_route']=='legacy_route'
    assert result['selection']['exact_tie_with_legacy']


def test_priority_changes_decision_without_changing_hard_constraints(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['tuning']['include_legacy_route']=False
    cfg['tuning']['selection_priority']='accuracy';fast=plan(cfg,tmp_path)
    cfg['tuning']['selection_priority']='smooth';smooth=plan(cfg,tmp_path)
    assert fast['selection']['selected_route']=='corrected_zn'
    assert smooth['selection']['selected_route']=='zn_pi_route'
    assert smooth['recommended']['metrics']['overshoot_pct']<fast['recommended']['metrics']['overshoot_pct']


def test_original_engine_executes_and_restores_global_config(tmp_path):
    from core.config import CONFIG
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True;cfg['tuning']['rounds']=1
    saved=deepcopy(CONFIG)
    result=plan(cfg,tmp_path,UnavailableTuner())
    legacy=next(a for a in result['arms'] if a['name']=='legacy_route')
    assert legacy['execution']['engine']['rounds_completed']==1
    assert legacy['execution']['engine']['fallback_count']==1
    assert legacy['execution']['evaluated_pid_count']>=2
    assert CONFIG==saved


def test_all_unqualified_routes_emit_no_recommendation(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['evaluation']['max_overshoot_pct']=0
    result=plan(cfg,tmp_path)
    assert result['recommended'] is None and not result['selection']['qualified']
    assert result['selection']['used_legacy_route'] is None


def test_history_keeps_legacy_provenance_after_restart(tmp_path):
    from thermal_pid.desktop_state import DesktopState
    from thermal_pid.desktop_jobs import JobManager
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    store=DesktopState(tmp_path);store.save({'project':cfg,'api_key':'offline-test-only'})
    manager=JobManager(store)
    with patch('thermal_pid.workflow.make_tuner',return_value=UnavailableTuner()), patch('thermal_pid.legacy_route.run_legacy_route',side_effect=baseline):
        view=manager.start({'project':cfg});job=manager.jobs[view['id']]
        job.worker.join(timeout=10)
    assert not job.worker.is_alive() and job.status=='completed'
    assert manager.history()[0]['selection']['used_legacy_route'] is True
    restored=JobManager(DesktopState(tmp_path))
    assert restored.history()[0]['selection']['selected_route']=='legacy_route'
    assert restored.get(job.id).view()['selection']['used_legacy_route'] is True


def test_corrected_initial_legacy_core_can_win_without_claiming_pure_new_strategy(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    def routes(cfg,initial,tuner,*args):
        if initial.get('name')=='corrected_legacy_route':
            return simulate(cfg,{'p':16,'i':.8,'d':60}),[],{'completed':True}
        return initial,[],{'completed':True}
    with patch('thermal_pid.legacy_route.run_legacy_route',side_effect=routes):
        result=plan(cfg,tmp_path,UnavailableTuner())
    choice=result['selection']
    assert choice['selected_route']=='corrected_legacy_route'
    assert choice['family']=='hybrid'
    assert choice['used_legacy_route'] is False and choice['used_legacy_tuning_core'] is True
    assert '旧版连续调优核心' in choice['reason']
    assert rank(result['recommended'])==min(rank(a['final']) for a in result['arms'] if a.get('role')=='route')


def test_hybrid_exact_tie_with_pure_new_does_not_claim_an_added_improvement(tmp_path):
    cfg=deepcopy(DEFAULTS);cfg['llm']['enabled']=True
    cfg['tuning']['include_legacy_route']=False
    def unchanged(cfg,initial,tuner,*args):return initial,[],{'completed':True}
    with patch('thermal_pid.legacy_route.run_legacy_route',side_effect=unchanged):
        result=plan(cfg,tmp_path,UnavailableTuner())
    assert result['selection']['selected_route']=='corrected_zn'
    assert result['selection']['family']=='new'
    assert result['selection']['used_legacy_tuning_core'] is False
