#!/usr/bin/env python3
"""Offline thermal tuning comparison. Standard library only; no field I/O or LLM."""
import argparse
import csv
import json
import math
from pathlib import Path

from core.buffer import AdvancedDataBuffer
from core.offline_evaluation import evaluate_step
from sim.model import HeatingSimulator, CONTROL_INTERVAL
from system_id import system_identify, tuning_candidates, read_from_file
from pid_safety import apply_pid_guardrails, get_pid_limits
from core.config import CONFIG, ensure_utf8_console


DEFAULT_CURRENT_PID = {"p": 1.0, "i": 0.1, "d": 0.05}


def checked_gains(gains, current_pid, limits):
    """All algorithm sources share pid_safety's deterministic gain constraints."""
    candidate = {key: float(gains[label]) for key, label in (("p","Kp"),("i","Ki"),("d","Kd"))}
    if not all(math.isfinite(v) for v in candidate.values()):
        raise ValueError("nonfinite PID suggestion rejected before simulation")
    safe, notes = apply_pid_guardrails(current_pid, candidate, limits=limits)
    kp, ki, kd = safe["p"], safe["i"], safe["d"]
    applied = {**gains, "controller_form": "parallel", "Kp": kp, "Ki": ki, "Kd": kd,
               "Ti": kp/ki if kp != 0 and ki != 0 else None,
               "Td": kd/kp if kp != 0 else (0.0 if kd == 0 else None),
               "formula": f"Kp={kp:.6g}, Ki={ki:.6g}, Kd={kd:.6g} (after pid_safety)"}
    return applied, notes


class FOPDTPlant:
    """Deviation FOPDT about ambient, with interpolated delayed zero-order-held input."""
    def __init__(self, K, tau, theta, dt, ambient=70.0):
        self.K, self.tau, self.theta, self.dt = K, tau, theta, dt
        self.temp = self.ambient = ambient
        self.pwm = 0.0
        self.time = 0.0
        self.commands = []

    def update(self):
        self.commands.append(self.pwm)
        # Exact integration over the interval, splitting at a fractional delay boundary.
        delay_steps = self.theta / self.dt
        whole = int(math.floor(delay_steps))
        fraction = delay_steps - whole
        index = len(self.commands) - 1 - whole
        older = self.commands[index-1] if index-1 >= 0 else 0.0
        newer = self.commands[index] if index >= 0 else 0.0
        for u, duration in ((older, fraction*self.dt), (newer, (1-fraction)*self.dt)):
            target = self.ambient + self.K * u
            self.temp = target + (self.temp-target) * math.exp(-duration/self.tau)
        self.time += self.dt


class ParallelController:
    """Common seconds-based controller: conditional integration, derivative on measurement.

    Same gains/form as HeatingSimulator; derivative implementation and anti-windup
    intentionally suit thermal comparison. No series/ideal gains accepted here.
    """
    def __init__(self, gains, dt, initial_temp, lower=0.0, upper=255.0):
        self.kp, self.ki, self.kd = (gains[k] for k in ("Kp", "Ki", "Kd"))
        self.dt, self.previous, self.integral = dt, initial_temp, 0.0
        self.lower, self.upper = lower, upper

    def compute(self, setpoint, temperature):
        error = setpoint - temperature
        derivative = -(temperature - self.previous) / self.dt
        proposed = self.integral + self.ki * error * self.dt
        raw = self.kp * error + proposed + self.kd * derivative
        increment = self.ki * error
        if not ((raw > self.upper and increment > 0) or (raw < self.lower and increment < 0)):
            self.integral = proposed
        raw = self.kp * error + self.integral + self.kd * derivative
        self.previous = temperature
        return min(self.upper, max(self.lower, raw))


def heating_probe():
    probe = HeatingSimulator(random_seed=0)
    probe.noise_level = 0.0
    t, y, u = [0.0], [probe.temp], [0.0]
    for _ in range(1000):
        probe.pwm = 100.0
        probe.update()
        t.append(probe.timestamp/1000.0)
        y.append(probe.temp)
        u.append(probe.pwm)
    return system_identify(t, y, u, time_unit="s")


def compare(plant_factory, candidates, setpoint, dt, duration, current_pid=None, limits=None,
            controller_kind="thermal"):
    if controller_kind not in ("thermal", "native"):
        raise ValueError("controller_kind must be thermal or native")
    limits = get_pid_limits("python_sim") if limits is None else limits
    baseline = dict(DEFAULT_CURRENT_PID if current_pid is None else current_pid)
    if set(baseline) != {"p", "i", "d"} or not all(math.isfinite(v) and v >= 0 for v in baseline.values()):
        raise ValueError("current PID must contain finite nonnegative p/i/d")
    baseline, baseline_notes = apply_pid_guardrails(baseline, baseline, limits=limits)
    results, traces = {}, {}
    for name, requested in candidates.items():
        if "error" in requested:
            results[name] = {"error": requested["error"]}
            continue
        try:
            gains, notes = checked_gains(requested, baseline, limits)
        except (ValueError, TypeError, KeyError) as exc:
            results[name] = {"error": str(exc), "safety_status": "rejected"}
            continue
        # No plant construction or update occurs until the suggestion has been checked.
        plant = plant_factory()
        if isinstance(plant, HeatingSimulator):
            plant.set_pid(gains["Kp"], gains["Ki"], gains["Kd"])
            plant.dynamic_setpoint = False
        elif controller_kind == "native":
            raise ValueError("native controller requires HeatingSimulator")
        initial = plant.temp
        controller = ParallelController(gains, dt, initial)
        rows = [{"time_s": 0.0, "input": initial, "setpoint": setpoint, "pwm": 0.0}]
        for index in range(int(round(duration/dt))):
            if controller_kind == "native":
                plant.compute_pid()
            else:
                plant.pwm = controller.compute(setpoint, plant.temp)
            plant.update()
            rows.append({"time_s": (index+1)*dt, "input": plant.temp,
                         "setpoint": setpoint, "pwm": plant.pwm})
        metrics = evaluate_step(rows, initial, setpoint)
        # Reuse original evaluation too; retain its definitions under a separate key.
        buffer = AdvancedDataBuffer(max_size=len(rows))
        for row in rows:
            buffer.add({**row, "timestamp": row["time_s"]*1000})
        metrics["legacy_metrics"] = buffer.calculate_advanced_metrics()
        metrics["saturation_fraction"] = sum(r["pwm"] in (0.0, 255.0) for r in rows[1:]) / (len(rows)-1)
        results[name] = {"requested_gains": requested, "gains": gains, "metrics": metrics,
                         "guardrail_notes": notes, "baseline_pid": baseline,
                         "baseline_guardrail_notes": baseline_notes,
                         "safety_status": "adjusted" if notes else "passed"}
        traces[name] = rows
    return results, traces


def main():
    ensure_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plant", choices=["fopdt", "heating"], default="fopdt")
    parser.add_argument("--K", type=float, default=0.8, help="degC per output unit")
    parser.add_argument("--tau", type=float, default=300.0)
    parser.add_argument("--theta", type=float, default=20.0)
    parser.add_argument("--lambda", dest="lambda_", type=float, default=None)
    parser.add_argument("--dt", type=float, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--setpoint", type=float, default=None)
    parser.add_argument("--id-file", help="Historical single-step CSV; replaces K/tau/theta")
    parser.add_argument("--time-unit", choices=["s", "ms"], default="ms")
    parser.add_argument("--current-pid", type=float, nargs=3, metavar=("P", "I", "D"),
                        default=(1.0, 0.1, 0.05), help="common reference gains for pid_safety checks")
    parser.add_argument("--llm-file", type=Path, help="saved LLM parallel suggestion JSON with p/i/d; no API call")
    parser.add_argument("--out", type=Path, default=Path("results/offline"))
    args = parser.parse_args()
    identification = None
    if args.id_file and args.plant != "fopdt":
        parser.error("--id-file applies to --plant fopdt; heating uses its own probe")
    if args.plant == "heating":
        identification = heating_probe()
    elif args.id_file:
        identification = read_from_file(args.id_file, args.time_unit, args.lambda_)
    if identification is not None:
        if "error" in identification:
            parser.error(identification["error"])
        m = identification["model"]
        args.K, args.tau, args.theta = m["K"], m["tau"], m["theta"]
    if not all(math.isfinite(v) for v in (args.K, args.tau, args.theta)) or args.K == 0 or args.tau <= 0 or args.theta < 0:
        parser.error("finite nonzero K, positive tau and nonnegative theta required")
    if args.K < 0:
        parser.error("negative-gain plants are not supported by the existing nonnegative pid_safety policy")
    dt = args.dt if args.dt is not None else (CONTROL_INTERVAL if args.plant == "heating" else min(1.0, args.tau/50))
    duration = args.duration if args.duration is not None else max(200.0, 8*args.tau)
    setpoint = args.setpoint if args.setpoint is not None else (100.0 if args.plant == "heating" else 70.0 + math.copysign(10.0, args.K))
    if not all(math.isfinite(v) for v in (dt, duration, setpoint)) or dt <= 0 or duration < 2*dt:
        parser.error("finite dt>0, duration>=2*dt, and setpoint required")
    if args.plant == "heating" and dt != CONTROL_INTERVAL:
        parser.error("native heating plant requires --dt 0.2")
    ambient = 20.0 if args.plant == "heating" else 70.0
    if setpoint == ambient:
        parser.error("setpoint must differ from ambient")
    def plant_factory():
        if args.plant == "heating":
            sim = HeatingSimulator(random_seed=0, setpoint=setpoint)
            sim.noise_level = 0.0
            return sim
        return FOPDTPlant(args.K, args.tau, args.theta, dt)
    candidates = tuning_candidates(args.K, args.tau, args.theta, args.lambda_)
    if "error" in candidates["SIMC_PI"]:
        parser.error(candidates["SIMC_PI"]["error"])
    if args.llm_file:
        try:
            proposal = json.loads(args.llm_file.read_text(encoding="utf-8-sig"))
            llm_gains = {label: float(proposal[key]) for key,label in (("p","Kp"),("i","Ki"),("d","Kd"))}
            if not all(math.isfinite(v) for v in llm_gains.values()):
                raise ValueError("LLM proposal gains must be finite")
            candidates["LLM"] = {**llm_gains, "method": "LLM", "controller_form": "parallel"}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"invalid --llm-file: {exc}")
    baseline = dict(zip(("p","i","d"), args.current_pid))
    limits = get_pid_limits("python_sim")
    try:
        results, traces = compare(plant_factory, candidates, setpoint, dt, duration, baseline, limits)
    except ValueError as exc:
        parser.error(str(exc))
    report = {"plant": args.plant, "model": {"K": args.K, "tau": args.tau, "theta": args.theta},
              "identification": identification, "settings": {"dt_s": dt, "duration_s": round(duration/dt)*dt,
              "setpoint_c": setpoint, "noise": 0, "output_limits": [0, 255],
              "controller": "parallel; derivative on measurement; conditional integration; fixed setpoint"},
              "safety": {"entry_point": "pid_safety.apply_pid_guardrails", "limits": limits,
                         "reference_pid": baseline, "global_max_increase_ratio": CONFIG.get("PID_MAX_INCREASE_RATIO", 0.0),
                         "scope": "deterministic parameter constraints; not chemical process safety"},
              "results": results}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/"summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for name, rows in traces.items():
        with (args.out/f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"{args.plant}: K={args.K:.6g}, tau={args.tau:.6g}s, theta={args.theta:.6g}s")
    print("Comparison uses pid_safety-checked gains; requested and applied gains are saved separately.")
    print("method       overshoot%   IAE(C*s)   tail_MAE(C)   settling(s)   output_TV")
    for name, result in results.items():
        if "error" in result:
            print(name, result["error"])
            continue
        m = result["metrics"]
        settling = f"{m['settling_time_s']:.1f}" if m["settling_time_s"] is not None else "unsettled"
        print(f"{name:12} {m['overshoot_pct']:10.3f} {m['iae_c_s']:10.3f} {m['steady_state_error_c']:13.5f} {settling:>13} {m['output_total_variation']:11.3f}")
        for note in result["guardrail_notes"]:
            print(f"  [pid_safety] {note}")
    print(f"Saved {args.out.resolve()}")


if __name__ == "__main__":
    main()
