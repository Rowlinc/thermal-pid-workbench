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
    assert len(summary['rows']) == 3
    rows = {r['arm']:r for r in summary['rows']}
    assert rows['original_zn']['eligible'] is False
    assert rows['selected']['eligible'] is True
    assert all(not r['llm_enabled'] and r['llm_history_records'] == 0 for r in summary['rows'])
    for r in summary['rows']:
        report = (folder / r['report']).resolve()
        assert report.is_file()
        original = json.loads((report.parent / 'summary.json').read_text(encoding='utf-8'))
        arm = next(a for a in original['arms'] if a['name'] == r['arm'])
        assert r['iae_c_s'] == arm['final']['metrics']['iae_c_s']
