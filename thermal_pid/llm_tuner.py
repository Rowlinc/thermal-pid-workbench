"""Thermal-specific prompt over the upstream LLM transport and response parser."""

import json


class ConfiguredTuner:
    def __init__(self, client):
        self.client = client

    def analyze(self, prompt_data, history_text, tuning_mode=None, prompt_context=None):
        system = """You propose PID parameters for a single-loop process in OFFLINE simulation.
Use the provided model, physical output units, sampling interval and evaluation constraints.
The supplied gains are an already tuned starting point. Preserve useful integral action;
do not restart a compulsory P-only tuning sequence. Seek qualified trials first, then
smaller full-task IAE, then smaller output total variation. A configured absolute
process-variable bound is separate from the overshoot percentage requirement.
Use the specified process name and unit (temperature, pressure, flow, level or custom).
Legacy keys containing temperature or _c refer to that process variable in its configured
unit; do not assume Celsius or convert units. Respect the stated model applicability.
All gains p, i, d must be nonnegative finite magnitudes in continuous parallel form,
with seconds as the time base: u = bias + direction*(p*error + i*integral(error dt)
+ d*filtered_derivative). Direction is supplied by the process gain sign outside this
response. Never emit actuator commands or device writes. Respect runtime gain limits
and increase ratios. A DONE status is a suggestion; the program verifies the entire
task again. Output only JSON with p, i, d, analysis_summary, and status (TUNING or DONE).
Give a brief engineering rationale, not a chain of reasoning."""
        return self.client.request_json(
            system_prompt=system,
            user_prompt=json.dumps(
                {
                    "context": prompt_context,
                    "current_trial": json.loads(prompt_data),
                    "history": json.loads(history_text),
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
        )
