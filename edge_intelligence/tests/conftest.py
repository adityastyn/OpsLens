"""Test-wide settings.

The suite checks the recorded SOP approval status (drafts stay drafts), so the
site setting that treats the bundled plant SOPs as approved is off by default
here. Tests of that setting switch it on themselves (test_sop_approval.py,
test_plan_sources.py)."""
import os

os.environ.setdefault("OPSLENS_PLANT_SOPS_APPROVED", "false")
