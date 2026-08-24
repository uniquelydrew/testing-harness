import tkinter as tk

from automation_harness.authoring.app import AuthoringApp


def test_authoring_gui_constructs_on_reference_display():
    root = tk.Tk()
    try:
        app = AuthoringApp(root)
        root.update_idletasks()
        root.update()
        assert app.plan.name == "new-test-plan"
        assert app.step_tree.get_children()
        assert app.object_tree.get_children()
    finally:
        root.destroy()
