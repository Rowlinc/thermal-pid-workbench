"""Pure local thermal environment implementing the original tuning engine interface."""
import math

from core.buffer import AdvancedDataBuffer
from core.env import BaseTuningEnvironment
from offline_compare import ParallelController
from pid_safety import apply_pid_guardrails, get_pid_limits


class ThermalTuningEnv(BaseTuningEnvironment):
    def __init__(self, plant_factory, initial_pid, setpoint, dt, samples_per_round=100,
                 controller_kind="thermal", prompt_context=None):
        self.plant=plant_factory()
        self.setpoint=setpoint
        self.dt=dt
        self.samples_per_round=samples_per_round
        self.controller_kind=controller_kind
        self.prompt_context=dict(prompt_context or {})
        self.current_pid=dict(initial_pid)
        self.controller=ParallelController({"Kp":initial_pid["p"],"Ki":initial_pid["i"],"Kd":initial_pid["d"]},dt,self.plant.temp)
        if controller_kind == "native":
            self.plant.dynamic_setpoint=False
            self.plant.set_pid(initial_pid["p"],initial_pid["i"],initial_pid["d"])
        self.ticks=0
        self.apply_audit=[]
        self.rows=[self._sample()]
        self.last_metrics={}

    def _sample(self):
        return {"timestamp":self.ticks*self.dt*1000,"time_s":self.ticks*self.dt,
                "input":self.plant.temp,"pwm":self.plant.pwm,"setpoint":self.setpoint,
                "error":self.setpoint-self.plant.temp,**self.current_pid}

    def collect_samples(self):
        samples=[]
        for _ in range(self.samples_per_round):
            if self.controller_kind == "native":
                self.plant.compute_pid()
            else:
                self.plant.pwm=self.controller.compute(self.setpoint,self.plant.temp)
            self.plant.update()
            self.ticks+=1
            row=self._sample()
            if not all(math.isfinite(row[k]) for k in ("input","pwm")):
                self.last_collect_issue="Nonfinite thermal simulation; stopped locally."
                return []
            samples.append(row)
            self.rows.append(row)
        buffer=AdvancedDataBuffer(max_size=len(samples))
        for row in samples: buffer.add(row)
        self.last_metrics=buffer.calculate_advanced_metrics()
        return samples

    def apply_pid(self, primary_pid, secondary_pid=None):
        if secondary_pid is not None:
            raise ValueError("thermal workflow has one controller")
        safe,notes=apply_pid_guardrails(self.current_pid,primary_pid,get_pid_limits("python_sim"))
        self.apply_audit.append({"time_s":self.ticks*self.dt,"requested_pid":dict(primary_pid),
                                 "applied_pid":dict(safe),"guardrail_notes":notes})
        self.current_pid=dict(safe)
        if self.controller_kind == "native":
            self.plant.set_pid(safe["p"],safe["i"],safe["d"])
        else:
            self.controller.kp,self.controller.ki,self.controller.kd=safe["p"],safe["i"],safe["d"]

    def get_current_pid(self): return dict(self.current_pid),None
    def get_setpoint(self): return self.setpoint
    def get_prompt_context(self): return dict(self.prompt_context)
    def shutdown(self): pass
    def reset_buffer_state(self): pass
