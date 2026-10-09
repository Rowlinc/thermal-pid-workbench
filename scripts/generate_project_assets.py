"""Generate public defaults, editor schema, and deterministic synthetic examples."""

from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thermal_pid.config import DEFAULTS
from thermal_pid.config_comments import COMMENTS, render_commented_json


def save(path, value):
    if Path(path).parent == Path("examples") and Path(path).suffix == ".json":
        value = deepcopy(value)
        value.setdefault("llm", {}).setdefault("credentials_file", "../config.json")
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def schema(value):
    if isinstance(value, dict):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {key: schema(v) for key, v in value.items()},
        }
    if isinstance(value, list):
        return {"type": "array", "items": {"type": "string"}, "uniqueItems": True, "default": value}
    if value is None:
        return {"type": ["number", "null"], "default": None}
    kind = (
        "boolean"
        if isinstance(value, bool)
        else (
            "integer"
            if isinstance(value, int)
            else "number" if isinstance(value, float) else "string"
        )
    )
    return {"type": kind, "default": value}


def main():
    (ROOT / 'project.json').write_text(render_commented_json(DEFAULTS), encoding='utf-8')
    definition = schema(DEFAULTS)
    def describe(node, path=''):
        for key, field in node.get('properties', {}).items():
            location=f'{path}.{key}' if path else key
            field['description']=COMMENTS[location]
            describe(field, location)
    describe(definition)
    definition.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Thermal PID Workbench project",
            "description": "Omitted fields use defaults. Cross-field and physical constraints are checked by --validate.",
        }
    )
    enums = {
        "mode": ["test", "use"],
        "model.type": ["fopdt", "heating", "custom"],
        "model.source": ["parameters", "csv", "probe"],
        "history.time_unit": ["s", "ms"],
        "controller.form": ["parallel", "ideal"],
        "controller.parameter_time_unit": ["s", "min"],
        "controller.derivative_on": ["measurement", "error"],
        "controller.initialization": ["zero", "tracking"],
        "controller.guardrail_policy": ["auto", "relative"],
        "device.adapter": ["disabled", "simulated", "tcp", "serial", "custom"],
    }
    for path, values in enums.items():
        node = definition
        for key in path.split("."):
            node = node["properties"][key]
        node["enum"] = values
    definition["properties"]["llm"]["properties"]["deepseek_thinking"]["enum"] = [
        "disabled",
        "enabled",
        "provider_default",
    ]
    definition["properties"]["algorithms"]["properties"]["include"]["items"]["enum"] = [
        "ZN_PID",
        "ZN_PI",
        "SIMC_PI",
    ]
    for path in (
        "actuator.max_rate_per_s",
        "algorithms.simc_lambda_s",
        "evaluation.max_settling_time_s",
        "evaluation.max_temperature_c",
        "evaluation.min_temperature_c",
        "evaluation.max_output_variation",
        "evaluation.max_saturation_fraction",
        "simulation.disturbance_time_s",
    ):
        node = definition
        for key in path.split("."):
            node = node["properties"][key]
        node["type"] = ["number", "null"]
    save("project.schema.json", definition)
    save(
        "examples/history.json",
        {
            "name": "synthetic_history",
            "model": {"source": "csv"},
            "history": {"file": "step_history.csv", "time_unit": "s"},
            "output": {"directory": "../results/history"},
        },
    )
    save(
        "examples/heating.json",
        {
            "name": "two_node_heater",
            "model": {"type": "heating", "source": "probe"},
            "controller": {"sample_time_s": 0.2, "initial_pid": {"p": 1, "i": 0.1, "d": 0.1}},
            "identification": {"probe_duration_s": 150},
            "simulation": {"duration_s": 150},
            "evaluation": {"max_temperature_c": 103.5, "max_settling_time_s": 100},
            "output": {"directory": "../results/heating_project"},
        },
    )
    save(
        "examples/cooling.json",
        {
            "name": "cooling_90_to_60",
            "model": {"K": -0.5, "tau_s": 20, "theta_s": 2, "operating_temperature_c": 100},
            "task": {"initial_temperature_c": 90, "target_temperature_c": 60},
            "actuator": {"unit": "valve_steps", "max": 200, "max_rate_per_s": 4},
            "controller": {"sample_time_s": 0.5},
            "evaluation": {"max_temperature_c": 100},
            "output": {"directory": "../results/cooling"},
        },
    )
    save(
        "examples/use_simulated.json",
        {
            "name": "adapter_integration",
            "mode": "use",
            "device": {
                "adapter": "simulated",
                "write_enabled": True,
                "monitor_duration_s": 30,
                "max_planning_output_change": 20,
            },
            "output": {"directory": "../results/use_simulated"},
        },
    )
    save(
        "examples/use_tcp_local.json",
        {
            "name": "tcp_loopback_integration",
            "mode": "use",
            "device": {
                "adapter": "tcp",
                "host": "127.0.0.1",
                "write_enabled": True,
                "monitor_duration_s": 3,
                "max_planning_output_change": 20,
            },
            "output": {"directory": "../results/use_tcp_local"},
        },
    )
    save(
        "examples/custom_model.json",
        {
            "name": "custom_model",
            "model": {
                "type": "custom",
                "source": "probe",
                "custom_factory": "examples.custom_model:create_model",
            },
            "output": {"directory": "../results/custom_model"},
        },
    )
    with (ROOT / "examples/step_history.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "input", "pwm"])
        for t in range(1001):
            writer.writerow(
                [t, 20 + 20 * (1 - math.exp(-max(0, t - 15) / 120)), 0 if t < 5 else 20]
            )


if __name__ == "__main__":
    main()
