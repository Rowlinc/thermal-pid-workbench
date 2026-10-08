"""Deterministic Z-N/SIMC selection before entering the existing LLM engine."""
from copy import deepcopy
import math

from offline_compare import compare, checked_gains
from pid_safety import apply_pid_guardrails, get_pid_limits
from sim.model import HeatingSimulator, CONTROL_INTERVAL
from system_id import system_identify, tuning_candidates


DEFAULT_SELECTION_RULES = {"max_overshoot_pct": 5.0, "max_tail_error_c": 0.3,
                           "require_settled": True}


def probe_heating(sim=None, duration=200.0, step_output=100.0):
    probe = deepcopy(sim) if sim is not None else HeatingSimulator(random_seed=0)
    probe.temp = probe.heater_temp = probe.ambient_temp
    probe.timestamp = probe.step_count = 0
    probe.pwm = probe.integral = probe.prev_error = 0.0
    probe.noise_level = 0.0
    times, temps, inputs = [0.0], [probe.temp], [0.0]
    for _ in range(round(duration / CONTROL_INTERVAL)):
        probe.pwm = step_output
        probe.update()
        times.append(probe.timestamp / 1000.0)
        temps.append(probe.temp)
        inputs.append(step_output)
    return system_identify(times, temps, inputs, time_unit="s")


def choose_candidate(results, baseline, rules=None):
    """Feasibility first, then IAE, output variation, settling time, stable name tie-break."""
    rules = {**DEFAULT_SELECTION_RULES, **(rules or {})}
    for key in ("max_overshoot_pct", "max_tail_error_c"):
        if not math.isfinite(rules[key]) or rules[key] < 0:
            raise ValueError(f"{key} must be finite and nonnegative")
    ranking = []
    for name, result in results.items():
        reasons = []
        metrics = result.get("metrics", {})
        if "error" in result:
            reasons.append(result["error"])
        elif not all(math.isfinite(metrics.get(key, math.nan)) for key in
                     ("overshoot_pct", "steady_state_error_c", "iae_c_s", "output_total_variation")):
            reasons.append("nonfinite metrics")
        else:
            if metrics["overshoot_pct"] > rules["max_overshoot_pct"]:
                reasons.append("overshoot exceeds selection limit")
            if metrics["steady_state_error_c"] > rules["max_tail_error_c"]:
                reasons.append("tail error exceeds selection limit")
            if rules["require_settled"] and metrics["settling_time_s"] is None:
                reasons.append("not settled during observation")
        result["selection"] = {"eligible": not reasons, "reasons": reasons}
        if not reasons:
            ranking.append((metrics["iae_c_s"], metrics["output_total_variation"],
                            metrics["settling_time_s"] if metrics["settling_time_s"] is not None else math.inf, name))
    if not ranking:
        return {"selected_method": "CURRENT_PID", "selected_pid": dict(baseline),
                "status": "no_eligible_candidate", "rules": rules,
                "reason": "No candidate met selection rules; retain checked current PID and let the existing engine evaluate it."}
    winner = min(ranking)[-1]
    gains = results[winner]["gains"]
    return {"selected_method": winner, "selected_pid": {"p":gains["Kp"],"i":gains["Ki"],"d":gains["Kd"]},
            "status": "selected", "rules": rules,
            "ranking": [item[-1] for item in sorted(ranking)],
            "reason": "Meets response limits; lowest IAE, then lowest output variation and settling time."}


def select_initial_pid(identification, plant_factory, current_pid, setpoint, dt, duration,
                       lambda_=None, limits=None, rules=None, controller_kind="thermal"):
    if "error" in identification:
        raise ValueError(identification["error"])
    limits = get_pid_limits("python_sim") if limits is None else limits
    if not all(math.isfinite(current_pid[k]) and current_pid[k] >= 0 for k in ("p","i","d")):
        raise ValueError("current PID must be finite and nonnegative")
    baseline, baseline_notes = apply_pid_guardrails(current_pid, current_pid, limits)
    model = identification["model"]
    candidates = tuning_candidates(model["K"], model["tau"], model["theta"], lambda_)
    results, traces = compare(plant_factory, candidates, setpoint, dt, duration,
                              baseline, limits, controller_kind)
    choice = choose_candidate(results, baseline, rules)
    # A final application check is relative to the same current PID, never a new bypass.
    safe, final_notes = apply_pid_guardrails(baseline, choice["selected_pid"], limits)
    choice["selected_pid"] = safe
    return {"identification": identification, "candidate_results":results,
            "selection":choice, "baseline_pid":baseline, "baseline_guardrail_notes":baseline_notes,
            "application_guardrail_notes":final_notes, "limits":limits,
            "settings":{"setpoint_c":setpoint,"dt_s":dt,"duration_s":duration,
                        "controller_kind":controller_kind}}, traces


def original_zn_initialization(current_pid, buffer_size=100, limits=None):
    """Exact original short probe + original system_id snapshot; same parameter policy."""
    from reference import original_system_id as original
    probe = HeatingSimulator(random_seed=0)
    probe.set_pid(0.0,0.0,0.0)
    times, temps, inputs = [], [], []
    for _ in range(max(40,min(80,int(buffer_size)))):
        probe.pwm = 255.0
        probe.update()
        data=probe.get_data()
        times.append(data["timestamp"])
        temps.append(data["input"])
        inputs.append(data["pwm"])
    identification=original.system_identify(times,temps,inputs)
    candidate=original.extract_initial_pid(identification,"PID")
    limits=get_pid_limits("python_sim") if limits is None else limits
    baseline, baseline_notes=apply_pid_guardrails(current_pid,current_pid,limits)
    if candidate is None:
        return {"identification":identification,"selected_pid":baseline,
                "requested_pid":None,"guardrail_notes":baseline_notes,
                "status":"original_warm_start_skipped"}
    safe, notes=apply_pid_guardrails(baseline,candidate,limits)
    return {"identification":identification,"selected_pid":safe,"requested_pid":candidate,
            "guardrail_notes":notes,"status":"original_zn"}
