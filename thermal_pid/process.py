"""Physical quantity metadata and backwards-compatible public configuration names.

Legacy temperature keys remain internal aliases to keep existing projects usable.
No unit conversion is implicit: all PV values use process.unit throughout a run.
"""
from copy import deepcopy

ALIASES = {
    'model': {'operating_value': 'operating_temperature_c'},
    'task': {'initial_value': 'initial_temperature_c', 'target_value': 'target_temperature_c'},
    'history.columns': {'value': 'temperature'},
    'evaluation': {'max_value': 'max_temperature_c', 'min_value': 'min_temperature_c',
                   'max_tail_error': 'max_tail_error_c', 'settling_band': 'settling_band_c'},
    'simulation': {'measurement_noise_std': 'measurement_noise_std_c',
                   'disturbance_rate_per_s': 'disturbance_rate_c_per_s',
                   'stop_min': 'temperature_stop_min_c', 'stop_max': 'temperature_stop_max_c'},
    'tuning': {'average_error_threshold': 'average_error_threshold_c'},
    'device': {'monitor_min_value': 'monitor_min_temperature_c',
               'monitor_max_value': 'monitor_max_temperature_c',
               'max_planning_value_change': 'max_planning_temperature_change_c'},
}
PROCESS_DEFAULT = {'kind': 'temperature', 'name': '温度', 'unit': '℃'}
FAILURE_LABELS = {'max_overshoot_pct':'超调超过上限','max_tail_error_c':'末段偏差超过上限',
    'max_temperature_c':'被控量超过上限','min_temperature_c':'被控量低于下限',
    'unsettled':'尚未稳定','max_settling_time_s':'调节时间超过上限',
    'max_output_variation':'输出变化过多','max_saturation_fraction':'输出饱和过久',
    'simulation_aborted':'仿真触发停止条件'}

def normalize(data):
    result = deepcopy(data)
    for section, aliases in ALIASES.items():
        obj = result
        for part in section.split('.'):
            obj = obj.get(part, {}) if isinstance(obj, dict) else {}
        if not isinstance(obj, dict):
            continue
        for new, old in aliases.items():
            if new in obj:
                if old in obj and obj[old] != obj[new]:
                    raise ValueError(f'conflicting aliases: {section}.{new} / {old}')
                obj[old] = obj.pop(new)
    return result

def public_config(cfg):
    result = deepcopy(cfg)
    for section, aliases in ALIASES.items():
        obj = result
        for part in section.split('.'):
            obj = obj.get(part, {})
        for new, old in aliases.items():
            if old in obj:
                obj[new] = obj.pop(old)
    return result

def quantity(cfg):
    return cfg.get('process', PROCESS_DEFAULT)

def is_legacy_temperature(cfg):
    p = quantity(cfg)
    return p['kind'] == 'temperature' and p['unit'] in ('℃', '°C', 'C')

class Cancelled(RuntimeError):
    pass

def checkpoint(cancelled):
    if cancelled and cancelled():
        raise Cancelled('用户已停止本次运行')
