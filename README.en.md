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
Open `results/project/report.html`; `pid.json` contains qualified parameters with explicit units,
or null if no candidate meets all requirements. `summary.json` records reproducibility information.

A checked-in 30°C → 100°C offline example is available in [`result/`](result/):
[`result.html`](result/result.html) contains curves and metrics, and
[`result-toread.md`](result/result-toread.md) provides a Chinese walkthrough.
Read the walkthrough on GitHub, then download or clone the repository and open the HTML
in a browser. The example uses no LLM or hardware; new runs still write to `results/project/`.

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
