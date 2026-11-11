"""Exercise the material request's serial entry behavior in the shipped script."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_issue_quantity_controls_serial_field_count_per_line() -> None:
    node_path = shutil.which("node")
    if node_path is None:
        pytest.skip("Node.js is required for the inventory UI script test")

    template = (
        Path(__file__).resolve().parents[3]
        / "templates/inventory/material_request_detail.html"
    ).read_text(encoding="utf-8")
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    probe = """
const router = materialIssueLine('2', ['RTR-1', 'RTR-2', 'RTR-3'], 1, true, false);
const before = [...router.serials];
router.serials[0] = 'RTR-2-edited';
router.issueQty = '1'; router.syncSerials();
const retained = [...router.serials];
router.issueQty = '2'; router.syncSerials();
const second = materialIssueLine('1', [], 0, true, false);
const cable = materialIssueLine('5', [], 0, false, false);
console.log(JSON.stringify({before, retained, router: router.serials,
  second: second.serials.length, cable: cable.serials.length}));
"""
    result = subprocess.run(  # noqa: S603 - executes the repository's own UI script
        [node_path, "-e", script + probe],
        capture_output=True,
        text=True,
        check=True,
    )
    state = json.loads(result.stdout)
    assert state["before"] == ["RTR-2", "RTR-3"]
    assert state["retained"] == ["RTR-2-edited"]
    assert state["router"][0] == "RTR-2-edited"
    assert state["second"] == 1
    assert state["cable"] == 0
