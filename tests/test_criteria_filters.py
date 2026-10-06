from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_filter_rules():
    node = shutil.which("node")
    if not node:
        pytest.skip("필터의 JavaScript 규칙 검사에는 Node.js가 필요합니다.")
    result = subprocess.run([node, "--test", str(Path(__file__).with_name("criteria_filters.test.mjs"))],
        capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
