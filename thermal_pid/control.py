"""Configured controller, unit conversion and independent full-horizon evaluation."""

import math
from system_id import parallel_to_ideal, ideal_to_parallel
from pid_safety import apply_pid_guardrails
from .models import Plant
from .config import ConfigError
from .process import checkpoint, quantity


def guard_policy(cfg, stage="llm"):
    """Offline initialization has no physical PID change to rate-limit."""
    if stage not in ("initial", "llm", "delivery"):
        raise ValueError("unknown guard stage")
    if (cfg["mode"] == "test"
            and cfg["controller"].get("guardrail_policy", "auto") == "auto"
            and stage in ("initial", "delivery")):
        return "absolute"
    return "relative"


def guard(cfg, current, candidate, *, stage="llm"):
    values = {key: None if isinstance(value, bool) else value for key, value in candidate.items()}
    return apply_pid_guardrails(
        current,
        values,
        cfg["controller"]["limits"],
        global_max_increase_ratio=cfg["controller"]["global_max_increase_ratio"],
        limit_increase=guard_policy(cfg, stage) == "relative",
    )


def export_pid(cfg, pid):
    sign = 1 if cfg["model"]["K"] > 0 else -1
    p, i, d = (sign * pid[k] for k in ("p", "i", "d"))
    scale = 60 if cfg["controller"]["parameter_time_unit"] == "min" else 1
    if cfg["controller"]["form"] == "ideal":
        result = parallel_to_ideal(p, i, d)
        result["Ti"] = result["Ti"] / scale if result["Ti"] is not None else None
        result["Td"] /= scale
    else:
        result = {"controller_form": "parallel", "Kp": p, "Ki": i * scale, "Kd": d / scale}
    result["parameter_time_unit"] = cfg["controller"]["parameter_time_unit"]
    return result


def import_pid(cfg, parameters):
    expected = cfg["controller"]
    if not isinstance(parameters, dict):
        raise ConfigError("device PID must be an object")
    if (
        parameters.get("controller_form") != expected["form"]
        or parameters.get("parameter_time_unit") != expected["parameter_time_unit"]
    ):
        raise ConfigError("device PID form/time unit differs from configuration")
    scale = 60 if expected["parameter_time_unit"] == "min" else 1
    try:
        keys = ("Kp", "Ti", "Td") if expected["form"] == "ideal" else ("Kp", "Ki", "Kd")
        for key in keys:
            value = parameters[key]
            if key == "Ti" and value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError()
        if expected["form"] == "ideal":
            ti = parameters["Ti"]
            value = ideal_to_parallel(
                parameters["Kp"], ti * scale if ti is not None else None, parameters["Td"] * scale
            )
        else:
            value = {
                "Kp": parameters["Kp"],
                "Ki": parameters["Ki"] / scale,
                "Kd": parameters["Kd"] * scale,
            }
        sign = 1 if cfg["model"]["K"] > 0 else -1
        pid = {key: sign * value[name] for key, name in zip(("p", "i", "d"), ("Kp", "Ki", "Kd"))}
        if not all(math.isfinite(v) and v >= 0 for v in pid.values()):
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ConfigError("invalid device PID or incompatible controller direction") from exc
    return pid


class Controller:
    def __init__(self, cfg, pid):
        self.cfg, self.pid = cfg, dict(pid)
        self.sign = 1 if cfg["model"]["K"] > 0 else -1
        self.integral = 0.0
        self.previous = None
        self.derivative = 0.0
        self.output = cfg["task"]["initial_output"]
        if cfg["controller"]["initialization"] == "tracking":
            error = cfg["task"]["target_temperature_c"] - cfg["task"]["initial_temperature_c"]
            self.integral = (
                self.output - cfg["model"]["operating_output"] - self.sign * pid["p"] * error
            )

    def step(self, temperature):
        cfg = self.cfg
        dt = cfg["controller"]["sample_time_s"]
        a = cfg["actuator"]
        error = cfg["task"]["target_temperature_c"] - temperature
        signal = -temperature if cfg["controller"]["derivative_on"] == "measurement" else error
        raw = 0.0 if self.previous is None else (signal - self.previous) / dt
        alpha = dt / (dt + cfg["controller"]["derivative_filter_s"])
        self.derivative += alpha * (raw - self.derivative)
        self.previous = signal
        increment = self.sign * self.pid["i"] * error * dt
        candidate_i = self.integral + increment
        unconstrained = (
            cfg["model"]["operating_output"]
            + self.sign * (self.pid["p"] * error + self.pid["d"] * self.derivative)
            + candidate_i
        )
        low, high = a["min"], a["max"]
        if a["max_rate_per_s"] is not None:
            low = max(low, self.output - a["max_rate_per_s"] * dt)
            high = min(high, self.output + a["max_rate_per_s"] * dt)
        # Conditional integration also treats a slew limit as saturation.
        if (
            low <= unconstrained <= high
            or unconstrained > high
            and increment < 0
            or unconstrained < low
            and increment > 0
        ):
            self.integral = candidate_i
        self.output = min(high, max(low, unconstrained))
        return self.output


def metrics(cfg, rows, aborted=None):
    task, e, a = cfg["task"], cfg["evaluation"], cfg["actuator"]
    dt = cfg["controller"]["sample_time_s"]
    target = task["target_temperature_c"]
    initial = task["initial_temperature_c"]
    direction = 1 if target > initial else -1
    errors = [target - r["temperature_c"] for r in rows]
    temperatures = [r["temperature_c"] for r in rows]
    tail = errors[-max(1, math.ceil(len(rows) * e["tail_fraction"])) :]
    last_bad = max(
        (index for index, error in enumerate(errors) if abs(error) > e["settling_band_c"]),
        default=-1,
    )
    first = last_bad + 1
    settling = (
        rows[first]["time_s"]
        if first < len(rows)
        and rows[-1]["time_s"] - rows[first]["time_s"] >= e["min_settled_observation_s"]
        else None
    )
    result = {
        "overshoot_pct": max(0, max(direction * (temp - target) for temp in temperatures))
        / abs(target - initial)
        * 100,
        "iae_c_s": sum(
            (abs(errors[k]) + abs(errors[k - 1])) * 0.5 * dt for k in range(1, len(errors))
        ),
        "tail_mae_c": sum(abs(x) for x in tail) / len(tail),
        "steady_state_error_c": sum(tail) / len(tail),
        "settling_time_s": settling,
        "output_tv": sum(
            abs(rows[k]["output"] - rows[k - 1]["output"]) for k in range(1, len(rows))
        ),
        "max_temperature_c": max(temperatures),
        "min_temperature_c": min(temperatures),
        "saturation_fraction": sum(
            r["output"] <= a["min"] + 1e-9 or r["output"] >= a["max"] - 1e-9 for r in rows[1:]
        )
        / max(1, len(rows) - 1),
        "aborted": aborted,
        "duration_s": rows[-1]["time_s"],
    }
    failures = []
    for metric, limit in (
        ("overshoot_pct", "max_overshoot_pct"),
        ("tail_mae_c", "max_tail_error_c"),
        ("max_temperature_c", "max_temperature_c"),
        ("output_tv", "max_output_variation"),
        ("saturation_fraction", "max_saturation_fraction"),
    ):
        if e[limit] is not None and result[metric] > e[limit]:
            failures.append(limit)
    if e["min_temperature_c"] is not None and result["min_temperature_c"] < e["min_temperature_c"]:
        failures.append("min_temperature_c")
    if settling is None:
        failures.append("unsettled")
    elif e["max_settling_time_s"] is not None and settling > e["max_settling_time_s"]:
        failures.append("max_settling_time_s")
    if aborted:
        failures.append("simulation_aborted")
    result.update(eligible=not failures, failures=failures)
    result.update(iae=result['iae_c_s'], tail_mae=result['tail_mae_c'],
                  steady_state_error=result['steady_state_error_c'],
                  max_value=result['max_temperature_c'], min_value=result['min_temperature_c'],
                  value_unit=quantity(cfg)['unit'])
    return result


def simulate(cfg, pid, cancelled=None):
    plant = Plant(cfg)
    controller = Controller(cfg, pid)
    rows = [
        {
            "time_s": 0.0,
            "temperature_c": plant.true_temp,
            "measured_temperature_c": plant.temp,
            "output": plant.pwm,
        }
    ]
    abort = None
    for _ in range(round(cfg["simulation"]["duration_s"] / plant.dt)):
        checkpoint(cancelled)
        plant.pwm = controller.step(plant.temp)
        plant.update()
        if not all(math.isfinite(x) for x in (plant.true_temp, plant.temp, plant.pwm)):
            abort = "nonfinite model response"
            break
        rows.append(
            {
                "time_s": plant.time,
                "temperature_c": plant.true_temp,
                "measured_temperature_c": plant.temp,
                "output": plant.pwm,
            }
        )
        if (
            not cfg["simulation"]["temperature_stop_min_c"]
            <= plant.true_temp
            <= cfg["simulation"]["temperature_stop_max_c"]
        ):
            abort = "temperature stop bound exceeded"
            break
    for row in rows:
        row['value'] = row['temperature_c']
        row['measured_value'] = row['measured_temperature_c']
    return {
        "pid": dict(pid),
        "export_pid": export_pid(cfg, pid),
        "metrics": metrics(cfg, rows, abort),
        "samples": rows,
    }


def rank(trial):
    m = trial["metrics"]
    return (
        not m["eligible"],
        len(m["failures"]),
        m["iae_c_s"],
        m["output_tv"],
        m["settling_time_s"] if m["settling_time_s"] is not None else math.inf,
    )
