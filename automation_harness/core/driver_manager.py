from __future__ import annotations

from dataclasses import dataclass

from automation_harness.core.test_context import TestContext
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.tracking_driver import TrackingDriver
from automation_harness.drivers.vision_driver import VisionDriver
from automation_harness.drivers.anchored_visual import AnchoredVisualDriver


@dataclass(frozen=True)
class DriverManager:
    """Typed driver composition; deliberately not a string-keyed service locator."""

    tracking: TrackingDriver
    vision: VisionDriver
    atspi: AtspiDriver
    java_accessibility: JavaAccessibilityDriver
    anchored_visual: AnchoredVisualDriver

    @classmethod
    def from_context(cls, context: TestContext) -> "DriverManager":
        return cls(
            tracking=TrackingDriver(context),
            vision=VisionDriver(context),
            atspi=AtspiDriver(context),
            java_accessibility=JavaAccessibilityDriver(context),
            anchored_visual=AnchoredVisualDriver(context),
        )
