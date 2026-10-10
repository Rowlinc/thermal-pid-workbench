from copy import deepcopy
import json

from thermal_pid.config import DEFAULTS
from scripts import run_comparison_suite as suite


def test_suite_records_every_arm_and_preserves_failed_metrics(tmp_path, monkeypatch):
    cfg = deepcopy(DEFAULTS)
    cfg['name'] = 'test_case'
    cfg['output']['directory'] = 'runs'
    cfg['llm']['enabled'] = True
    cfg['controller']['guardrail_policy'] = 'relative'
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(suite, 'ROOT', tmp_path)
    assert suite.main([str(path), '--llm', 'off']) == 0
    folder = next((tmp_path / 'results/comparison_suite').iterdir())
    summary = json.loads((folder / 'suite.json').read_text(encoding='utf-8'))
    assert summary['completed_scenarios'] == 1
    assert len(summary['rows']) == 6
    assert summary['selection_counts'] == {'legacy':0,'new':0,'hybrid':0,'unqualified':0,'not_comparable':1}
    text=(folder/'comparison.md').read_text(encoding='utf-8')
    assert 'legacy_route' in text and '未形成完整对照' in text
    assert '每个场景内三组' not in text
    rows = {r['arm']:r for r in summary['rows']}
    assert rows['original_zn']['eligible'] is False
    assert rows['selected']['eligible'] is True
    assert rows['selected']['legacy_status']=='llm_disabled'
    assert rows['selected']['strictly_improved_vs_legacy'] is False
    assert rows['simc_route']['family']=='new'
    assert all(not r['llm_enabled'] and r['llm_history_records'] == 0 for r in summary['rows'])
    for r in summary['rows']:
        report = (folder / r['report']).resolve()
        assert report.is_file()
        original = json.loads((report.parent / 'summary.json').read_text(encoding='utf-8'))
        arm = next(a for a in original['arms'] if a['name'] == r['arm'])
        assert r['iae_c_s'] == arm['final']['metrics']['iae_c_s']


def test_budget_limited_runs_are_not_counted_as_complete_comparisons(tmp_path, monkeypatch):
    cfg = deepcopy(DEFAULTS)
    cfg['output']['directory']='runs'
    path=tmp_path/'scenario.json'
    path.write_text(json.dumps(cfg),encoding='utf-8')
    monkeypatch.setattr(suite,'ROOT',tmp_path)
    assert suite.main([str(path),'--llm','off'])==0
    folder=next((tmp_path/'results/comparison_suite').iterdir())
    rows=json.loads((folder/'suite.json').read_text(encoding='utf-8'))['rows']
    for r in rows:
        r['legacy_status']='completed'
        r['strictly_improved_vs_legacy']=True
        r['budget_limited']=True
    suite.save_summary(folder,rows,[],1)
    saved=json.loads((folder/'suite.json').read_text(encoding='utf-8'))
    assert saved['selection_counts']['new']==0
    assert saved['selection_counts']['not_comparable']==1
    assert '未形成完整对照' in (folder/'comparison.md').read_text(encoding='utf-8')
