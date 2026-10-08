"""Minimal custom model hook. Replace this factory with a validated process model."""

from thermal_pid.models import FOPDTModel


def create_model(config):
    # The returned object exposes .temperature and step(output, dt_s).
    # Each factory call must create a fresh, deterministic initial state.
    return FOPDTModel(config)
