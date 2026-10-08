"""One experiment file, strict validation, and defaults for omitted fields."""

from copy import deepcopy
import json
import math
from pathlib import Path
from .config_comments import parse_commented_json, render_commented_json


DEFAULTS = {
    "schema_version": 1,
    "name": "temperature_30_to_100",
    "mode": "test",
    "model": {
        "type": "fopdt",
        "source": "parameters",
        "K": 1.0,
        "tau_s": 120.0,
        "theta_s": 10.0,
        "operating_temperature_c": 20.0,
        "operating_output": 0.0,
        "heater_gain_c_per_output": 3.0,
        "heater_tau_s": 10.0,
        "heat_transfer_per_s": 0.5,
        "cooling_per_s": 0.05,
        "custom_factory": "",
    },
    "history": {
        "file": "",
        "time_unit": "s",
        "columns": {"time": "timestamp", "temperature": "input", "output": "pwm"},
        "use_recorded_operating_point": True,
    },
    "identification": {
        "probe_duration_s": 960.0,
        "probe_output_change": 20.0,
        "max_relative_rmse": 0.05,
    },
    "task": {
        "initial_temperature_c": 30.0,
        "target_temperature_c": 100.0,
        "ambient_temperature_c": 20.0,
        "initial_heater_temperature_c": 30.0,
        "initial_output": 0.0,
    },
    "actuator": {"unit": "%", "min": 0.0, "max": 100.0, "max_rate_per_s": 10.0},
    "controller": {
        "form": "parallel",
        "parameter_time_unit": "s",
        "sample_time_s": 1.0,
        "derivative_on": "measurement",
        "derivative_filter_s": 0.5,
        "initialization": "zero",
        "initial_pid": {"p": 1.0, "i": 0.01, "d": 0.0},
        "limits": {
            "p": {"min": 0.0, "max": 5000.0, "max_increase_ratio": 3.0},
            "i": {"min": 0.0, "max": 500.0, "max_increase_ratio": 4.0},
            "d": {"min": 0.0, "max": 500.0, "max_increase_ratio": 4.0},
        },
        "global_max_increase_ratio": 0.0,
    },
    "algorithms": {"include": ["ZN_PID", "ZN_PI", "SIMC_PI"], "simc_lambda_s": None},
    "evaluation": {
        "max_overshoot_pct": 5.0,
        "max_temperature_c": 102.0,
        "min_temperature_c": None,
        "max_tail_error_c": 0.3,
        "settling_band_c": 1.0,
        "max_settling_time_s": 600.0,
        "min_settled_observation_s": 20.0,
        "tail_fraction": 0.1,
        "max_output_variation": None,
        "max_saturation_fraction": None,
    },
    "simulation": {
        "duration_s": 600.0,
        "seed": 0,
        "measurement_noise_std_c": 0.0,
        "gain_scale": 1.0,
        "disturbance_time_s": None,
        "disturbance_rate_c_per_s": 0.0,
        "temperature_stop_min_c": -20.0,
        "temperature_stop_max_c": 150.0,
    },
    "tuning": {
        "rounds": 4,
        "samples_per_round": 60,
        "stable_rounds": 2,
        "average_error_threshold_c": 1.2,
        "compare_original": True,
    },
    "llm": {
        "enabled": False,
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "credentials_file": "config.json",
        "api_key_env": "LLM_API_KEY",
        "timeout_s": 60.0,
        "max_attempts": 2,
        "max_output_tokens": 2048,
        "json_output": True,
        "deepseek_thinking": "disabled",
    },
    "device": {
        "adapter": "disabled",
        "write_enabled": False,
        "object_id": "temperature-loop-1",
        "host": "",
        "port": 9100,
        "serial_port": "",
        "baud": 115200,
        "custom_factory": "",
        "timeout_s": 5.0,
        "max_sample_age_s": 10.0,
        "monitor_duration_s": 30.0,
        "monitor_interval_s": 1.0,
        "monitor_min_temperature_c": -20.0,
        "monitor_max_temperature_c": 120.0,
        "restore_on_fault": True,
        "max_planning_temperature_change_c": 2.0,
        "max_planning_output_change": 5.0,
    },
    "output": {"directory": "results/project", "save_csv": True},
    "_help": {
        "task": "初始温度、目标温度、环境温度和加热器初始温度分别设置。",
        "model": "K 是 °C/输出单位；tau、theta 用秒。已知模型选 parameters，历史数据选 csv，内置模型探测选 probe。",
        "actuator": "填写实际输出单位、上下限和每秒最大变化量。null 表示不限制变化速率。",
        "controller": "initial_pid 和护栏均为秒制并联式增益的非负幅值；输出方向由 K 的符号确定。form/time_unit 选择设备导出的表示方式。",
        "evaluation": "同时约束超调、最高温度、末段温差和调节时间；null 表示关闭对应可选门槛。",
        "llm": "默认不联网。启用后读取环境变量或本地 credentials_file；密钥不要写进这个可公开的项目文件。",
        "device": "test 不创建设备接口。use 需配置 adapter 并显式开启 write_enabled。simulated 只用于接口联调，不能代表现场验证。",
    },
}


class ConfigError(ValueError):
    pass


def _merge(default, provided, path=""):
    if not isinstance(provided, dict):
        raise ConfigError(f"{path or 'project'} must be an object")
    result = deepcopy(default)
    for key, value in provided.items():
        location = f"{path}.{key}" if path else key
        if key not in default:
            raise ConfigError(f"unknown configuration field: {location}")
        if isinstance(default[key], dict):
            result[key] = _merge(default[key], value, location)
        else:
            result[key] = deepcopy(value)
    return result


def validate(cfg):
    def number(path, minimum=None, positive=False, optional=False, integer=False):
        value = cfg
        for key in path.split("."):
            value = value[key]
        if optional and value is None:
            return
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ConfigError(f"{path} must be a finite number")
        if minimum is not None and value < minimum or positive and value <= 0:
            raise ConfigError(f"{path} is outside its permitted range")
        if integer and int(value) != value:
            raise ConfigError(f"{path} must be an integer")

    def enum(path, allowed):
        value = cfg
        for key in path.split("."):
            value = value[key]
        if value not in allowed:
            raise ConfigError(f"{path} must be one of {allowed}")

    if type(cfg["schema_version"]) is not int or cfg["schema_version"] != 1:
        raise ConfigError("unsupported schema_version")
    enum("mode", ("test", "use"))
    enum("model.type", ("fopdt", "heating", "custom"))
    enum("model.source", ("parameters", "csv", "probe"))
    enum("history.time_unit", ("s", "ms"))
    enum("controller.form", ("parallel", "ideal"))
    enum("controller.parameter_time_unit", ("s", "min"))
    enum("controller.derivative_on", ("measurement", "error"))
    enum("controller.initialization", ("zero", "tracking"))
    enum("llm.deepseek_thinking", ("disabled", "enabled", "provider_default"))
    enum("device.adapter", ("disabled", "simulated", "tcp", "serial", "custom"))
    for path in (
        "model.K",
        "model.operating_temperature_c",
        "model.operating_output",
        "task.initial_temperature_c",
        "task.target_temperature_c",
        "task.ambient_temperature_c",
        "task.initial_heater_temperature_c",
        "task.initial_output",
        "actuator.min",
        "actuator.max",
        "simulation.disturbance_rate_c_per_s",
        "simulation.temperature_stop_min_c",
        "simulation.temperature_stop_max_c",
        "device.monitor_min_temperature_c",
        "device.monitor_max_temperature_c",
    ):
        number(path)
    if cfg["model"]["K"] == 0:
        raise ConfigError("model.K must be nonzero")
    for path in (
        "model.tau_s",
        "model.heater_gain_c_per_output",
        "model.heater_tau_s",
        "model.heat_transfer_per_s",
        "controller.sample_time_s",
        "simulation.duration_s",
        "simulation.gain_scale",
        "identification.probe_duration_s",
        "identification.probe_output_change",
        "evaluation.settling_band_c",
        "device.timeout_s",
        "device.max_sample_age_s",
        "device.monitor_interval_s",
        "llm.timeout_s",
    ):
        number(path, positive=True)
    for path in (
        "model.theta_s",
        "model.cooling_per_s",
        "controller.derivative_filter_s",
        "controller.global_max_increase_ratio",
        "simulation.measurement_noise_std_c",
        "evaluation.max_overshoot_pct",
        "evaluation.max_tail_error_c",
        "evaluation.min_settled_observation_s",
        "identification.max_relative_rmse",
        "device.monitor_duration_s",
        "tuning.average_error_threshold_c",
        "device.max_planning_temperature_change_c",
        "device.max_planning_output_change",
    ):
        number(path, minimum=0)
    for path in (
        "actuator.max_rate_per_s",
        "algorithms.simc_lambda_s",
        "evaluation.max_settling_time_s",
    ):
        number(path, positive=True, optional=True)
    for path in ("evaluation.max_temperature_c", "evaluation.min_temperature_c"):
        number(path, optional=True)
    number("simulation.disturbance_time_s", minimum=0, optional=True)
    for path in ("evaluation.max_output_variation", "evaluation.max_saturation_fraction"):
        number(path, minimum=0, optional=True)
    if (
        cfg["evaluation"]["max_saturation_fraction"] is not None
        and cfg["evaluation"]["max_saturation_fraction"] > 1
    ):
        raise ConfigError("evaluation.max_saturation_fraction must be <=1")
    number("evaluation.tail_fraction", positive=True)
    if cfg["evaluation"]["tail_fraction"] > 1:
        raise ConfigError("evaluation.tail_fraction must be <=1")
    for path in (
        "tuning.rounds",
        "llm.max_attempts",
        "llm.max_output_tokens",
        "tuning.samples_per_round",
        "tuning.stable_rounds",
        "device.port",
        "device.baud",
    ):
        number(path, positive=True, integer=True)
    number("simulation.seed", integer=True)
    if cfg["device"]["port"] > 65535:
        raise ConfigError("device.port must be <=65535")
    for gain in ("p", "i", "d"):
        number(f"controller.initial_pid.{gain}", minimum=0)
        number(f"controller.limits.{gain}.min", minimum=0)
        number(f"controller.limits.{gain}.max", minimum=0)
        number(f"controller.limits.{gain}.max_increase_ratio", minimum=1)
        if cfg["controller"]["limits"][gain]["min"] > cfg["controller"]["limits"][gain]["max"]:
            raise ConfigError(f"controller.limits.{gain}: min exceeds max")
        if (
            not cfg["controller"]["limits"][gain]["min"]
            <= cfg["controller"]["initial_pid"][gain]
            <= cfg["controller"]["limits"][gain]["max"]
        ):
            raise ConfigError(f"controller.initial_pid.{gain} outside configured limits")
    for path in (
        "history.use_recorded_operating_point",
        "llm.enabled",
        "llm.json_output",
        "tuning.compare_original",
        "device.write_enabled",
        "device.restore_on_fault",
        "output.save_csv",
    ):
        value = cfg
        for key in path.split("."):
            value = value[key]
        if not isinstance(value, bool):
            raise ConfigError(f"{path} must be boolean")
    for section, key in (
        ("name", None),
        ("actuator", "unit"),
        ("output", "directory"),
        ("device", "object_id"),
        ("llm", "provider"),
        ("llm", "api_key_env"),
    ):
        value = cfg[section] if key is None else cfg[section][key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{section}.{key or ''} must be a nonempty string")
    for section, keys in (
        ("model", ("custom_factory",)),
        ("history", ("file",)),
        ("device", ("host", "serial_port", "custom_factory")),
        ("llm", ("base_url", "model", "credentials_file")),
    ):
        for key in keys:
            if not isinstance(cfg[section][key], str):
                raise ConfigError(f"{section}.{key} must be a string")
    if not all(isinstance(v, str) and v for v in cfg["history"]["columns"].values()):
        raise ConfigError("history.columns must name three CSV columns")
    a, t, e, s = cfg["actuator"], cfg["task"], cfg["evaluation"], cfg["simulation"]
    if a["min"] >= a["max"]:
        raise ConfigError("actuator.min must be below max")
    if (
        not a["min"] <= t["initial_output"] <= a["max"]
        or not a["min"] <= cfg["model"]["operating_output"] <= a["max"]
    ):
        raise ConfigError("initial/operating output must lie within actuator bounds")
    if t["initial_temperature_c"] == t["target_temperature_c"]:
        raise ConfigError("a nonzero temperature step is required")
    if s["temperature_stop_min_c"] >= s["temperature_stop_max_c"]:
        raise ConfigError("simulation stop bounds reversed")
    if (
        not s["temperature_stop_min_c"] <= t["initial_temperature_c"] <= s["temperature_stop_max_c"]
        or not s["temperature_stop_min_c"]
        <= t["target_temperature_c"]
        <= s["temperature_stop_max_c"]
    ):
        raise ConfigError("initial/target temperature outside simulation stop bounds")
    if e["max_temperature_c"] is not None and e["max_temperature_c"] < t["target_temperature_c"]:
        raise ConfigError("evaluation maximum temperature is below target")
    if e["min_temperature_c"] is not None and e["min_temperature_c"] > t["target_temperature_c"]:
        raise ConfigError("evaluation minimum temperature is above target")
    if cfg["device"]["monitor_min_temperature_c"] >= cfg["device"]["monitor_max_temperature_c"]:
        raise ConfigError("device monitor bounds reversed")
    dt = cfg["controller"]["sample_time_s"]
    if s["duration_s"] < 2 * dt or s["duration_s"] / dt > 1000000:
        raise ConfigError("simulation duration must cover >=2 and <=1000000 samples")
    if not math.isclose(s["duration_s"] / dt, round(s["duration_s"] / dt), abs_tol=1e-8):
        raise ConfigError("simulation.duration_s must be a whole number of sample intervals")
    if e["min_settled_observation_s"] > s["duration_s"]:
        raise ConfigError("minimum settled observation exceeds simulation duration")
    if cfg["tuning"]["samples_per_round"] < 5:
        raise ConfigError("samples_per_round must be >=5")
    if (
        cfg["identification"]["probe_duration_s"] / dt < 5
        or cfg["identification"]["probe_duration_s"] / dt > 1000000
    ):
        raise ConfigError("identification probe must cover 5..1000000 samples")
    if cfg["model"]["source"] == "csv" and not cfg["history"]["file"]:
        raise ConfigError("history.file is required for CSV identification")
    if cfg["model"]["source"] == "csv" and cfg["model"]["type"] != "fopdt":
        raise ConfigError(
            "CSV identification requires model.type=fopdt; simulations then use the fitted historical model"
        )
    if cfg["model"]["type"] == "custom" and not cfg["model"]["custom_factory"]:
        raise ConfigError("model.custom_factory must be module:function")
    if cfg["model"]["source"] == "parameters" and cfg["model"]["type"] != "fopdt":
        raise ConfigError(
            "parameters source requires model.type=fopdt; choose probe for heating/custom models"
        )
    include = cfg["algorithms"]["include"]
    if (
        not isinstance(include, list)
        or not include
        or not all(isinstance(v, str) for v in include)
        or len(set(include)) != len(include)
        or not set(include) <= {"ZN_PID", "ZN_PI", "SIMC_PI"}
    ):
        raise ConfigError("algorithms.include must contain unique supported candidates")
    if cfg["mode"] == "use":
        d = cfg["device"]
        if d["adapter"] == "disabled" or not d["write_enabled"]:
            raise ConfigError(
                "use mode requires a configured device adapter and write_enabled=true"
            )
        if d["adapter"] == "tcp" and not d["host"]:
            raise ConfigError("device.host required for TCP")
        if d["adapter"] == "serial" and not d["serial_port"]:
            raise ConfigError("device.serial_port required")
        if d["adapter"] == "custom" and not d["custom_factory"]:
            raise ConfigError("device.custom_factory required")
        if (
            not d["monitor_min_temperature_c"]
            <= t["target_temperature_c"]
            <= d["monitor_max_temperature_c"]
        ):
            raise ConfigError("target temperature lies outside device monitor bounds")
    return cfg


def load_project(path=None, mode=None):
    file = Path(path or "project.json").resolve()
    if path is not None and not file.exists():
        raise ConfigError(f"project file not found: {file}")
    data = {}
    if file.exists():
        try:
            data = parse_commented_json(file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read project file: {exc}") from exc
    cfg = _merge(DEFAULTS, data)
    if mode is not None:
        cfg["mode"] = mode
    validate(cfg)
    return cfg, file.parent


def write_defaults(path):
    path = Path(path)
    if path.exists():
        raise ConfigError(f"refusing to overwrite existing project file: {path}")
    path.write_text(render_commented_json(DEFAULTS), encoding="utf-8")
