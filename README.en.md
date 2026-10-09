# Thermal PID Workbench

Configurable thermal PID identification, tuning, evaluation and gateway integration,
derived from [KINGSTON-115/llm-pid-tuner](https://github.com/KINGSTON-115/llm-pid-tuner), Apache-2.0.

[中文](README.md) · [Configuration](docs/CONFIGURATION.md) · [Methods](docs/METHODS.md) · [Gateway protocol](docs/DEVICE_PROTOCOL.md)

Use Python 3.10+. The default offline experiment needs only the standard library:

```sh
python pid_project.py
```

Edit `project.json` to change the plant, history CSV, thermal task, actuator, controller,
criteria and optional LLM. Each field has Chinese comments explaining meaning, units,
choices and defaults. The loader supports these comments as well as plain JSON.
Omitted fields retain defaults; unknown fields fail validation.
Relative paths resolve against the configuration directory. The default task is 30°C to 100°C.
Every run creates a distinct `results/project/<timestamp>/` folder and prints its report path. Open its `report.html`; `pid.json` contains qualified parameters with explicit units,
or null if no candidate meets all requirements. `summary.json` records reproducibility information.

A checked-in 30°C → 100°C offline example is available in [`result/`](result/):
[`result.html`](result/result.html) contains curves and metrics, and
[`result-toread.md`](result/result-toread.md) provides a Chinese walkthrough.
Read the walkthrough on GitHub, then download or clone the repository and open the HTML
in a browser. The example uses no LLM or hardware; new runs write to separate `results/project/<timestamp>/` folders without overwriting history.

```sh
python -m pip install -e .
thermal-pid --init my_project.json
thermal-pid --config my_project.json --validate
thermal-pid --config my_project.json
```

- `test`: identify, guard Z-N/SIMC candidates, optionally refine using an LLM, simulate and report.
- `use`: additionally validate device state, conditionally apply qualified parameters, read back and monitor for a bounded duration.

Try `examples/use_simulated.json` without hardware. TCP/serial use a documented JSON-line gateway
protocol; custom factories can implement vendor interfaces. The existing device owns the continuous
control loop. Software tests do not constitute physical plant commissioning. Independent DCS/PLC/SIS
protections remain responsible for process safety.

Models include known FOPDT parameters, a single-step historical CSV, a two-node heater and custom
factories. Output limits, slew rates, sample time, anti-windup, cooling direction and parallel/ideal
PID conversion are explicit. IMC is background theory for SIMC, not a separate algorithm.

`original_zn`, `corrected_zn` and `selected` isolate identification/initialization changes under common
conditions. They do not compare every detail of the complete upstream program. All proposals pass
`pid_safety.py`; best parameters are retained. The final jump from starting gains is checked again.

Enable LLMs by installing `.[llm]`, setting `LLM_API_KEY` or local `config.json`, and running
`python pid_project.py --config examples/deepseek.json`. The provider receives model and simulation
summaries. Do not publish credentials or production data.

For development install `.[dev,llm,serial,legacy]` and run `python -m pytest tests -q`.
Legacy entry points keep their own configuration. See [NOTICE](NOTICE), [CHANGELOG](CHANGELOG.md)
and [CONTRIBUTING](CONTRIBUTING.md).

## Run history and scenario comparisons

The existing `result/` reference snapshot uses the previous relative-increase policy. With the new default `auto` policy, the default task may select Z-N PID instead of that snapshot's Z-N PI. Compare the configuration and effective guard policy recorded in each report; see [guardrail revision notes](docs/GUARDRAILS.md).

Each run reserves a unique timestamp subfolder beneath `output.directory`; old reports are preserved. The CLI prints the folder at startup and the report path at completion. Six annotated configurations in `examples/scenarios/` cover different setpoints, longer lag/delay and a persistent cooling disturbance. They enable LLM calls using the private root `config.json` key.

The default `controller.guardrail_policy=auto` applies numeric and absolute gain bounds to initial analytical candidates in test mode, without clipping formulas relative to an arbitrary default PID. LLM rounds still have incremental increase checks. Use mode retains device-relative checks, including the final change from the device's actual starting PID. Set `relative` to reproduce the previous policy. All candidates still require full-task simulation validation; reports record the effective policy.

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py
```

Use `--llm off` for deterministic simulations without API calls. The suite writes `index.html`, `comparison.md`, `comparison.csv` and `suite.json` into a unique `results/comparison_suite/<timestamp>/` folder, including unsuccessful scenarios. The original-identification arm is a baseline in the current unified framework, rather than an independent execution of the complete upstream project.
