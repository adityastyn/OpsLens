"""The site setting that treats the bundled plant SOPs as approved."""
import os
import unittest
from unittest.mock import patch

import sop_approval


class SopApproval(unittest.TestCase):
    def draft(self, sop_id="SOP-LFT-001"):
        return {"sop_id": sop_id, "approved": False, "approval_status": "Draft for review — NOT APPROVED",
                "document_status": "Revision 0 — Draft for review. REFERENCE ONLY — NOT APPROVED.",
                "revision_label": "0 (Draft for review)", "status": "DRAFT / NOT APPROVED", "manual_review_required": True}

    def test_plant_sops_are_approved_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPSLENS_PLANT_SOPS_APPROVED", None)
            doc = sop_approval.apply(self.draft())
        self.assertTrue(doc["approved"])
        self.assertEqual(doc["approval_status"], "approved")
        self.assertTrue(doc["approval_reference"])
        self.assertEqual(doc["status"], "APPROVED")
        self.assertEqual(doc["revision_label"], "0")
        self.assertFalse(doc["manual_review_required"])
        self.assertNotRegex(doc["document_status"], "(?i)draft|not approved")

    def test_setting_off_keeps_the_recorded_draft_status(self):
        with patch.dict(os.environ, {"OPSLENS_PLANT_SOPS_APPROVED": "false"}):
            doc = sop_approval.apply(self.draft())
            self.assertFalse(sop_approval.approved("SOP-LFT-001"))
        self.assertEqual(doc, self.draft())

    def test_uploaded_or_unknown_sops_are_never_changed(self):
        with patch.dict(os.environ, {"OPSLENS_PLANT_SOPS_APPROVED": "true"}):
            self.assertEqual(sop_approval.apply(self.draft("SOP-UPLOADED-77")), self.draft("SOP-UPLOADED-77"))
            self.assertFalse(sop_approval.approved("SOP-UPLOADED-77"))
            self.assertTrue(sop_approval.approved("SOP-UPLOADED-77", True))  # its own recorded approval still counts
            self.assertFalse(sop_approval.approved(None))

    def test_every_scenario_sop_and_the_handover_sop_are_plant_sops(self):
        import incident_catalog
        for spec in incident_catalog.SCENARIOS:
            if spec.sop_id:
                self.assertTrue(sop_approval.is_plant_sop(spec.sop_id), spec.sop_id)
        self.assertTrue(sop_approval.is_plant_sop("SOP-GEN-003"))


if __name__ == "__main__":
    unittest.main()
