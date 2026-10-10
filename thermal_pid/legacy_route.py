"""Original continuous tuning engine adapted to the common configured object.

The controller/plant/evaluator are shared by all routes. This is not the
upstream built-in heating demo. No hardware adapter is created here.
"""
from copy import deepcopy
import threading

from .control import Controller, guard, simulate, rank
from .models import Plant
from .process import checkpoint, quantity

_CONFIG_LOCK = threading.RLock()


def run_legacy_route(cfg, initial, tuner, cancelled=None, progress=None):
    from core.config import CONFIG, DEFAULT_CONFIG
    from core.env import BaseTuningEnvironment
    from core.tuning_engine import run_tuning_engine

    history = []
    trials = [initial]
    route_label = '混合路线（修正初值＋旧版核心）' if initial.get('name') == 'corrected_legacy_route' else '旧版完整路线'
    priority = cfg['tuning'].get('selection_priority', 'accuracy')

    class Environment(BaseTuningEnvironment):
        def __init__(self):
            self.plant = Plant(cfg)
            self.pid = dict(initial['pid'])
            self.controller = Controller(cfg, self.pid)

        def collect_samples(self):
            checkpoint(cancelled)
            if progress:
                progress({'type': 'progress', 'message': route_label + '：连续采样并调优'})
            batch = []
            for _ in range(cfg['tuning']['samples_per_round']):
                checkpoint(cancelled)
                self.plant.pwm = self.controller.step(self.plant.temp)
                self.plant.update()
                if not cfg['simulation']['temperature_stop_min_c'] <= self.plant.true_temp <= cfg['simulation']['temperature_stop_max_c']:
                    raise ValueError('旧版路线连续仿真超过停止边界')
                batch.append(dict(timestamp=self.plant.time * 1000,
                    setpoint=self.get_setpoint(), input=self.plant.temp, pwm=self.plant.pwm,
                    error=self.get_setpoint() - self.plant.temp, **self.pid))
            return batch

        def apply_pid(self, primary_pid, secondary_pid=None):
            checkpoint(cancelled)
            self.pid, notes = guard(cfg, self.pid, primary_pid, stage='llm')
            self.controller.pid = dict(self.pid)
            # Every applied proposal, including rollback, receives a fresh full-task
            # evaluation. A final-round proposal is never trusted without a trial.
            delivery, delivery_notes = guard(cfg, cfg['controller']['initial_pid'], self.pid, stage='delivery')
            trial = simulate(cfg, delivery, cancelled=cancelled)
            trials.append(trial)
            history.append(dict(round=len(history) + 1, applied_pid=dict(self.pid),
                delivery_pid=delivery, guard_notes=notes + delivery_notes,
                metrics=trial['metrics'], reason='原版连续调优建议／回滚，经统一全任务复测'))

        def get_current_pid(self): return dict(self.pid), None
        def get_setpoint(self): return cfg['task']['target_temperature_c']
        def get_prompt_context(self):
            return dict(control_domain=cfg['process']['kind'], process=quantity(cfg),
                value_unit=cfg['process']['unit'], model=cfg['model'], task=cfg['task'],
                actuator=cfg['actuator'], controller=cfg['controller'], evaluation=cfg['evaluation'],
                instruction='Offline only. Gains are nonnegative continuous parallel magnitudes, seconds. Process direction is applied outside the gains. Legacy input/error fields use the configured process unit.')
        def shutdown(self): pass
        def reset_buffer_state(self): pass

    events = []
    class Sink:
        def publish(self, kind, **values):
            if kind != 'sample': events.append(dict(type=kind, **values))

    runtime = deepcopy(DEFAULT_CONFIG)
    runtime.update(BUFFER_SIZE=cfg['tuning']['samples_per_round'],
        MAX_TUNING_ROUNDS=cfg['tuning']['rounds'], REQUIRED_STABLE_ROUNDS=cfg['tuning']['stable_rounds'],
        MIN_ERROR_THRESHOLD=cfg['evaluation']['max_tail_error_c'],
        GOOD_ENOUGH_AVG_ERROR=cfg['tuning']['average_error_threshold_c'],
        GOOD_ENOUGH_STEADY_STATE_ERROR=cfg['evaluation']['max_tail_error_c'],
        GOOD_ENOUGH_OVERSHOOT=cfg['evaluation']['max_overshoot_pct'],
        PID_LIMITS={'default': deepcopy(cfg['controller']['limits'])},
        PID_MAX_INCREASE_RATIO=cfg['controller']['global_max_increase_ratio'],
        LLM_PROVIDER=cfg['llm']['provider'], LLM_MODEL_NAME=cfg['llm']['model'], CSV_EXPORT_PATH='')
    # ConfiguredTuner uses the new prompt. Its underlying LLMTuner.analyze retains
    # the original staged prompt, parser and transport. Injected test tuners can
    # implement the same analyze interface without an actual network connection.
    original_tuner = getattr(tuner, 'client', tuner)
    with _CONFIG_LOCK:
        saved = deepcopy(CONFIG)
        try:
            CONFIG.clear(); CONFIG.update(runtime)
            engine = run_tuning_engine(Environment(), original_tuner, 'generic',
                event_sink=Sink(), emit_console=False)
        finally:
            CONFIG.clear(); CONFIG.update(saved)
    checkpoint(cancelled)
    # The original final PID remains in the pool, alongside all previously
    # applied PIDs, and the guarded original initialization.
    delivery, notes = guard(cfg, cfg['controller']['initial_pid'], engine['final_pid'], stage='delivery')
    final = simulate(cfg, delivery, cancelled=cancelled)
    trials.append(final)
    best = min(trials, key=lambda t: rank(t, priority))
    return best, history, dict(engine=engine, events=events,
        completed=engine.get('completed_reason') != 'error',
        raw_final_pid=engine['final_pid'], final_pid_metrics=final['metrics'],
        delivery_guard_notes=notes, evaluated_pid_count=len(trials),
        scope='original continuous tuning core on common adapted plant/controller')
