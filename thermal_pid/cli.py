"""Public entry point for the workbench and every upstream entry point."""

import argparse
import importlib
import importlib.util
import sys

ROUTES = {
    "project": ("pid_project", "Configurable offline planning and device integration"),
    "simulate": ("simulator", "Original Python simulator/TUI and configured Simulink backend"),
    "hardware": ("tuner", "Original serial CSV hardware tuner"),
    "identify": ("system_id", "Original file/live/demo system-identification entry"),
    "doctor": ("doctor", "Original environment/API/serial diagnostics"),
    "upstream": ("launcher", "Original interactive launcher, without changing its arguments"),
    "desktop": ("thermal_pid.desktop", "Visual desktop workbench"),
}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--help", "-h", "commands"):
        parser = argparse.ArgumentParser(
            description="Thermal PID Workbench: visual, configurable and upstream workflows",
            epilog="Without a command, options are passed to pid_project.py. Use COMMAND --help for details.",
        )
        parser.add_argument("command", nargs="?", choices=sorted(ROUTES))
        parser.print_help()
        for name, (_, description) in ROUTES.items():
            print(f"  {name:10} {description}")
        return 0
    command = args.pop(0) if args and args[0] in ROUTES else "project"
    if command in ("simulate", "hardware", "doctor", "upstream"):
        missing = [name for name in ("requests", "serial", "textual") if importlib.util.find_spec(name) is None]
        if missing:
            print("This workflow needs the [full] dependencies; see README installation.", file=sys.stderr)
            return 1
    if command == "doctor" and args:
        if args == ["--help"]:
            print("thermal-pid doctor: run original environment/API/serial diagnostics using config.json")
            return 0
        raise SystemExit("doctor does not accept additional arguments")
    entry = importlib.import_module(ROUTES[command][0])
    result = entry.main() if command == "doctor" else entry.main(args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
