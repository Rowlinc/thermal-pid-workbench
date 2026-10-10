from copy import deepcopy
import math

import pytest

from thermal_pid.config import DEFAULTS, ConfigError, validate
from thermal_pid.control import guard, guard_policy
from thermal_pid.workflow import plan
from pid_safety import PIDRejected


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


def test_relative_policy_rejects_without_clipping(tmp_path):
    c = cfg()
    c['controller']['guardrail_policy'] = 'relative'
    result = plan(c, tmp_path)
    assert [x['name'] for x in result['candidates']] == ['SIMC_PI']
    assert {x['name'] for x in result['rejected_candidates']} >= {'ZN_PID', 'ZN_PI'}
    assert result['candidates'][0]['pid'] == {'p':2.4, 'i':0.02, 'd':0}
    assert result['guardrail_context']['initial_candidates'] == 'relative'


@pytest.mark.parametrize('stage', ['initial','llm','delivery'])
@pytest.mark.parametrize('policy', ['auto','relative'])
def test_use_mode_never_bypasses_device_relative_limit(stage, policy):
    c = cfg()
    c['mode'] = 'use'
    c['controller']['guardrail_policy'] = policy
    current = c['controller']['initial_pid']
    with pytest.raises(PIDRejected) as error:
        guard(c, current, {'p':14.4,'i':0.72,'d':72}, stage=stage)
    assert error.value.decision.pid is None
    assert guard_policy(c, stage) == 'relative'
    with pytest.raises(PIDRejected):
        guard(c, current, {'p':27,'i':0.64,'d':72}, stage='delivery')
    assert current == {'p':1, 'i':.01, 'd':0}


def test_llm_still_has_incremental_limits_in_offline_auto():
    c = cfg()
    with pytest.raises(PIDRejected) as error:
        guard(c, {'p':14.4,'i':0.72,'d':72}, {'p':1000,'i':100,'d':500})
    assert len(error.value.decision.notes) == 3 and guard_policy(c) == 'relative'


@pytest.mark.parametrize('candidate', [
    {'p':math.nan,'i':True,'d':'broken'}, {'p':1e9,'i':-1,'d':1e9}])
def test_absolute_stage_keeps_bounds_and_invalid_value_checks(candidate):
    c = cfg()
    with pytest.raises(PIDRejected) as error:
        guard(c, c['controller']['initial_pid'], candidate, stage='initial')
    assert error.value.decision.pid is None
    assert len(error.value.decision.notes) == 3


def test_invalid_policy_is_rejected():
    c = cfg()
    c['controller']['guardrail_policy'] = 'disabled'
    with pytest.raises(ConfigError):
        validate(c)
