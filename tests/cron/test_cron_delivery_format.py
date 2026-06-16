"""Tests for user-facing cron delivery formatting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cron.scheduler import _format_cron_delivery, _format_delivery_excerpt


def test_cron_delivery_strips_legacy_wrapper_and_management_boilerplate():
    job = {
        "id": "b5577bf6051d",
        "name": "ecom-green-sites-hourly",
        "schedule_display": "every 60m",
        "script": "ecom-green-sites-hourly.sh",
        "prompt": "Hourly Ecom green-site validation watchdog.",
    }
    content = """Cronjob Response: ecom-green-sites-hourly
(job_id: b5577bf6051d)
-------------

ecom-green-sites-hourly: skipped.
• Reason: previous run still active
• Proof: /Users/ecom/repos/ecom/outputs/cron/ecom-green-sites-hourly/20260616T222032Z

To stop or manage this job, send me a new message.
"""

    formatted = _format_cron_delivery(job, content)

    assert formatted.startswith("ecom-green-sites-hourly: skipped.")
    assert "• Job: ecom-green-sites-hourly (`b5577bf6051d`)" in formatted
    assert "• What: Hourly Ecom green-site validation watchdog." in formatted
    assert "• Schedule: every 60m" in formatted
    assert "• Script: `ecom-green-sites-hourly.sh`" in formatted
    assert "• Reason: previous run still active" in formatted
    assert "• Proof: /Users/ecom/repos/ecom/outputs/cron/ecom-green-sites-hourly/20260616T222032Z" in formatted
    assert "Cronjob Response" not in formatted
    assert "job_id:" not in formatted
    assert "To stop or manage" not in formatted


def test_cron_delivery_formats_json_stdout_as_bullets():
    excerpt = _format_delivery_excerpt('{"status":"failed","error":"boom","artifact_path":"/tmp/run"}')

    assert "{" not in excerpt
    assert "• Status: failed" in excerpt
    assert "• Error: boom" in excerpt
    assert "• Artifact path: /tmp/run" in excerpt


def test_successful_cron_delivery_keeps_short_identity_without_extra_description():
    job = {
        "id": "abc123",
        "name": "daily-summary",
        "schedule_display": "0 9 * * *",
        "prompt": "Long internal prompt that should only appear on failure.",
    }

    formatted = _format_cron_delivery(job, "done")

    assert formatted.startswith("daily-summary: completed.")
    assert "• Job: daily-summary (`abc123`)" in formatted
    assert "• What:" not in formatted
    assert "done" in formatted
