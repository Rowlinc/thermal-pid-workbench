"""Windows CLI smoke tests with redirected, strict Western console encoding."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from thermal_pid.config import DEFAULTS


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console encoding regression")
@pytest.mark.parametrize("batch", [False, True])
def test_offline_cli_handles_chinese_paths_and_output_with_cp1252(tmp_path, batch):
    workspace = tmp_path / "温控实验"
    workspace.mkdir()
    cfg = deepcopy(DEFAULTS)
    cfg["output"]["directory"] = "runs"
    (workspace / "project.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    command = [sys.executable, str(ROOT / "pid_project.py")]
    if batch:
        command = [sys.executable, str(ROOT / "scripts/run_comparison_suite.py"),
                   str(workspace / "project.json"), "--llm", "off"]
    environment = dict(os.environ, PYTHONIOENCODING="cp1252:strict", PYTHONUTF8="0",
                       PYTHONCOERCECLOCALE="0")
    completed = subprocess.run(command, cwd=workspace, env=environment,
                               capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    output = completed.stdout.decode("utf-8")
    assert "温控实验" in output
    if batch:
        assert "Summary:" in output
    else:
        assert "Task (温度): 30 -> 100 ℃" in output
        assert "Final route:" in output
    assert list((workspace / "runs").glob("*/pid.json"))
