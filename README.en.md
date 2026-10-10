# Thermal PID Workbench

Version 0.4 adds a visual Windows desktop application for single-loop temperature, pressure, flow, level, speed and custom process variables. Run `PIDWorkbench.exe`, or install `.[full,desktop]` in a virtual environment and run `desktop_app.py`.
Runs are viewed directly inside the app and retained in persistent history, including configuration, response curves, PID, metrics, guardrails and LLM rounds. HTML/CSV/JSON are optional exports.
Settings, CSV uploads/column mapping, model parameters, custom Python models, LLM API credentials, device adapters and original serial/Simulink workflows are available visually.
See [desktop guide](docs/DESKTOP.md) and [upstream compatibility](docs/UPSTREAM_COMPATIBILITY.md). The generic FOPDT fitter is not an identifier for arbitrary models; integrating dynamics use their own SIMC formula or user PID.

Thermal PID identification, Z-N/SIMC tuning, simulation evaluation and gateway integration. Derived from [KINGSTON-115/llm-pid-tuner](https://github.com/KINGSTON-115/llm-pid-tuner), under Apache-2.0.

[中文完整教程](README.md) · [Configuration](docs/CONFIGURATION.md) · [Methods](docs/METHODS.md) · [Device protocol](docs/DEVICE_PROTOCOL.md)

## Files and modes

`project.json` contains the model/history, task, actuator/controller, criteria, LLM service settings and adapter configuration. Private root `config.json` supplies `LLM_API_KEY`; the unified entry point reads service/model settings from the task configuration. `pid_project.py` is the entry point.

Use `project.json` for tasks/LLM settings and root `config.json` for private credentials. No separate DeepSeek task file is required.

| Workflow | mode | llm.enabled | Behavior |
|---|---|---|---|
| Test without LLM | test | false | Local identification, analytical tuning, simulation and reports |
| Test with LLM | test | true | Local simulation plus online API suggestions |
| Use without LLM | use | false | Offline validation, device checks, conditional PID/setpoint write, readback and bounded monitoring |
| Use with LLM | use | true | Use workflow plus LLM suggestions; LLM does not issue device commands |

Use mode is an integration workflow, not evidence of physical commissioning. The device owns continuous control; DCS/PLC/SIS protections remain independent. Test mode does not connect hardware. Checked-in defaults are test mode, LLM off and a simulated 30→100°C task.

## First-time installation

Use Python 3.10+. Clone or enter your extracted ZIP directory. Run all subsequent commands from the repository root:

```powershell
git clone https://github.com/Rowlinc/thermal-pid-workbench.git
cd thermal-pid-workbench
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . --index-url https://pypi.org/simple
```

Create the environment once and reuse it. Explicit interpreter paths avoid activation and execution-policy changes. Analytical code uses the standard library; project installation is recommended for a consistent environment. LLMs need API clients:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[llm]" --index-url https://pypi.org/simple
```

Serial needs `.[serial]`; LLM plus serial needs `.[llm,serial]`. Installation needs internet access and does not download a language model. Linux/macOS: create with `python3 -m venv .venv`, install using `./.venv/bin/python -m pip install -e .` and optionally `-e '.[llm]'`; replace Windows interpreter paths below accordingly.

## 1. Test without LLM

After basic installation, save these existing root `project.json` fields: mode=test, llm.enabled=false, device.adapter=disabled, device.write_enabled=false. Review your model, task, output limits, controller and evaluation settings; defaults are not your physical device data.

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.json
```

Validation checks configuration only, not API access or device compatibility. Open the current printed `results/project/<timestamp>/report.html` and `pid.json`. Null PID means no qualified candidate.

## 2. Test with LLM

Install `.[llm]`. Create/edit root `config.json` as plain JSON without comments, preserving other existing fields:

```json
{"LLM_API_KEY":"replace-with-your-actual-key"}
```

Never publish actual credentials. Edit these existing fields in root project.json; this is only a fragment, not a replacement for the whole task:

```json
{
  "mode":"test",
  "llm":{"enabled":true,"provider":"openai","base_url":"https://api.deepseek.com/v1","model":"deepseek-flash","credentials_file":"config.json"},
  "tuning":{"rounds":4,"compare_original":true},
  "device":{"adapter":"disabled","write_enabled":false}
}
```

Provider=openai names a compatible API protocol, including DeepSeek; the service must support the model. Save and run:

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.json
```

Check the new report's LLM history. Requested/applied PID entries show usable suggestions; llm_unavailable/schema events show failure. A report or enabled switch does not prove API success; failures can retain prior best parameters. Three arms × four rounds allow up to 12 suggestions, plus possible retries. A key alone does not enable LLMs; editing configuration does not update old reports. Simulation is local, LLM calls are online.

The process environment variable LLM_API_KEY may replace the file and takes precedence. The Chinese guide provides PowerShell instructions.

## 3. Use without LLM

After basic installation, start with the included in-memory simulated device preset, which disables LLMs:

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/use_simulated.json --validate
.\.venv\Scripts\python.exe pid_project.py --config examples/use_simulated.json
```

Inspect `results/use_simulated/<timestamp>/report.html` and `device_audit.json`. For TCP serialization open two terminals in the repository root. Terminal 1:

```powershell
.\.venv\Scripts\python.exe -m thermal_pid.gateway_demo --config examples/use_tcp_local.json --port 9100
```

Terminal 2:

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/use_tcp_local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config examples/use_tcp_local.json
```

Inspect `results/use_tcp_local/<timestamp>/`; stop the gateway with Ctrl+C. This controls simulated hardware only.

For your actual device, implement and validate the [gateway contract](docs/DEVICE_PROTOCOL.md). An IP address does not implement a vendor protocol. Copy root configuration once:

```powershell
Copy-Item -LiteralPath project.json -Destination project.use.local.json
```

If the destination exists, edit it rather than overwriting device settings. Set mode=use, llm.enabled=false, device.write_enabled=true and matching object_id. TCP needs adapter=tcp and actual host/port/timeout. Serial needs `.[serial]`, adapter=serial, serial_port/baud and JSONL. Custom needs a module:function factory and its actual vendor dependencies. Provide verified model/history, task, units/bounds/rate, sample time, PID representation/filter/initialization and criteria matching the device.

First validate and simulate without connecting hardware:

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test
```

After reviewing the result and validating the interface, run use mode:

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json
```

The final command may write **PID and setpoint**. Actual state and device-relative limits can change test recommendations. No qualified candidate or incompatible state prevents writing. Check simulation status and device_audit.json separately; monitoring is bounded, not permanent.

## 4. Use with LLM

Install `.[llm]` or `.[llm,serial]`, set root credentials as in section 2, and complete device adaptation as in section 3. In root project.use.local.json enable LLMs, specify service/model/provider and credentials_file=config.json.

```powershell
# Offline first; does not connect the device
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test
# After reviewing the report and validating the interface
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json
```

Proposals pass numeric/absolute bounds, incremental checks and full simulation. Final changes are checked against actual starting device PID; state is rechecked after planning. For simulated use+LLM, copy root project.json to project.use-sim-llm.local.json, set mode=use, llm.enabled=true, credentials_file=config.json, adapter=simulated, write_enabled=true, max_planning_output_change=20 and service settings above. Validate/run that file with --config. The [Chinese guide](README.md) provides full commands.

## Models and batch tests

Known models use source=parameters; CSV uses source=csv, history.file, columns/time units and a single input step with baseline/settled tail. Probe only steps local models. Included history/heating/cooling/custom_model presets disable LLMs by default; history data are synthetic.

Paths resolve against the configuration directory: credentials are config.json for root files, ../config.json for examples/, ../../config.json for examples/scenarios/. Adjust paths when moving configurations.

Six annotated scenarios default to LLM enabled. Basic installation is enough for the first command; the second also needs LLM extras, credentials and service setup:

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --llm off
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --llm on
```

These flags belong to the batch script, not pid_project.py. Six scenes × three arms × four rounds allow up to 72 suggestions, plus retries. Reports live under results/scenarios/<name>/<timestamp>/; batch index.html, comparison.md/CSV and suite.json under results/comparison_suite/<timestamp>/. Failed qualification remains visible.

## Results, guardrails and troubleshooting

Every run creates a unique timestamp folder beneath output.directory. Open the current printed path, not an older results/project/report.html. Reports show curves, metrics, candidates, qualification and LLM history. PID JSON has explicit form/units or null. Summary JSON records config/time/hash and full guard/rollback history. CSVs record metrics/trajectories; use adds the device audit.

From 0.4.1, original_zn is an untuned formula reference; legacy_route runs the original continuous tuning core on a common adapted plant/controller. Z-N PID, Z-N PI and SIMC PI each refine independently. selected is the final cross-route winner alias, not another LLM call. All proposals share full-task evaluation and delivery checks; exact ties retain the legacy baseline. Reports/history expose provenance, reasons, objectives and counts. Old records with no provenance are excluded from statistics. tuning.selection_priority supports accuracy/smooth/speed; include_legacy_route defaults to true; compare_original only controls the formula reference. With LLM off, the legacy route is guarded initialization only. The guarantee concerns this configured simulation/objective, not every metric or real hardware.

Default guardrail_policy=auto retains numeric/absolute bounds for offline formulas/final suggestions, incremental LLM checks and device-relative/final-jump checks in use. Relative adds incremental checks to offline initialization. Any violation rejects the complete proposal without clipping; other candidates continue. Complete simulation qualification remains mandatory; configured bounds are not derived physical safety limits. See [guardrail notes](docs/GUARDRAILS.md). Output TV is cumulative output changes, not energy.

Missing interpreter: create .venv in the right directory. Missing API modules: install using that interpreter. No LLM history: check enabled/config/save/current report. Valid config with API failure: inspect service/model/key/network/history. Null PID: inspect failed criteria. Different use/test gains: actual device state and relative checks affect planning.

The checked-in [result/](result/) snapshot uses old relative policy, LLM off and Z-N PI; auto may select Z-N PID. Download to open HTML; GitHub shows source. Do not combine different configurations as one experiment.

## Development and attribution

This guide concerns pid_project.py; legacy entries keep their own configuration. IMC is SIMC background only. Strong coupling, unstable or strongly nonlinear plants require further models/algorithms.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm,serial,legacy]" --index-url https://pypi.org/simple
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m build
```

Tests do not call external LLMs or real hardware. `results/`, `config.json`, `.env` and `*.local.json` are ignored. Keep credentials and production data private. Retain [LICENSE](LICENSE), [NOTICE](NOTICE) and upstream attribution; see [CHANGELOG](CHANGELOG.md) and [CONTRIBUTING](CONTRIBUTING.md).

Version 0.4.2 also evaluates an explicitly labeled hybrid route: corrected Z-N initialization followed by the retained original continuous tuning engine. It can be disabled with `tuning.include_corrected_legacy_route=false` and is skipped when LLM is off. Route provenance and history distinguish legacy, pure new and hybrid selection; hybrid gains must not be attributed solely to the new LLM strategy. The reproducible configurations are in `examples/route_benchmarks`.

### Rejection and retry budgets

`tuning.rounds` bounds valid proposal simulations on each new route. Rejections do not consume these rounds. `max_guardrail_retries_per_round` defaults to 2 retries after the first rejection; `max_llm_requests_per_route` defaults to 12 suggestion-interface calls, including retries. Transport retries are separately bounded by `llm.max_attempts`; these limits are not token or billing caps.

Reports distinguish `ACCEPTED`, `REJECTED_GUARDRAIL` (no simulation), `FAILED_SIMULATION`, `FAILED_EVALUATION` and `PASSED`. A rejected algorithm has no performance score and is not necessarily mathematically wrong. Runs with no available candidates still retain diagnostic records and null PID. Legacy tuning routes also use the current rejection guardrails; they are not an independent execution of unmodified upstream code.
