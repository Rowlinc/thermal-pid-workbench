# Contributing

Install Python 3.10 or newer, then `python -m pip install -e ".[dev,llm,serial,legacy]"`.
Run `python -m pytest tests -q` from a clean checkout without local credentials.

For thermal workflow changes, cover a meaningful physical behavior or failure condition:
unit/time conversion, object identification, identical evaluation conditions, actuator limits,
rejection of invalid proposals, device readback and ownership-aware rollback.
Tests must not contact an external LLM or a physical device. TCP tests bind loopback only.

Keep the thermal project configuration strict: new fields require defaults, validation,
schema and user documentation. Regenerate defaults/schema using
`python scripts/generate_project_assets.py` after changing the schema.
Use the shared `pid_safety.py` functions for parameter constraints.

Do not commit local credentials, historical production CSVs or generated device logs.
Report comparison conditions and failures alongside successes. A single simulated result
does not establish superiority across chemical plants. Preserve upstream attribution.
