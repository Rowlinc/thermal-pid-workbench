# Changelog

## 0.4.0 — 2026-10-09

- Add a Windows desktop application and loopback browser UI with editable configurations, CSV preview/column mapping, model import, LLM service/key settings, device adapters and original workflows.
- View results directly in the application and retain independent run records, curves, configuration, PID, metrics, guardrails and LLM history. Restore a record's settings for another run; exports remain optional.
- Generalize process metadata, configured units, plots, metrics, LLM context and gateway checks to temperature, pressure, flow, level, speed and custom variables; support generic configuration aliases while retaining old temperature keys.
- Add integrating dynamics and IPDT SIMC PI; refuse FOPDT CSV/Z-N formulas for integrating models. Add manual PID initialization for custom models and generic `value/step` Python file interfaces.
- Preserve original launcher, serial hardware profiles, Python simulation, Simulink/multiple controller support and original files; expose entry routes and full dependency installation.
- Add cooperative cancellation and live candidate snapshots, protect the local API with a session/host/origin check, keep credentials out of exports, and build from an explicit public resource allowlist.
- Validate 440 project tests and 327 upstream contract tests (11 subtests in each run), actual Edge UI operations and the native Windows renderer. Physical hardware/real MATLAB commissioning remains outside this verification.
- Produce a one-file Windows executable and portable ZIP with documentation/license; local results and private credentials are excluded.

## Unreleased — 2026-10-09

- Remove unused `_help` metadata from current configurations/schema, redundant task aliases and duplicate example reports; retain compatibility when reading old configurations and keep one public reference snapshot in `result/`.
- Document virtual-environment installation and complete test/use workflows with and without LLMs; clarify private config.json credentials versus task presets and fix root credential paths in examples.
- Separate offline analytical gain validation from device-relative increase checks using `controller.guardrail_policy`; preserve LLM incremental checks and the existing use-mode final jump constraint, with `relative` available for prior-result reproduction.
- Save each unified project run in a unique timestamp folder; preserve previous reports and handle timestamp collisions.
- Add six annotated temperature-task, lag/delay and disturbance configurations, plus a repeatable comparison runner.
- Summarize every completed arm, including disqualified results, LLM activity, parameter changes and saturation fractions; distinguish the original-identification baseline from an independent full upstream execution.

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
