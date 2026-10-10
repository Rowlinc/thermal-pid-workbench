"""Models expressed in physical output units, independent of a 0..255 PWM scale."""

from copy import deepcopy
import csv
import importlib
import importlib.util
import hashlib
import math
from pathlib import Path
import random
import sys

from system_id import first_order_model, system_identify
from .config import ConfigError


def load_factory(spec):
    module, separator, name = spec.rpartition(":")
    if not separator or not module or not name:
        raise ConfigError("factory must be module:function")
    if module.lower().endswith('.py'):
        path = Path(module).resolve()
        if not path.is_file():
            raise ConfigError('custom Python model file does not exist')
        definition = importlib.util.spec_from_file_location('pid_custom_' + hashlib.sha256(str(path).encode()).hexdigest()[:16], path)
        loaded = importlib.util.module_from_spec(definition)
        sys.modules[definition.name]=loaded
        definition.loader.exec_module(loaded)
    else:
        loaded = importlib.import_module(module)
    factory = getattr(loaded, name)
    if not callable(factory):
        raise ConfigError("factory must be callable")
    return factory


class FOPDTModel:
    def __init__(self, cfg):
        m, t = cfg["model"], cfg["task"]
        self.K = m["K"] * cfg["simulation"]["gain_scale"]
        self.tau, self.theta = m["tau_s"], m["theta_s"]
        self.base_temp, self.base_output = m["operating_temperature_c"], m["operating_output"]
        self.temperature = t["initial_temperature_c"]
        self.prehistory_output = t["initial_output"]
        self.commands = []
        self.dt = None

    def step(self, output, dt):
        if self.dt is not None and dt != self.dt:
            raise ValueError("FOPDT sample time cannot change during a run")
        self.dt = dt
        self.commands.append(output)
        steps = self.theta / dt
        whole = math.floor(steps)
        fraction = steps - whole
        index = len(self.commands) - 1 - whole
        old = self.commands[index - 1] if index - 1 >= 0 else self.prehistory_output
        new = self.commands[index] if index >= 0 else self.prehistory_output
        for u, elapsed in ((old, fraction * dt), (new, (1 - fraction) * dt)):
            target = self.base_temp + self.K * (u - self.base_output)
            self.temperature = target + (self.temperature - target) * math.exp(-elapsed / self.tau)
        return self.temperature


class HeatingModel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.temperature = cfg["task"]["initial_temperature_c"]
        self.heater = cfg["task"]["initial_heater_temperature_c"]

    def step(self, output, dt):
        m, t = self.cfg["model"], self.cfg["task"]
        target = t["ambient_temperature_c"] + m["heater_gain_c_per_output"] * self.cfg[
            "simulation"
        ]["gain_scale"] * (output - m["operating_output"])
        internal = min(
            0.1, m["heater_tau_s"] / 20, 1 / (20 * (m["heat_transfer_per_s"] + m["cooling_per_s"]))
        )
        count = max(1, math.ceil(dt / internal))
        elapsed = dt / count
        for _ in range(count):
            self.heater = target + (self.heater - target) * math.exp(-elapsed / m["heater_tau_s"])
            equilibrium = (
                m["heat_transfer_per_s"] * self.heater
                + m["cooling_per_s"] * t["ambient_temperature_c"]
            ) / (m["heat_transfer_per_s"] + m["cooling_per_s"])
            self.temperature = equilibrium + (self.temperature - equilibrium) * math.exp(
                -(m["heat_transfer_per_s"] + m["cooling_per_s"]) * elapsed
            )
        return self.temperature


class IntegratingModel:
    """IPDT: dy/dt = K * (delayed u - operating u). K has PV/(output*s)."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.temperature = cfg['task']['initial_temperature_c']
        self.commands = []

    def step(self, output, dt):
        self.commands.append(output)
        delay = self.cfg['model']['theta_s'] / dt
        whole, fraction = math.floor(delay), delay % 1
        index = len(self.commands) - 1 - whole
        initial = self.cfg['task']['initial_output']
        new = self.commands[index] if index >= 0 else initial
        old = self.commands[index-1] if index-1 >= 0 else initial
        effective = fraction*old + (1-fraction)*new
        self.temperature += self.cfg['model']['K'] * self.cfg['simulation']['gain_scale'] * (effective-self.cfg['model']['operating_output']) * dt
        return self.temperature


class Plant:
    def __init__(self, cfg, nominal=False):
        self.cfg = deepcopy(cfg)
        if nominal:
            self.cfg["simulation"].update(
                {"measurement_noise_std_c": 0.0, "gain_scale": 1.0, "disturbance_time_s": None}
            )
        self.dt = self.cfg["controller"]["sample_time_s"]
        kind = self.cfg["model"]["type"]
        if kind == "fopdt":
            self.model = FOPDTModel(self.cfg)
        elif kind == 'integrating':
            self.model = IntegratingModel(self.cfg)
        elif kind == "heating":
            self.model = HeatingModel(self.cfg)
        else:
            from .process import public_config
            factory_cfg=deepcopy(self.cfg)
            canonical=public_config(self.cfg)
            for section in canonical:
                if isinstance(canonical[section],dict):
                    factory_cfg[section].update(canonical[section])
            self.model = load_factory(self.cfg["model"]["custom_factory"])(factory_cfg)
            self.value_attribute = 'value' if hasattr(self.model, 'value') else 'temperature'
            if not callable(getattr(self.model, "step", None)) or not math.isfinite(
                getattr(self.model, self.value_attribute, float('nan'))
            ):
                raise ConfigError(
                    "custom model must expose finite value (legacy: temperature) and step(output, dt_s)"
                )
        self.value_attribute = getattr(self, 'value_attribute', 'temperature')
        self.true_temp = self.temp = float(getattr(self.model, self.value_attribute))
        self.pwm = self.cfg["task"]["initial_output"]
        self.time = 0.0
        self.rng = random.Random(self.cfg["simulation"]["seed"])

    def update(self):
        self.true_temp = float(self.model.step(self.pwm, self.dt))
        self.time += self.dt
        s = self.cfg["simulation"]
        if s["disturbance_time_s"] is not None and self.time >= s["disturbance_time_s"]:
            self.true_temp += s["disturbance_rate_c_per_s"] * self.dt
            setattr(self.model, self.value_attribute, self.true_temp)
        self.temp = self.true_temp + self.rng.gauss(0, s["measurement_noise_std_c"])


def factory(cfg, nominal=False):
    return lambda: Plant(cfg, nominal)


def probe_data(cfg):
    """Log u(t) before integrating [t,t+dt), including a settled baseline."""
    probe = Plant(cfg, nominal=True)
    baseline = cfg["model"]["operating_output"]
    change = cfg["identification"]["probe_output_change"]
    step = baseline + change
    if step > cfg["actuator"]["max"]:
        step = baseline - change
    if not cfg["actuator"]["min"] <= step <= cfg["actuator"]["max"]:
        raise ConfigError("probe output change does not fit actuator range")
    times, temperatures, outputs = [], [], []
    for _ in range(round(cfg["identification"]["probe_duration_s"] / probe.dt) + 1):
        probe.pwm = baseline if probe.time < 5 * probe.dt else step
        times.append(probe.time)
        temperatures.append(probe.temp)
        outputs.append(probe.pwm)
        probe.update()
    return times, temperatures, outputs, "s"


def identify(cfg, base):
    """Return effective config, identification, and raw data for a reproducible reference."""
    cfg = deepcopy(cfg)
    m = cfg["model"]
    data = None
    if m['source'] == 'manual' or m['type'] == 'integrating':
        return cfg, dict(model=dict(K=m['K'], tau=m['tau_s'], theta=m['theta_s'],
                                   kind=m['type']), source='user model; no FOPDT fit',
                         warnings=['不进行FOPDT辨识；原Z-N对照不适用于本模型。']), None
    if m["source"] == "parameters":
        if m["type"] != "fopdt":
            raise ConfigError(
                "parameters source requires a FOPDT model; use probe for other model types"
            )
        result = {
            "model": first_order_model(m["tau_s"], m["K"], m["theta_s"]),
            "source": "user supplied model",
            "warnings": [],
        }
    elif m["source"] == "csv":
        path = (Path(base) / cfg["history"]["file"]).resolve()
        columns = cfg["history"]["columns"]
        times, temperatures, outputs = [], [], []
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not set(columns.values()) <= set(reader.fieldnames or []):
                    raise ConfigError(f"CSV must contain configured columns: {columns}")
                for index, row in enumerate(reader, start=2):
                    if index > 1000001:
                        raise ConfigError(
                            "history CSV exceeds one million samples; select a step segment"
                        )
                    try:
                        values = [
                            float(row[columns[key]]) for key in ("time", "temperature", "output")
                        ]
                    except (ValueError, TypeError, KeyError) as exc:
                        raise ConfigError(f"invalid CSV row {index}") from exc
                    if not all(math.isfinite(v) for v in values):
                        raise ConfigError(f"nonfinite CSV row {index}")
                    if not cfg["actuator"]["min"] <= values[2] <= cfg["actuator"]["max"]:
                        raise ConfigError(
                            f"CSV output on row {index} is outside actuator range; check units"
                        )
                    times.append(values[0])
                    temperatures.append(values[1])
                    outputs.append(values[2])
        except OSError as exc:
            raise ConfigError(f"cannot read history CSV: {exc}") from exc
        result = system_identify(
            times, temperatures, outputs, time_unit=cfg["history"]["time_unit"]
        )
        result["history_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        data = (times, temperatures, outputs, cfg["history"]["time_unit"])
        if "error" not in result and cfg["history"]["use_recorded_operating_point"]:
            m["operating_temperature_c"] = result["summary"]["initial_temp"]
            m["operating_output"] = outputs[0]
    else:
        probe_cfg = deepcopy(cfg)
        probe_cfg["task"]["initial_temperature_c"] = m["operating_temperature_c"]
        probe_cfg["task"]["initial_heater_temperature_c"] = m["operating_temperature_c"]
        probe_cfg["task"]["initial_output"] = m["operating_output"]
        if m["type"] == "heating":
            # The two-node model's zero-relative-input equilibrium is ambient.
            probe_cfg["task"]["initial_temperature_c"] = cfg["task"]["ambient_temperature_c"]
            probe_cfg["task"]["initial_heater_temperature_c"] = cfg["task"]["ambient_temperature_c"]
            m["operating_temperature_c"] = cfg["task"]["ambient_temperature_c"]
        times, temperatures, outputs, _ = probe_data(probe_cfg)
        result = system_identify(times, temperatures, outputs, time_unit="s")
        data = (times, temperatures, outputs, "s")
    if "error" in result:
        raise ConfigError(result["error"])
    if any("未达到稳态" in warning for warning in result.get("warnings", [])):
        raise ConfigError(
            "identification step has not reached steady state; provide longer history/probe"
        )
    quality = result.get("fit_rmse_c", 0.0) / max(
        abs(result.get("summary", {}).get("temp_rise", 1.0)), 1e-12
    )
    if quality > cfg["identification"]["max_relative_rmse"]:
        raise ConfigError(
            "FOPDT fit exceeds configured relative RMSE limit; inspect object data/model"
        )
    if m["source"] == "probe":
        # Keep the simulated physical plant unchanged after identification.
        # A custom factory may use every model field to construct its dynamics.
        if m["K"] * result["model"]["K"] <= 0:
            raise ConfigError(
                "identified direction differs from model.K; correct its sign before tuning"
            )
    else:
        m.update(
            {
                "K": result["model"]["K"],
                "tau_s": result["model"]["tau"],
                "theta_s": result["model"]["theta"],
            }
        )
    result["relative_rmse"] = quality
    if data is None and (cfg["tuning"]["compare_original"] or cfg['tuning'].get('include_legacy_route', True)):
        # Reference identifier sees the same configured virtual object, not a hidden demo.
        probe_cfg = deepcopy(cfg)
        probe_cfg["task"].update(
            {
                "initial_temperature_c": m["operating_temperature_c"],
                "initial_output": m["operating_output"],
            }
        )
        data = probe_data(probe_cfg)
    return cfg, result, data
