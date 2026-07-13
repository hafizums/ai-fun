"""Mirror Gate 8 workflow mapping expectations in Python tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_JS = ROOT / "app" / "web" / "workflow.js"
APP_JS = ROOT / "app" / "web" / "app.js"


def test_workflow_js_syntax() -> None:
    completed = subprocess.run(
        ["node", "--check", str(WORKFLOW_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_app_js_syntax() -> None:
    completed = subprocess.run(
        ["node", "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_workflow_unit_suite() -> None:
    completed = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "workflow.test.mjs")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ok" in completed.stdout


def test_no_automatic_paid_chaining_markers() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert "ensurePaidConfirm" in text
    assert "This starts a paid AI generation request" in text
    assert "Local processing — no provider charge" in text
    assert "for (const stage of paidStages)" not in text
    assert "autoStartPipeline" not in text


def test_409_refresh_not_post_retry() -> None:
    text = APP_JS.read_text(encoding="utf-8")
    assert "handleConflictAndRefresh" in text
    assert "status === 409" in text
