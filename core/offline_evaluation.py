"""Full-horizon, constant-setpoint metrics; timestamps explicitly in seconds."""
import math


def evaluate_step(rows, initial_temp, setpoint, initial_output=0.0, settling_fraction=0.02):
    if len(rows) < 2 or setpoint == initial_temp:
        raise ValueError("need >=2 samples and a nonzero setpoint step")
    times = [r["time_s"] for r in rows]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("time must increase strictly")
    if not all(math.isfinite(r[k]) for r in rows for k in ("time_s", "input", "pwm")):
        raise ValueError("nonfinite trajectory")
    errors = [setpoint - r["input"] for r in rows]
    amplitude = abs(setpoint - initial_temp)
    direction = 1 if setpoint > initial_temp else -1
    overshoot = max(0.0, max(direction * (r["input"] - setpoint) for r in rows))
    iae = sum((abs(a) + abs(b)) * 0.5 * (t1 - t0)
              for a, b, t0, t1 in zip(errors, errors[1:], times, times[1:]))
    band = amplitude * settling_fraction
    outside = [i for i, e in enumerate(errors) if abs(e) > band]
    last_outside = outside[-1] if outside else -1
    # Must remain in band for >=5% of horizon; a last-sample crossing is not settling.
    settled_index = last_outside + 1
    settling = None
    if settled_index < len(rows) and times[-1] - times[settled_index] >= 0.05 * (times[-1] - times[0]):
        settling = times[settled_index] - times[0]
    tail_start = times[0] + 0.9 * (times[-1] - times[0])
    tail = [e for t, e in zip(times, errors) if t >= tail_start]
    outputs = [initial_output] + [r["pwm"] for r in rows]
    changes = [abs(b-a) for a, b in zip(outputs, outputs[1:])]
    return {"overshoot_pct": overshoot / amplitude * 100, "overshoot_c": overshoot,
            "iae_c_s": iae, "steady_state_error_c": sum(abs(e) for e in tail) / len(tail),
            "steady_state_signed_error_c": sum(tail) / len(tail),
            "settling_time_s": settling, "settling_band_c": band,
            "output_total_variation": sum(changes), "output_max_step": max(changes),
            "output_min": min(r["pwm"] for r in rows),
            "output_max": max(r["pwm"] for r in rows)}
