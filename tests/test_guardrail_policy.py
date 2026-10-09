from copy import deepcopy
import math

import pytest

from thermal_pid.config import DEFAULTS, ConfigError, validate
from thermal_pid.control import guard, guard_policy
from thermal_pid.workflow import plan


def cfg():
    return deepcopy(DEFAULTS)


def test_formula_candidates_preserve_real_differences_and_final_delivery(tmp_path):
    c = cfg()
    result = plan(c, tmp_path)
    candidates = {t['name']:t for t in result['candidates']}
    assert candidates['ZN_PID']['pid'] == pytest.approx({'p':14.4, 'i':0.72, 'd':72})
    assert candidates['ZN_PI']['pid'] == pytest.approx({'p':10.8, 'i':10.8/33.3, 'd':0})
    assert candidates['SIMC_PI']['pid'] == pytest.approx({'p':2.4, 'i':0.02, 'd':0})
    assert result['recommended']['pid'] == candidates[result['selected_method']]['pid']
    assert result['recommended']['pid']['p'] > 3
    # An arbitrary default reference must not change a formula-based offline test.
    c['controller']['initial_pid'] = {'p':2, 'i':0.02, 'd':1}
    again = plan(c, tmp_path)
    assert [t['pid'] for t in again['candidates']] == [t['pid'] for t in result['candidates']]


def test_relative_policy_reproduces_legacy_candidate_clipping(tmp_path):
    c = cfg()
    c['controller']['guardrail_policy'] = 'relative'
    result = plan(c, tmp_path)
    assert result['selected_method'] == 'ZN_PI'
    assert result['recommended']['pid'] == {'p':3, 'i':0.04, 'd':0}
    assert result['guardrail_context']['initial_candidates'] == 'relative'


@pytest.mark.parametrize('stage', ['initial','llm','delivery'])
@pytest.mark.parametrize('policy', ['auto','relative'])
def test_use_mode_never_bypasses_device_relative_limit(stage, policy):
    c = cfg()
    c['mode'] = 'use'
    c['controller']['guardrail_policy'] = policy
    current = c['controller']['initial_pid']
    applied, notes = guard(c, current, {'p':14.4,'i':0.72,'d':72}, stage=stage)
    assert applied == {'p':3,'i':0.04,'d':72}
    assert guard_policy(c, stage) == 'relative' and notes
    # Even after iterative increases, the final jump is checked against device PID.
    applied, _ = guard(c, current, {'p':27,'i':0.64,'d':72}, stage='delivery')
    assert applied['p'] == 3 and applied['i'] == 0.04


def test_llm_still_has_incremental_limits_in_offline_auto():
    c = cfg()
    applied, notes = guard(c, {'p':14.4,'i':0.72,'d':72}, {'p':1000,'i':100,'d':500})
    assert applied == pytest.approx({'p':43.2,'i':2.88,'d':288})
    assert notes and guard_policy(c) == 'relative'


def test_absolute_stage_keeps_bounds_and_invalid_value_checks():
    c = cfg()
    current = c['controller']['initial_pid']
    invalid, notes = guard(c, current, {'p':math.nan,'i':True,'d':'broken'}, stage='initial')
    assert invalid == current and len(notes) == 3
    bounded, notes = guard(c, current, {'p':1e9,'i':-1,'d':1e9}, stage='initial')
    assert bounded == {'p':5000,'i':0,'d':500} and notes


def test_invalid_policy_is_rejected():
    c = cfg()
    c['controller']['guardrail_policy'] = 'disabled'
    with pytest.raises(ConfigError):
        validate(c)
