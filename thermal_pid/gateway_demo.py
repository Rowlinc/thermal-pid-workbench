"""Loopback-only reference gateway backed by a simulated device, for integration."""

import argparse
import json
import socketserver
from .config import load_project
from .devices import SimulatedDevice


def main():
    parser = argparse.ArgumentParser(
        description="Simulated JSONL gateway; never connects to hardware"
    )
    parser.add_argument("--config")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    cfg, _ = load_project(args.config, mode="test")
    device = SimulatedDevice(cfg)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(cfg["device"]["timeout_s"])
            while True:
                raw = self.rfile.readline(65537)
                if not raw or len(raw) > 65536 or not raw.endswith(b"\n"):
                    break
                request = {}
                try:
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise ValueError()
                    if (
                        request.get("protocol_version") != 1
                        or request.get("object_id") != cfg["device"]["object_id"]
                    ):
                        raise ValueError()
                    operation = request.get("operation")
                    if operation == "read_state":
                        payload = {"state": device.read_state()}
                    elif operation == "apply_parameters":
                        if request.get("initialization") != cfg["controller"]["initialization"]:
                            raise ValueError()
                        payload = device.apply(
                            request["pid"], request["setpoint_c"], request["expected_revision"]
                        )
                    else:
                        raise ValueError()
                    response = {"ok": True, **payload}
                except Exception:
                    response = {"ok": False, "error": "invalid_request_or_revision_conflict"}
                response.update(
                    protocol_version=1,
                    request_id=request.get("request_id") if isinstance(request, dict) else None,
                )
                self.wfile.write((json.dumps(response, allow_nan=False) + "\n").encode())
                self.wfile.flush()

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", args.port), Handler) as server:
        print(f"SIMULATED gateway on 127.0.0.1:{args.port}; Ctrl+C to stop", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
