import tempfile
import unittest
from pathlib import Path

from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.plan import StepCall, TestPlan


class StepMetadataTests(unittest.TestCase):
    def test_display_name_and_optional_description_round_trip(self):
        plan = TestPlan(name="Recorded", steps=(
            StepCall("step-001", "gui.object.action", name="Open File menu",
                     description="Choose the export command", group="Recorded session"),
            StepCall("step-002", "gui.object.action", name="Choose format"),
        ))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recorded.yaml"
            save_plan(plan, path)
            loaded = load_plan(path)
        self.assertEqual(loaded.steps, plan.steps)
        self.assertEqual(loaded.steps[0].node_id, "step-001")
        self.assertEqual(loaded.steps[1].description, "")


if __name__ == "__main__":
    unittest.main()
