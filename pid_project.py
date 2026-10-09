"""Unified configurable entry point; legacy simulator commands remain available."""

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
from thermal_pid.config import ConfigError, load_project, validate, write_defaults
from thermal_pid.workflow import plan
from thermal_pid.report import write_report


def create_run_directory(root):
    """Reserve a distinct folder, even when runs share the same clock timestamp."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f%z")
    index = 0
    while True:
        directory = root / (stamp if index == 0 else f"{stamp}_{index}")
        try:
            directory.mkdir(exist_ok=False)
            return directory
        except FileExistsError:
            index += 1


def run(cfg, base, cancelled=None, progress=None):
    from thermal_pid.process import checkpoint
    checkpoint(cancelled)
    validate(cfg)
    device = None
    audit = []
    result = None
    directory = create_run_directory((Path(base) / cfg["output"]["directory"]).resolve())
    print(f"Run directory: {directory}", flush=True)
    identified = None
    try:
        if cfg["mode"] == "use":
            from thermal_pid.devices import connect, validate_state
            from thermal_pid.control import import_pid
            from thermal_pid.models import identify

            cfg, identification, data = identify(cfg, base)
            identified = (identification, data)
            validate(cfg)
            device = connect(cfg)
            before = validate_state(cfg, device.read_state())
            audit.append({"event": "initial_state", "state": before})
            cfg = deepcopy(cfg)
            cfg["controller"]["initial_pid"] = import_pid(cfg, before["pid"])
            for key, value in cfg["controller"]["initial_pid"].items():
                bounds = cfg["controller"]["limits"][key]
                if not bounds["min"] <= value <= bounds["max"]:
                    raise ConfigError(
                        "current device PID is outside configured limits; reconcile device configuration"
                    )
            cfg["task"]["initial_temperature_c"] = before["temperature_c"]
            cfg["task"]["initial_output"] = before["output"]
            validate(cfg)
        result = plan(cfg, base, identified=identified, cancelled=cancelled, progress=progress)
        result["device_status"] = "pending" if device is not None else "not_connected_test_mode"
        write_report(result, directory, cfg["output"]["save_csv"])
        if device is not None:
            checkpoint(cancelled)
            from thermal_pid.devices import deploy, DeviceError

            if result["recommended"] is None:
                raise DeviceError("no qualified recommendation; device write refused")
            deploy(result["config"], device, before, result["recommended"], audit, cancelled=cancelled)
            result["device_status"] = (
                "completed_simulated"
                if cfg["device"]["adapter"] == "simulated"
                else "completed_bounded_monitoring"
            )
            write_report(result, directory, cfg["output"]["save_csv"])
        return result, directory
    except BaseException as exc:
        if device is not None:
            if not any(e["event"] == "fault" for e in audit):
                audit.append({"event": "fault", "error": type(exc).__name__})
            if result is not None:
                result["device_status"] = "failed_check_device_audit"
                write_report(result, directory, cfg["output"]["save_csv"])
        raise
    finally:
        if device is not None:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "device_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            device.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Thermal PID Workbench: one editable project configuration"
    )
    parser.add_argument("--config", help="project JSON; defaults to project.json")
    parser.add_argument("--mode", choices=["test", "use"], help="override configured mode")
    parser.add_argument(
        "--init", metavar="PATH", help="write default project configuration and exit"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate configuration without simulation or device connection",
    )
    args = parser.parse_args(argv)
    try:
        if args.init:
            write_defaults(args.init)
            print(f"Created {args.init}")
            return 0
        cfg, base = load_project(args.config, args.mode)
        if args.validate:
            print("Configuration valid; no device connection")
            return 0
        result, directory = run(cfg, base)
        print(f"Task ({cfg['process']['name']}): {cfg['task']['initial_temperature_c']:g} -> {cfg['task']['target_temperature_c']:g} {cfg['process']['unit']}")
        print(
            f"Selected initialization: {result['selected_method']}; {result['recommendation_status']}"
        )
        for arm in result["arms"]:
            m = arm["final"]["metrics"]
            print(
                f"{arm['name']:14} overshoot={m['overshoot_pct']:.3f}% IAE={m['iae_c_s']:.3f} tail_MAE={m['tail_mae_c']:.5f} settling={m['settling_time_s']} TV={m['output_tv']:.3f} eligible={m['eligible']}"
            )
        print(f'Report: {directory / "report.html"}')
        return 0 if result["recommended"] is not None else 2
    except (ConfigError, RuntimeError, OSError, ValueError, ImportError) as exc:
        # Client errors can contain API credentials; never expose their text.
        print(
            f'Failed: {str(exc) if isinstance(exc,ConfigError) or type(exc).__name__=="DeviceError" else type(exc).__name__}',
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
