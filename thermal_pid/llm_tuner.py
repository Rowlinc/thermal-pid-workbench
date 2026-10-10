"""Thermal-specific prompt over the upstream LLM transport and response parser."""

import json


class ConfiguredTuner:
    def __init__(self, client):
        self.client = client

    @property
    def last_request_diagnostic(self):
        return getattr(self.client, 'last_request_diagnostic', {})

    def analyze(self, prompt_data, history_text, tuning_mode=None, prompt_context=None):
        system = """You propose PID parameters for a single-loop process in OFFLINE simulation.
Use the provided model, physical output units, sampling interval and evaluation constraints.
The supplied gains are an already tuned starting point. Preserve useful integral action;
do not restart a compulsory P-only tuning sequence. Seek qualified trials first, then
the context.selection_priority objective. Passing acceptance limits is not evidence
that the gains are optimal. If a starting trial is eligible, still propose one
moderate, informative gain change directed at the objective, using the runtime
increase limits. Preserve useful integral action and use measured feedback to
choose the next direction. For smooth priority with zero overshoot, improve the
next objective (output total variation), instead of treating zero overshoot as
a reason to stop. When saturated, reason about actuator range and slew limits:
higher gains may not speed up the initial response and may worsen overshoot.
Do not return unchanged gains with DONE on the first request merely because
all limits pass. Use DONE only after at least two distinct evaluated proposals
in the supplied history fail to improve the selected objective, or after a
justified optimum is supported by measurements. Never invent trial results.
Accuracy prioritizes full-task IAE then
output total variation; smooth prioritizes overshoot then output total variation;
speed prioritizes settling time then IAE. A configured absolute
process-variable bound is separate from the overshoot percentage requirement.
Use the specified process name and unit (temperature, pressure, flow, level or custom).
Legacy keys containing temperature or _c refer to that process variable in its configured
unit; do not assume Celsius or convert units. Respect the stated model applicability.
All gains p, i, d must be nonnegative finite magnitudes in continuous parallel form,
with seconds as the time base: u = bias + direction*(p*error + i*integral(error dt)
+ d*filtered_derivative). Direction is supplied by the process gain sign outside this
response. Never emit actuator commands or device writes. Respect runtime gain limits
and increase ratios. Violations reject the entire proposal without clipping or
simulation. Use rejection reasons in the history to submit a complete valid proposal.
A DONE status is a suggestion; the program verifies the entire
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
