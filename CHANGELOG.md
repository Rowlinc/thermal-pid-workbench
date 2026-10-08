# Changelog

## 0.3.0 — 2026-10-08

- Add `project.json` and `thermal-pid` / `pid_project.py` as the unified thermal entry point.
- Accept known FOPDT parameters, single-step CSV history, built-in heating models and custom model factories.
- Apply output units, bounds, slew rate, PID sample interval and physical task settings consistently.
- Keep Z-N PID/PI and add SIMC PI. IMC is background theory only.
- Compare the original identifier + Z-N, corrected identifier + Z-N, and automatic selection on the same task.
- Route analytical and LLM proposals through `pid_safety.py`; validate the final jump from current parameters again.
- Preserve best parameters and rollback. The new workflow scores complete, reset experiments so each proposal has a comparable start state.
- Add full-task performance criteria, reports, signed parameter export and seconds/minutes conversion.
- Make LLM timeouts, retry counts, output size and JSON output configurable; preserve request options on HTTP fallback and support DeepSeek's explicit thinking setting.
- Add `test` and `use` modes with simulated, TCP, serial and custom gateway adapters, revision checks, readback and bounded monitoring.
- Retain upstream legacy entry points and reference identification source under Apache-2.0.

The 0.3 series is an integration release. Physical controller commissioning and process validation are application-specific and have not been performed by the bundled offline tests.
