"""JSON-line gateway protocol. The gateway owns the closed-loop controller."""

from copy import deepcopy
import json
import math
import socket
import time
import uuid
from .config import ConfigError
from .control import export_pid, import_pid, Controller
from .models import Plant, load_factory
from .process import quantity, is_legacy_temperature, checkpoint


class DeviceError(RuntimeError):
    pass


class Gateway:
    def __init__(self, cfg):
        self.cfg = cfg
        self.connection = None
        self.stream = None
        d = cfg["device"]
        if d["adapter"] == "tcp":
            self.connection = socket.create_connection((d["host"], d["port"]), d["timeout_s"])
            self.connection.settimeout(d["timeout_s"])
            self.stream = self.connection.makefile("rwb")
        else:
            try:
                import serial
            except ImportError as exc:
                raise ConfigError("serial adapter requires pip install .[serial]") from exc
            self.stream = serial.Serial(
                d["serial_port"], d["baud"], timeout=d["timeout_s"], write_timeout=d["timeout_s"]
            )

    def request(self, operation, **payload):
        request_id = uuid.uuid4().hex
        request = {
            "protocol_version": 1 if is_legacy_temperature(self.cfg) else 2,
            "request_id": request_id,
            "operation": operation,
            "object_id": self.cfg["device"]["object_id"],
            **payload,
        }
        try:
            self.stream.write((json.dumps(request, allow_nan=False) + "\n").encode())
            self.stream.flush()
            raw = self.stream.readline(65537)
            if not raw or len(raw) > 65536 or not raw.endswith(b"\n"):
                raise DeviceError("missing, oversized or incomplete gateway reply")
            response = json.loads(raw)
            if (
                not isinstance(response, dict)
                or response.get("request_id") != request_id
                or response.get("protocol_version") != request['protocol_version']
            ):
                raise DeviceError("gateway reply identity/protocol mismatch")
            if response.get("ok") is not True:
                raise DeviceError("gateway rejected request")
            return response
        except (OSError, ValueError, TypeError) as exc:
            raise DeviceError("gateway transport or JSON error") from exc

    def read_state(self):
        response = self.request("read_state")
        if "state" not in response:
            raise DeviceError("gateway reply missing state")
        return response["state"]

    def apply(self, pid, setpoint, expected_revision):
        if not is_legacy_temperature(self.cfg):
            return self.request('apply_parameters', pid=pid, setpoint=setpoint,
                                value_unit=quantity(self.cfg)['unit'],
                                process_kind=quantity(self.cfg)['kind'],
                                expected_revision=expected_revision,
                                initialization=self.cfg['controller']['initialization'])
        return self.request(
            "apply_parameters",
            pid=pid,
            setpoint_c=setpoint,
            expected_revision=expected_revision,
            initialization=self.cfg["controller"]["initialization"],
        )

    def close(self):
        if self.stream is not None:
            self.stream.close()
        if self.connection is not None:
            self.connection.close()


class SimulatedDevice:
    """Local adapter integration demonstration, never a physical connection."""

    def __init__(self, cfg):
        self.cfg = deepcopy(cfg)
        self.plant = Plant(cfg)
        self.controller = Controller(cfg, cfg["controller"]["initial_pid"])
        self.pid = export_pid(cfg, cfg["controller"]["initial_pid"])
        self.revision = 0

    def read_state(self):
        self.plant.pwm = self.controller.step(self.plant.temp)
        self.plant.update()
        state = {
            "object_id": self.cfg["device"]["object_id"],
            "revision": self.revision,
            "timestamp_s": time.time(),
            "pid": deepcopy(self.pid),
            "sample_time_s": self.plant.dt,
            "output_unit": self.cfg["actuator"]["unit"],
            "output_min": self.cfg["actuator"]["min"],
            "output_max": self.cfg["actuator"]["max"],
            "derivative_on": self.cfg["controller"]["derivative_on"],
            "derivative_filter_s": self.cfg["controller"]["derivative_filter_s"],
            "max_rate_per_s": self.cfg["actuator"]["max_rate_per_s"],
            "anti_windup": "conditional",
            "initialization": self.cfg["controller"]["initialization"],
            "output_bias": self.cfg["model"]["operating_output"],
            "temperature_c": self.plant.temp,
            "output": self.plant.pwm,
            "setpoint_c": self.cfg["task"]["target_temperature_c"],
        }
        if not is_legacy_temperature(self.cfg):
            state.update(value=state.pop('temperature_c'), setpoint=state.pop('setpoint_c'),
                         value_unit=quantity(self.cfg)['unit'], process_kind=quantity(self.cfg)['kind'])
        return state

    def apply(self, pid, setpoint, expected_revision):
        if expected_revision != self.revision:
            raise DeviceError("configuration revision conflict")
        magnitude = import_pid(self.cfg, pid)
        self.cfg["task"]["target_temperature_c"] = setpoint
        self.cfg["task"]["initial_output"] = self.plant.pwm
        self.cfg["task"]["initial_temperature_c"] = self.plant.temp
        self.controller = Controller(self.cfg, magnitude)
        self.pid = deepcopy(pid)
        self.revision += 1
        return {"ok": True, "revision": self.revision}

    def close(self):
        pass


def connect(cfg):
    if cfg["mode"] != "use":
        raise DeviceError("device creation is forbidden outside use mode")
    kind = cfg["device"]["adapter"]
    if kind == "simulated":
        return SimulatedDevice(cfg)
    if kind in ("tcp", "serial"):
        return Gateway(cfg)
    if kind == "custom":
        device = load_factory(cfg["device"]["custom_factory"])(deepcopy(cfg))
        if not all(
            callable(getattr(device, name, None)) for name in ("read_state", "apply", "close")
        ):
            raise DeviceError("custom adapter requires read_state, apply, close")
        return device
    raise DeviceError("device adapter disabled")


def adapt_state(cfg, state):
    if not isinstance(state, dict):
        raise DeviceError('invalid state object')
    state = deepcopy(state)
    if not is_legacy_temperature(cfg):
        p = quantity(cfg)
        if state.get('value_unit') != p['unit'] or state.get('process_kind') != p['kind']:
            raise DeviceError('device process quantity/unit mismatch')
        state['temperature_c'] = state.get('value')
        state['setpoint_c'] = state.get('setpoint')
    return state


def validate_state(cfg, state):
    state = adapt_state(cfg, state)
    if not isinstance(state, dict):
        raise DeviceError("invalid state object")
    expected = {
        "object_id": cfg["device"]["object_id"],
        "sample_time_s": cfg["controller"]["sample_time_s"],
        "output_unit": cfg["actuator"]["unit"],
        "output_min": cfg["actuator"]["min"],
        "output_max": cfg["actuator"]["max"],
        "derivative_on": cfg["controller"]["derivative_on"],
        "derivative_filter_s": cfg["controller"]["derivative_filter_s"],
        "max_rate_per_s": cfg["actuator"]["max_rate_per_s"],
        "anti_windup": "conditional",
        "initialization": cfg["controller"]["initialization"],
        "output_bias": cfg["model"]["operating_output"],
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise DeviceError(f"device/config mismatch: {key}")
    revision = state.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, (str, int))
        or isinstance(revision, str)
        and not revision
    ):
        raise DeviceError("device configuration revision missing")
    for key in ("timestamp_s", "temperature_c", "output", "setpoint_c"):
        value = state.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise DeviceError(f"invalid state: {key}")
    age = time.time() - state["timestamp_s"]
    if not -1 <= age <= cfg["device"]["max_sample_age_s"]:
        raise DeviceError("stale or future device timestamp")
    if not cfg["actuator"]["min"] <= state["output"] <= cfg["actuator"]["max"]:
        raise DeviceError("device output outside range")
    if (
        not cfg["device"]["monitor_min_temperature_c"]
        <= state["temperature_c"]
        <= cfg["device"]["monitor_max_temperature_c"]
    ):
        raise DeviceError("device temperature outside monitor bounds")
    import_pid(cfg, state.get("pid", {}))
    return state


def same_parameters(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if set(left) != set(right):
        return False
    return all(
        (
            math.isclose(v, right[key], rel_tol=1e-8, abs_tol=1e-10)
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and isinstance(right[key], (int, float))
            else v == right[key]
        )
        for key, v in left.items()
    )


def deploy(cfg, device, before, recommendation, audit, cancelled=None):
    """CAS write, independent readback, bounded monitoring and conflict-aware restore."""
    wanted = recommendation["export_pid"]
    target = cfg["task"]["target_temperature_c"]
    owned_revision = None
    try:
        checkpoint(cancelled)
        if (
            cfg["mode"] != "use"
            or not cfg["device"]["write_enabled"]
            or not recommendation["metrics"]["eligible"]
        ):
            raise DeviceError("write requires use mode, enabled writes and a qualified simulation")
        fresh = validate_state(cfg, device.read_state())
        if (
            fresh["revision"] != before["revision"]
            or not same_parameters(fresh["pid"], before["pid"])
            or fresh["setpoint_c"] != before["setpoint_c"]
        ):
            raise DeviceError("configuration changed during offline planning; refusing write")
        if (
            abs(fresh["temperature_c"] - before["temperature_c"])
            > cfg["device"]["max_planning_temperature_change_c"]
            or abs(fresh["output"] - before["output"]) > cfg["device"]["max_planning_output_change"]
        ):
            raise DeviceError(
                "operating state moved too far during planning; regenerate recommendation"
            )
        checkpoint(cancelled)
        ack = device.apply(wanted, target, before["revision"])
        if (
            not isinstance(ack, dict)
            or ack.get("ok") is not True
            or type(ack.get("revision")) not in (str, int)
            or ack.get("revision") in ("", before["revision"])
        ):
            raise DeviceError("invalid write acknowledgement; write state is uncertain")
        owned_revision = ack["revision"]
        audit.append({"event": "write_ack", "revision": owned_revision})
        after = validate_state(cfg, device.read_state())
        if (
            after["revision"] != owned_revision
            or not same_parameters(after["pid"], wanted)
            or after["setpoint_c"] != target
        ):
            raise DeviceError("parameter readback mismatch")
        audit.append({"event": "readback_verified", "state": after})
        deadline = time.monotonic() + cfg["device"]["monitor_duration_s"]
        while time.monotonic() < deadline:
            checkpoint(cancelled)
            if cfg["device"]["adapter"] != "simulated":
                time.sleep(
                    min(cfg["device"]["monitor_interval_s"], max(0, deadline - time.monotonic()))
                )
            state = validate_state(cfg, device.read_state())
            if (
                state["revision"] != owned_revision
                or not same_parameters(state["pid"], wanted)
                or state["setpoint_c"] != target
            ):
                raise DeviceError("external configuration change during monitoring")
            audit.append({"event": "monitor", "state": state})
            if cfg["device"]["adapter"] == "simulated" and len(
                [r for r in audit if r["event"] == "monitor"]
            ) >= math.ceil(
                cfg["device"]["monitor_duration_s"] / cfg["device"]["monitor_interval_s"]
            ):
                break
        audit.append(
            {
                "event": "completed",
                "scope": (
                    "simulated adapter"
                    if cfg["device"]["adapter"] == "simulated"
                    else "bounded device monitoring"
                ),
            }
        )
    except BaseException as exc:
        audit.append({"event": "fault", "error": type(exc).__name__, "detail": str(exc)})
        if owned_revision is not None and cfg["device"]["restore_on_fault"]:
            try:
                # Read without accepting unsafe PV; restoration changes parameters only.
                state = adapt_state(cfg, device.read_state())
                if (
                    state.get("object_id") != cfg["device"]["object_id"]
                    or state.get("revision") != owned_revision
                ):
                    raise DeviceError("restoration refused: another writer owns the revision")
                age = time.time() - state.get("timestamp_s", -math.inf)
                if not -1 <= age <= cfg["device"]["max_sample_age_s"]:
                    raise DeviceError("restoration refused: stale state")
                ack = device.apply(before["pid"], before["setpoint_c"], owned_revision)
                restored = adapt_state(cfg, device.read_state())
                if (
                    ack.get("ok") is not True
                    or ack.get("revision") == owned_revision
                    or restored.get("object_id") != cfg["device"]["object_id"]
                    or not -1
                    <= time.time() - restored.get("timestamp_s", -math.inf)
                    <= cfg["device"]["max_sample_age_s"]
                    or restored.get("revision") != ack.get("revision")
                    or not same_parameters(restored.get("pid", {}), before["pid"])
                    or restored.get("setpoint_c") != before["setpoint_c"]
                ):
                    raise DeviceError("restoration not verified")
                audit.append({"event": "restored", "revision": ack["revision"]})
            except Exception as restore_exc:
                audit.append({"event": "restore_failed", "detail": str(restore_exc)})
        raise
