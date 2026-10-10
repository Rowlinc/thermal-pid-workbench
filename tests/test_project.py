from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch
import pytest
from thermal_pid.config import DEFAULTS, ConfigError, load_project, validate
from thermal_pid.control import export_pid, import_pid, simulate, Controller
from thermal_pid.devices import DeviceError, Gateway, SimulatedDevice, deploy, validate_state
from thermal_pid.models import identify, FOPDTModel
from thermal_pid.workflow import plan
from pid_project import run, create_run_directory


def config():
    return deepcopy(DEFAULTS)


def test_defaults_omission_and_typos(tmp_path):
    path = tmp_path / "project.json"
    path.write_text('{"task":{"target_temperature_c":95}}')
    cfg, base = load_project(path)
    assert cfg["task"]["initial_temperature_c"] == 30 and cfg["task"]["target_temperature_c"] == 95
    path.write_text('{"task":{"target_temperatur_c":95}}')
    with pytest.raises(ConfigError, match="unknown"):
        load_project(path)


@pytest.mark.parametrize(
    "path,value",
    [
        ("controller.sample_time_s", 0),
        ("actuator.max", -1),
        ("simulation.duration_s", 1),
        ("model.K", 0),
        ("evaluation.tail_fraction", 2),
        ("algorithms.include", [{}]),
        ("device.write_enabled", "true"),
        ("history.time_unit", "minutes"),
    ],
)
def test_reject_invalid(path, value):
    cfg = config()
    section, key = path.split(".")
    cfg[section][key] = value
    with pytest.raises(ConfigError):
        validate(cfg)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("form", ["parallel", "ideal"])
@pytest.mark.parametrize("unit", ["s", "min"])
def test_device_conversion_roundtrip(direction, form, unit):
    cfg = config()
    cfg["model"]["K"] = direction
    cfg["controller"].update(form=form, parameter_time_unit=unit)
    pid = {"p": 2.0, "i": 0.05, "d": 0.8}
    parameters = export_pid(cfg, pid)
    assert import_pid(cfg, parameters) == pytest.approx(pid)
    assert parameters["Kp"] == 2 * direction


def test_fixed_delay_and_operating_point():
    cfg = config()
    cfg["model"].update(K=2, tau_s=10, theta_s=2.5, operating_temperature_c=30, operating_output=40)
    cfg["task"].update(initial_temperature_c=30, initial_output=40)
    plant = FOPDTModel(cfg)
    assert plant.step(50, 1) == 30 and plant.step(50, 1) == 30
    assert plant.step(50, 1) == pytest.approx(50 - 20 * math.exp(-0.5 / 10))


def test_full_task_default_and_output_slew(tmp_path):
    cfg = config()
    cfg["output"]["directory"] = "output"
    with patch("thermal_pid.devices.connect", side_effect=AssertionError("test must not connect")):
        result, directory = run(cfg, tmp_path)
    assert result["recommended"] and (directory / "report.html").exists()
    assert result["recommended"]["samples"][0]["temperature_c"] == 30
    assert abs(result["recommended"]["samples"][-1]["temperature_c"] - 100) < 0.3
    for candidate in result["candidates"]:
        outputs = [r["output"] for r in candidate["samples"]]
        assert all(0 <= u <= 100 for u in outputs)
        assert max(abs(b - a) for a, b in zip(outputs, outputs[1:])) <= 10 + 1e-9


def test_repeated_runs_preserve_previous_results(tmp_path):
    cfg = config()
    _, first = run(cfg, tmp_path)
    original = (first / "summary.json").read_bytes()
    cfg["task"]["target_temperature_c"] = 80
    _, second = run(cfg, tmp_path)
    assert first != second and first.parent == second.parent
    assert (first / "summary.json").read_bytes() == original
    assert json.loads((second / "summary.json").read_text(encoding="utf-8"))["config"]["task"]["target_temperature_c"] == 80


def test_run_directory_clock_collision(tmp_path):
    with patch("pid_project.datetime") as clock:
        clock.now.return_value.astimezone.return_value.strftime.return_value = "fixed_timestamp"
        first = create_run_directory(tmp_path)
        (first / "marker").write_text("preserved")
        second = create_run_directory(tmp_path)
    assert second.name == "fixed_timestamp_1"
    assert (first / "marker").read_text() == "preserved"


def test_custom_scale_sample_time_and_cooling(tmp_path):
    cfg = config()
    cfg["model"].update(K=-0.5, operating_temperature_c=100, theta_s=2, tau_s=20)
    cfg["task"].update(initial_temperature_c=90, target_temperature_c=60)
    cfg["controller"]["sample_time_s"] = 0.5
    cfg["actuator"].update(unit="valve_steps", max=200, max_rate_per_s=4)
    cfg["evaluation"].update(max_temperature_c=100, max_settling_time_s=500)
    result = plan(cfg, tmp_path)
    assert result["recommended"] and result["recommended"]["export_pid"]["Kp"] < 0
    assert abs(result["recommended"]["samples"][-1]["temperature_c"] - 60) < 0.3
    assert result["recommended"]["samples"][1]["time_s"] == 0.5


def test_csv_column_units_and_fit(tmp_path):
    cfg = config()
    cfg["model"].update(source="csv", operating_temperature_c=15)
    cfg["history"].update(
        file="data.csv",
        time_unit="ms",
        columns={"time": "ms", "temperature": "pv", "output": "valve"},
    )
    with (tmp_path / "data.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ms", "pv", "valve"])
        for t in range(1001):
            writer.writerow(
                [t * 1000, 25 + 20 * (1 - math.exp(-max(0, t - 15) / 100)), 0 if t < 5 else 10]
            )
    effective, result, data = identify(cfg, tmp_path)
    assert effective["model"]["K"] == pytest.approx(2, rel=0.002)
    assert effective["model"]["tau_s"] == pytest.approx(100, rel=0.01)
    assert effective["model"]["theta_s"] == pytest.approx(10, abs=0.2)
    assert effective["model"]["operating_temperature_c"] == 25


def test_fail_requirements_no_recommendation(tmp_path):
    cfg = config()
    cfg["evaluation"]["max_settling_time_s"] = 1
    result = plan(cfg, tmp_path)
    assert result["recommended"] is None
    assert all(not c["metrics"]["eligible"] for c in result["candidates"])


def test_known_model_without_original_comparison_needs_no_probe(tmp_path):
    cfg = config()
    cfg["tuning"]["compare_original"] = False
    cfg['tuning']['include_legacy_route'] = False
    cfg["actuator"].update(max=1, max_rate_per_s=0.1)
    cfg["model"]["K"] = 100
    result = plan(cfg, tmp_path)
    assert len([a for a in result['arms'] if a.get('role') == 'route']) == 3
    assert result['arms'][-1]['name'] == 'selected'


def test_llm_bad_proposal_keeps_best(tmp_path):
    class Tuner:
        def analyze(self, *args, **kwargs):
            return {"p": 30000, "i": 400, "d": 50, "done": True}

    cfg = config()
    cfg["llm"]["enabled"] = True
    cfg["tuning"]["rounds"] = 1
    result = plan(cfg, tmp_path, Tuner())
    arm = next(a for a in result["arms"] if a["name"] == result['selection']['selected_route'])
    assert arm["history"][0]["guard_notes"]
    assert arm["final"]["metrics"]["eligible"]
    assert arm["final"]["metrics"]["iae_c_s"] <= arm["initial"]["metrics"]["iae_c_s"]


def test_unavailable_llm_keeps_qualified_initial(tmp_path):
    class Tuner:
        def analyze(self, *args, **kwargs):
            return None

    cfg = config()
    cfg["llm"]["enabled"] = True
    result = plan(cfg, tmp_path, Tuner())
    assert result["recommended"]
    assert all(a["history"][0]["event"] == "llm_unavailable" for a in result["arms"] if a.get('role') == 'route' and a['family'] == 'new')


def test_final_llm_gain_checked_against_start(tmp_path):
    class Tuner:
        def analyze(self, *args, **kwargs):
            return {"p": 50, "i": 0.5, "d": 50}

    cfg = config()
    cfg["controller"]["guardrail_policy"] = "relative"
    cfg["llm"]["enabled"] = True
    cfg["tuning"]["rounds"] = 2
    result = plan(cfg, tmp_path, Tuner())
    for arm in result["arms"]:
        pid = arm["final"]["pid"]
        assert pid["p"] <= 3 and pid["i"] <= 0.04


def test_probe_recovers_true_deadtime(tmp_path):
    cfg = config()
    cfg["model"]["source"] = "probe"
    effective, identified, _ = identify(cfg, tmp_path)
    assert identified["model"]["theta"] == pytest.approx(10, abs=0.15)
    assert identified["model"]["tau"] == pytest.approx(120, rel=0.01)
    assert effective["model"] == cfg["model"]


def test_tracking_initialization_starts_from_current_output():
    cfg = config()
    cfg["controller"]["initialization"] = "tracking"
    cfg["task"]["initial_output"] = 45
    controller = Controller(cfg, {"p": 3, "i": 0, "d": 0})
    assert controller.step(30) == pytest.approx(45)


def test_configured_prompt_uses_task_not_hardcoded_limits():
    from thermal_pid.llm_tuner import ConfiguredTuner

    class Client:
        def request_json(self, system_prompt, user_prompt):
            assert "P-only" in system_prompt
            assert json.loads(user_prompt)["context"]["evaluation"]["max_overshoot_pct"] == 1
            assert json.loads(user_prompt)["context"]["actuator"]["unit"] == "kW"
            return {"p": 1, "i": 0.01, "d": 0}

    result = ConfiguredTuner(Client()).analyze(
        "{}",
        "[]",
        prompt_context={"evaluation": {"max_overshoot_pct": 1}, "actuator": {"unit": "kW"}},
    )
    assert result["p"] == 1


def use_config():
    cfg = config()
    cfg["mode"] = "use"
    cfg["device"].update(
        adapter="simulated", write_enabled=True, monitor_duration_s=3, max_planning_output_change=20
    )
    return cfg


def test_use_simulated_writes_and_reads_back(tmp_path):
    result, directory = run(use_config(), tmp_path)
    assert result["recommended"]
    events = json.loads((directory / "device_audit.json").read_text())
    assert any(e["event"] == "readback_verified" for e in events)
    assert events[-1]["event"] == "completed"


@pytest.mark.parametrize(
    "key,value",
    [
        ("timestamp_s", 0),
        ("sample_time_s", 0.1),
        ("output_unit", "PWM"),
        ("object_id", "other"),
        ("temperature_c", 999),
        ("revision", None),
        ("anti_windup", "none"),
    ],
)
def test_reject_incompatible_device_state(key, value):
    cfg = use_config()
    device = SimulatedDevice(cfg)
    state = device.read_state()
    state[key] = value
    with pytest.raises((DeviceError, ConfigError)):
        validate_state(cfg, state)


def test_no_qualified_use_never_writes(tmp_path):
    cfg = use_config()
    cfg["evaluation"]["max_settling_time_s"] = 1
    device = SimulatedDevice(cfg)
    with (
        patch("thermal_pid.devices.connect", return_value=device),
        patch.object(device, "apply", side_effect=AssertionError("must not write")),
    ):
        with pytest.raises(DeviceError, match="no qualified"):
            run(cfg, tmp_path)


def test_conflicting_revision_never_overwrites(tmp_path):
    cfg = use_config()
    device = SimulatedDevice(cfg)
    before = device.read_state()
    device.revision += 1
    result = plan(cfg, tmp_path)
    audit = []
    with patch.object(device, "apply", side_effect=AssertionError("must not overwrite")):
        with pytest.raises(DeviceError, match="configuration changed"):
            deploy(cfg, device, before, result["recommended"], audit)


def test_readback_mismatch_restores(tmp_path):
    cfg = use_config()
    device = SimulatedDevice(cfg)
    before = device.read_state()
    result = plan(cfg, tmp_path)
    audit = []
    original = device.read_state
    calls = 0

    def read():
        nonlocal calls
        calls += 1
        state = original()
        if calls == 2:
            state["setpoint_c"] = 999
        return state

    device.read_state = read
    with pytest.raises(DeviceError, match="readback mismatch"):
        deploy(cfg, device, before, result["recommended"], audit)
    assert audit[-1]["event"] == "restored" and device.pid == before["pid"]


def test_external_revision_prevents_rollback(tmp_path):
    cfg = use_config()
    device = SimulatedDevice(cfg)
    before = device.read_state()
    result = plan(cfg, tmp_path)
    audit = []
    original = device.read_state
    calls = 0

    def read():
        nonlocal calls
        calls += 1
        if calls == 3:
            device.revision += 1
        return original()

    device.read_state = read
    with pytest.raises(DeviceError):
        deploy(cfg, device, before, result["recommended"], audit)
    assert audit[-1]["event"] == "restore_failed"


def test_uncertain_ack_does_not_guess_rollback_revision(tmp_path):
    cfg = use_config()
    device = SimulatedDevice(cfg)
    before = device.read_state()
    result = plan(cfg, tmp_path)
    audit = []
    calls = []

    def apply(*args):
        calls.append(args)
        return {"ok": True}

    device.apply = apply
    with pytest.raises(DeviceError, match="uncertain"):
        deploy(cfg, device, before, result["recommended"], audit)
    assert len(calls) == 1 and audit[-1]["event"] == "fault"


def test_serial_protocol_partial_response_is_rejected():
    import io

    cfg = use_config()
    gateway = Gateway.__new__(Gateway)
    gateway.cfg = cfg

    class Serial:
        def write(self, data):
            pass

        def flush(self):
            pass

        def readline(self, size):
            return b'{"ok":true}'

    gateway.stream = Serial()
    with pytest.raises(DeviceError, match="incomplete"):
        gateway.request("read_state")


@pytest.mark.parametrize("bad_id", [False, True])
def test_tcp_protocol_request_identity(bad_id):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]

    def server():
        connection, _ = listener.accept()
        with connection, connection.makefile("rwb") as stream:
            request = json.loads(stream.readline())
            reply = {
                "protocol_version": 1,
                "request_id": "wrong" if bad_id else request["request_id"],
                "ok": True,
                "state": {"test": True},
            }
            stream.write((json.dumps(reply) + "\n").encode())
            stream.flush()
        listener.close()

    thread = threading.Thread(target=server)
    thread.start()
    cfg = use_config()
    cfg["device"].update(adapter="tcp", host="127.0.0.1", port=port)
    device = Gateway(cfg)
    try:
        if bad_id:
            with pytest.raises(DeviceError):
                device.read_state()
        else:
            assert device.read_state() == {"test": True}
    finally:
        device.close()
        thread.join(2)
