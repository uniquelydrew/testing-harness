from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from automation_harness.authoring.gui.repository_window import ObjectRepositoryWindow
from automation_harness.authoring.gui.repository_workbench_window import WorkbenchObjectRepositoryWindow


class RhelWorkbenchObjectRepositoryWindow(WorkbenchObjectRepositoryWindow):
    """RHEL/PyGObject-safe Object Repository workbench window.

    ``ObjectRepositoryWindow.__init__`` calls ``self.refresh()`` before the
    Workbench subclass installs its hierarchical TreeStore.  Without this
    guard, virtual dispatch runs the hierarchical refresh against the base
    four-column ListStore.  The resulting out-of-range column lookup manifests
    on older PyGObject as the misleading ``gobject.GType`` TypeError.
    """

    def __init__(self, *args, **kwargs):
        self._hierarchical_model_ready = False
        super().__init__(*args, **kwargs)

    def selected(self, tree, column=0):
        if not self._hierarchical_model_ready:
            # ArtifactWindow.selected/ObjectRepositoryWindow.selected is a
            # static method.  Do not inject ``self`` here; older PyGObject
            # deployments exposed this as the observed 3-positional-argument
            # failure while the base repository model was still active.
            return ObjectRepositoryWindow.selected(tree, column)
        return super().selected(tree, column)

    def refresh(self):
        if not self._hierarchical_model_ready:
            return ObjectRepositoryWindow.refresh(self)
        return super().refresh()

    def _install_hierarchical_tree(self):
        self.store = Gtk.TreeStore(
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
        )
        self.tree.set_model(self.store)
        columns = self.tree.get_columns()
        if columns:
            columns[0].set_title("Object")
        if len(columns) > 2:
            columns[2].set_title("Resolvers")
        self._hierarchical_model_ready = True
