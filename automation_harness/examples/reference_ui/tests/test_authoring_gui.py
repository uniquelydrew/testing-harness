import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.app import AuthoringApp


def test_authoring_gui_constructs_on_reference_display():
    app = AuthoringApp()
    try:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        assert app.plan.name == "new-test-plan"
        assert len(app.step_store) > 0
        assert len(app.object_store) > 0
    finally:
        app.window.destroy()
