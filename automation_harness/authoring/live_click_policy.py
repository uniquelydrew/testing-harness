from __future__ import annotations

"""Post-install authoring click policy.

The live authoring layer resolves and highlights objects independently of their
accessible action interface. Click must use the same resolved geometry instead
of being an alias for accessibility ``activate``.
"""

import json
import threading

from automation_harness.core.pointer_actions import click_bounds


def install(app_module) -> None:
    from automation_harness.authoring import object_identity_workbench as workbench

    GLib = app_module.GLib

    def finish_click(self, component_id, result=None, error=None):
        if error is not None:
            self._set_status("Click failed")
            self._error("Click failed", "%s: %s" % (type(error).__name__, error))
            return False
        self._set_status("Clicked %s" % component_id)
        self._set_text(
            self.object_detail,
            json.dumps(
                {"component_id": component_id, "click": result},
                indent=2,
                default=str,
            ),
        )
        return False

    def click_selected_worker(self, definition):
        errors = []
        for strategy in definition.strategies:
            try:
                if strategy.type == "anchored_visual":
                    captured = self.capture.resolve_anchored_visual(strategy.options)
                elif strategy.type in {"atspi", "java_accessibility"}:
                    captured = self.capture.capture_by_locator(
                        identification=strategy.options.get("identification")
                    )
                elif strategy.type == "javafx":
                    captured = self.capture.javafx_driver.inspect(
                        identification=strategy.options.get("identification")
                    )
                else:
                    continue
                if captured.bounds is None:
                    raise LookupError("resolved object has no screen bounds")
                result = click_bounds(captured.bounds, "click")
            except Exception as exc:
                errors.append(
                    "%s: %s: %s" % (strategy.type, type(exc).__name__, exc)
                )
                continue
            GLib.idle_add(
                self._finish_selected_click,
                definition.component_id,
                result,
                None,
            )
            return
        GLib.idle_add(
            self._finish_selected_click,
            definition.component_id,
            None,
            RuntimeError("; ".join(errors) or "no bounds-resolvable strategy"),
        )

    def click_selected_object(self):
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Click", "Select a captured object first.")
        definition = self.repository.get(component_id)
        self._set_status("Clicking %s…" % component_id)
        threading.Thread(
            target=self._click_selected_worker,
            args=(definition,),
            name="automation-author-pointer-click",
            daemon=True,
        ).start()

    def finish_workbench_click(self, result=None, error=None):
        if error is not None:
            self._set_status("Click failed")
            self.app._error("Click failed", "%s: %s" % (type(error).__name__, error))
            return False
        self._set_status("Clicked selected object")
        self.app._set_text(
            self.app.object_detail,
            json.dumps({"click": result}, indent=2, default=str),
        )
        return False

    def workbench_click_selected(self):
        node = self._selected_node()
        if node is None:
            return
        bounds = node.payload.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            return self.app._error(
                "Click",
                "The selected object has no screen bounds to click.",
            )
        self._set_status("Clicking selected object…")

        def worker():
            try:
                result = click_bounds(bounds, "click")
            except Exception as exc:
                workbench.GLib.idle_add(self._finish_workbench_click, None, exc)
                return
            workbench.GLib.idle_add(self._finish_workbench_click, result, None)

        threading.Thread(
            target=worker,
            name="automation-workbench-pointer-click",
            daemon=True,
        ).start()

    app_module.AuthoringApp.click_selected_object = click_selected_object
    app_module.AuthoringApp._click_selected_worker = click_selected_worker
    app_module.AuthoringApp._finish_selected_click = finish_click
    workbench.ObjectIdentityWorkbench.click_selected = workbench_click_selected
    workbench.ObjectIdentityWorkbench._finish_workbench_click = finish_workbench_click
