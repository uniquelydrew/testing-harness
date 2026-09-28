package automation.harness.agent;

import java.awt.AWTEvent;
import java.awt.Component;
import java.awt.Container;
import java.awt.EventQueue;
import java.awt.Point;
import java.awt.Rectangle;
import java.awt.Toolkit;
import java.awt.Window;
import java.awt.event.AWTEventListener;
import java.awt.event.MouseEvent;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.swing.AbstractButton;
import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JCheckBoxMenuItem;
import javax.swing.JComboBox;
import javax.swing.JLabel;
import javax.swing.JList;
import javax.swing.JMenu;
import javax.swing.JMenuBar;
import javax.swing.JMenuItem;
import javax.swing.JPasswordField;
import javax.swing.JPopupMenu;
import javax.swing.JRadioButton;
import javax.swing.JRadioButtonMenuItem;
import javax.swing.JSeparator;
import javax.swing.JSlider;
import javax.swing.JSpinner;
import javax.swing.JTable;
import javax.swing.JTabbedPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.JToggleButton;
import javax.swing.JToolBar;
import javax.swing.JTree;
import javax.swing.SwingUtilities;
import javax.swing.text.JTextComponent;

/** Java 8 native Swing/AWT discovery, including opaque JOGL/TDF surfaces. */
final class SwingRecorder {
    private static volatile RecordingBuffer buffer;
    private static volatile CompletableFuture<Map<String, Object>> captureFuture;
    private static volatile boolean listenerInstalled;
    private static volatile Map<String, Object> selectionBefore;
    private SwingRecorder() { }

    static synchronized void start(RecordingBuffer destination) { buffer = destination; installListener(); }
    static void stop() { buffer = null; }
    static synchronized CompletableFuture<Map<String, Object>> beginCapture() {
        installListener();
        CompletableFuture<Map<String, Object>> future = new CompletableFuture<Map<String, Object>>();
        captureFuture = future; return future;
    }
    static void endCapture(CompletableFuture<Map<String, Object>> future) { if (captureFuture == future) captureFuture = null; }

    static Map<String, Object> hitTest(final double screenX, final double screenY) {
        final Map<String, Object>[] result = new Map[]{null};
        runOnEdtAndWait(new Runnable() { public void run() {
            Component component = componentAt((int)Math.round(screenX), (int)Math.round(screenY));
            if (component != null) result[0] = target(component, screenX, screenY);
        }});
        if (result[0] == null) throw new IllegalArgumentException("no Swing/AWT component found at screen coordinate");
        return result[0];
    }

    static List<Map<String, Object>> windows() {
        final List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        runOnEdtAndWait(new Runnable() { public void run() {
            for (Window window : Window.getWindows()) {
                if (!window.isShowing()) continue;
                Map<String, Object> item = snapshot(window);
                item.put("active", window.isActive()); item.put("focused", window.isFocused()); result.add(item);
            }
        }});
        return result;
    }

    static Map<String, Object> resolve(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath,
            String renderedClass, String trackClass, String trackIdentityKey, String trackIdentityValue) {
        Component component = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        if (trackIdentityKey != null && trackIdentityValue != null) {
            Component surface = RenderedSurfaceRegistry.nearestSurface(component);
            if (surface == null && RenderedSurfaceRegistry.adapterFor(component) != null) surface = component;
            if (surface == null) throw new IllegalArgumentException("rendered-object locator did not resolve a Solipsys surface");
            Map<String, Object> semantic = SolipsysAwtViewCanvasAdapter.resolveRenderedNode(
                    surface, renderedClass, trackClass, trackIdentityKey, trackIdentityValue);
            String resolutionStatus = String.valueOf(semantic.get("resolution_status"));
            if (!"resolved".equals(resolutionStatus)) {
                throw new IllegalArgumentException(
                        "Solipsys rendered object resolution failed: " + resolutionStatus
                        + " (candidate_count=" + semantic.get("candidate_count") + ")");
            }
            Map<String, Object> result = target(surface, null, null);
            result.put("semantic_node", semantic);
            Map<String, Object> promotion = castMap(result.get("promotion"));
            promotion.put("promoted", true);
            promotion.put("reason", "solipsys-rendered-object-locator");
            return result;
        }
        return target(component, null, null);
    }

    static Map<String, Object> activateWindow(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        final Component component = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            Window owner = component instanceof Window ? (Window)component : SwingUtilities.getWindowAncestor(component);
            if (owner != null) { owner.toFront(); owner.requestFocus(); }
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(target(component, null, null));
        result.put("window_activated", true);
        return result;
    }

    static Map<String, Object> focus(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        final Component component = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            Window owner = SwingUtilities.getWindowAncestor(component);
            if (owner != null) { owner.toFront(); owner.requestFocus(); }
            component.requestFocusInWindow();
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(target(component, null, null));
        result.put("focused", component.isFocusOwner());
        return result;
    }

    static Map<String, Object> activate(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        final Component component = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            Window owner = SwingUtilities.getWindowAncestor(component);
            if (owner != null) { owner.toFront(); owner.requestFocus(); }
            if (component instanceof AbstractButton) ((AbstractButton)component).doClick();
            else component.requestFocusInWindow();
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(target(component, null, null));
        result.put("activated", true);
        return result;
    }

    static Map<String, Object> setText(
            String name, String accessibleId, String nativeClass,
            String windowTitle, String componentPath, final String value) {
        final Component component = find(
                name, accessibleId, nativeClass, windowTitle, componentPath);
        if (!(component instanceof JTextComponent)) {
            throw new IllegalArgumentException("target is not a Swing text component");
        }
        final JTextComponent text = (JTextComponent)component;
        if (!text.isEditable()) {
            throw new IllegalArgumentException("Swing text component is read-only");
        }
        runOnEdtAndWait(new Runnable() { public void run() {
            text.requestFocusInWindow();
            text.setText(value == null ? "" : value);
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(
                target(component, null, null));
        result.put("text", text.getText());
        return result;
    }

    static String getText(
            String name, String accessibleId, String nativeClass,
            String windowTitle, String componentPath) {
        final Component component = find(
                name, accessibleId, nativeClass, windowTitle, componentPath);
        if (!(component instanceof JTextComponent)) {
            throw new IllegalArgumentException("target is not a Swing text component");
        }
        final String[] value = new String[]{""};
        runOnEdtAndWait(new Runnable() { public void run() {
            value[0] = ((JTextComponent)component).getText();
        }});
        return value[0];
    }

    static Map<String, Object> selectChild(
            String name, String accessibleId, String nativeClass,
            String windowTitle, String componentPath, final int index) {
        final Component component = find(
                name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            if (component instanceof JComboBox) {
                JComboBox combo = (JComboBox)component;
                if (index < 0 || index >= combo.getItemCount()) {
                    throw new IllegalArgumentException("combo-box index is outside the item range");
                }
                combo.setSelectedIndex(index);
            } else if (component instanceof JList) {
                JList list = (JList)component;
                if (index < 0 || index >= list.getModel().getSize()) {
                    throw new IllegalArgumentException("list index is outside the item range");
                }
                list.setSelectedIndex(index);
            } else if (component instanceof JTree) {
                JTree tree = (JTree)component;
                if (index < 0 || index >= tree.getRowCount()) {
                    throw new IllegalArgumentException("tree row is outside the visible row range");
                }
                tree.setSelectionRow(index);
            } else if (component instanceof JTable) {
                JTable table = (JTable)component;
                if (index < 0 || index >= table.getRowCount()) {
                    throw new IllegalArgumentException("table row is outside the row range");
                }
                table.setRowSelectionInterval(index, index);
            } else if (component instanceof JTabbedPane) {
                JTabbedPane tabs = (JTabbedPane)component;
                if (index < 0 || index >= tabs.getTabCount()) {
                    throw new IllegalArgumentException("tab index is outside the tab range");
                }
                tabs.setSelectedIndex(index);
            } else {
                throw new IllegalArgumentException(
                        "target does not expose indexed Swing selection");
            }
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(
                target(component, null, null));
        result.put("selected_index", index);
        return result;
    }

    static Number getValue(
            String name, String accessibleId, String nativeClass,
            String windowTitle, String componentPath) {
        final Component component = find(
                name, accessibleId, nativeClass, windowTitle, componentPath);
        final Number[] value = new Number[]{null};
        runOnEdtAndWait(new Runnable() { public void run() {
            if (component instanceof JSlider) {
                value[0] = Integer.valueOf(((JSlider)component).getValue());
            } else if (component instanceof JSpinner) {
                Object raw = ((JSpinner)component).getValue();
                if (!(raw instanceof Number)) {
                    throw new IllegalArgumentException("spinner value is not numeric");
                }
                value[0] = (Number)raw;
            } else {
                throw new IllegalArgumentException("target has no numeric Swing value");
            }
        }});
        return value[0];
    }

    static Map<String, Object> setValue(
            String name, String accessibleId, String nativeClass,
            String windowTitle, String componentPath, final double value) {
        final Component component = find(
                name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            if (component instanceof JSlider) {
                ((JSlider)component).setValue((int)Math.round(value));
            } else if (component instanceof JSpinner) {
                JSpinner spinner = (JSpinner)component;
                Object current = spinner.getValue();
                spinner.setValue(coerceNumber(value, current));
            } else {
                throw new IllegalArgumentException("target has no numeric Swing value");
            }
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(
                target(component, null, null));
        result.put("value", getValue(
                name, accessibleId, nativeClass, windowTitle, componentPath));
        return result;
    }

    private static Number coerceNumber(double value, Object current) {
        if (current instanceof Byte) return Byte.valueOf((byte)Math.round(value));
        if (current instanceof Short) return Short.valueOf((short)Math.round(value));
        if (current instanceof Integer) return Integer.valueOf((int)Math.round(value));
        if (current instanceof Long) return Long.valueOf(Math.round(value));
        if (current instanceof Float) return Float.valueOf((float)value);
        return Double.valueOf(value);
    }

    static Map<String, Object> selectMenuPath(
            String name, String accessibleId, String nativeClass, String windowTitle, String componentPath,
            final String[] ids, final String[] texts, final int[] ordinals) {
        final Component owner = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        final Component[] terminal = new Component[]{null};
        runOnEdtAndWait(new Runnable() { public void run() {
            Component current = owner;
            for (int index = 0; index < ids.length; index++) {
                Component child = findMenuChild(
                        current, ids[index], texts[index],
                        ordinals != null && index < ordinals.length ? ordinals[index] : -1);
                if (child == null) {
                    throw new IllegalArgumentException(
                            "menu path segment " + index + " did not resolve under " + current.getClass().getName());
                }
                terminal[0] = child;
                current = child;
            }
            if (!(terminal[0] instanceof JMenuItem)) {
                throw new IllegalArgumentException("terminal menu path segment is not a JMenuItem");
            }
            ((JMenuItem)terminal[0]).doClick();
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(target(terminal[0], null, null));
        result.put("selected_menu_path", true);
        return result;
    }

    private static Component find(final String name, final String accessibleId, final String nativeClass, final String windowTitle, final String componentPath) {
        final List<Component> matches = new ArrayList<Component>();
        runOnEdtAndWait(new Runnable() { public void run() {
            for (Window window : Window.getWindows()) if (window.isShowing()) collect(window, matches, name, accessibleId, nativeClass, windowTitle, componentPath);
        }});
        if (matches.isEmpty()) throw new IllegalArgumentException("no Swing/AWT component matched the locator");
        if (matches.size() > 1) throw new IllegalArgumentException("Swing/AWT locator is ambiguous: " + matches.size() + " matches");
        return matches.get(0);
    }

    private static void collect(Component component, List<Component> matches, String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        if (!component.isShowing()) return;
        Map<String, Object> item = snapshot(component);
        if (matches(item.get("name"), name) && matches(item.get("accessible_id"), accessibleId) && matches(item.get("native_class"), nativeClass)
                && matches(item.get("window"), windowTitle) && matches(item.get("component_path"), componentPath)) matches.add(component);
        if (component instanceof Container) for (Component child : ((Container)component).getComponents()) collect(child, matches, name, accessibleId, nativeClass, windowTitle, componentPath);
    }
    private static boolean matches(Object actual, String expected) { return expected == null || expected.trim().isEmpty() || (actual != null && expected.equals(String.valueOf(actual))); }

    private static Component findMenuChild(Component parent, String accessibleId, String text, int ordinal) {
        List<Component> children = menuChildren(parent);
        if (ordinal >= 0 && ordinal < children.size()) {
            Component candidate = children.get(ordinal);
            if (menuIdentityMatches(candidate, accessibleId, text)) return candidate;
        }
        for (Component child : children) {
            if (menuIdentityMatches(child, accessibleId, text)) return child;
        }
        return null;
    }

    private static boolean menuIdentityMatches(Component component, String accessibleId, String text) {
        if (accessibleId != null && !accessibleId.trim().isEmpty()) {
            String id = component.getName();
            if (!accessibleId.equals(id)) return false;
        }
        if (text != null && !text.trim().isEmpty()) {
            String actual = authoredText(component);
            if (blank(actual) && component instanceof Accessible) {
                AccessibleContext context = ((Accessible)component).getAccessibleContext();
                actual = context == null ? null : context.getAccessibleName();
            }
            if (!text.equals(actual)) return false;
        }
        return (accessibleId != null && !accessibleId.trim().isEmpty())
                || (text != null && !text.trim().isEmpty());
    }

    private static List<Component> menuChildren(Component component) {
        List<Component> result = new ArrayList<Component>();
        if (component instanceof JMenuBar) {
            JMenuBar bar = (JMenuBar)component;
            for (int index = 0; index < bar.getMenuCount(); index++) {
                JMenu menu = bar.getMenu(index);
                if (menu != null) result.add(menu);
            }
        } else if (component instanceof JMenu) {
            for (Component child : ((JMenu)component).getMenuComponents()) {
                result.add(child);
            }
        } else if (component instanceof JPopupMenu) {
            for (Component child : ((JPopupMenu)component).getComponents()) {
                result.add(child);
            }
        }
        return result;
    }

    private static List<Map<String, Object>> menuChildrenSnapshots(Component component) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        int ordinal = 0;
        for (Component child : menuChildren(component)) {
            Map<String, Object> item = snapshot(child);
            item.put("ordinal", ordinal++);
            item.put("disabled", !child.isEnabled());
            item.put("visible", child.isVisible());
            result.add(item);
        }
        return result;
    }

    private static synchronized void installListener() {
        if (listenerInstalled) return;
        Toolkit.getDefaultToolkit().addAWTEventListener(new AWTEventListener() { public void eventDispatched(AWTEvent event) { SwingRecorder.event(event); }}, AWTEvent.MOUSE_EVENT_MASK);
        listenerInstalled = true;
    }
    private static void event(AWTEvent value) {
        if (!(value instanceof MouseEvent)) return;
        final MouseEvent event = (MouseEvent)value;
        if (event.getID() == MouseEvent.MOUSE_PRESSED) {
            Component physical = event.getComponent();
            if (physical == null) return;
            Point screen = event.getLocationOnScreen();
            Component deepest = componentAtEventWindow(physical, screen.x, screen.y);
            if (deepest == null) deepest = physical;
            selectionBefore = selectionSnapshot(deepest);

            RecordingBuffer destination = buffer;
            if (destination != null) {
                Map<String, Object> pressTarget = target(
                        deepest, Double.valueOf(screen.x), Double.valueOf(screen.y));
                Component surface = RenderedSurfaceRegistry.nearestSurface(deepest);
                if (surface != null) {
                    promoteRenderedSelection(pressTarget, deepest, screen);
                    Map<String, Object> promotion = castMap(pressTarget.get("promotion"));
                    if (!Boolean.TRUE.equals(promotion.get("promoted"))) {
                        // Do not flash the entire render canvas when native
                        // semantic picking is not yet available at press time.
                        return;
                    }
                }
                emitPointerObservation(destination, event, screen, pressTarget, "pressed");
            }
            return;
        }
        if (event.getID() != MouseEvent.MOUSE_RELEASED) return;
        final Point screen = event.getLocationOnScreen();
        final Component physical = event.getComponent();
        final Map<String, Object> before = selectionBefore;
        selectionBefore = null;
        if (physical == null) return;
        EventQueue.invokeLater(new Runnable() {
            public void run() { processRelease(event, physical, screen, before); }
        });
    }

    private static void processRelease(MouseEvent event, Component physical, Point screen, Map<String, Object> before) {
        Component deepest = componentAtEventWindow(physical, screen.x, screen.y);
        if (deepest == null) deepest = physical;
        Map<String, Object> target = target(deepest, Double.valueOf(screen.x), Double.valueOf(screen.y));
        Map<String, Object> after = selectionSnapshot(deepest);
        promoteRenderedSelection(target, deepest, screen);
        if (before != null || after != null) {
            Map<String, Object> node = castMap(target.get("semantic_node"));
            Map<String, Object> properties = castMap(node.get("properties"));
            Map<String, Object> transition = new LinkedHashMap<String, Object>();
            if (before != null) transition.put("before", before);
            if (after != null) transition.put("after", after);
            transition.put("changed", !String.valueOf(before).equals(String.valueOf(after)));
            properties.put("render_surface_selection_transition", transition);
        }
        CompletableFuture<Map<String, Object>> pending = captureFuture;
        if (pending != null) pending.complete(target);
        RecordingBuffer destination = buffer;
        if (destination != null) {
            emitPointerObservation(destination, event, screen, target, "released");
        }
    }

    private static void promoteRenderedSelection(Map<String, Object> target, Component component, Point screen) {
        Component surface = RenderedSurfaceRegistry.nearestSurface(component);
        if (surface == null || !(RenderedSurfaceRegistry.adapterFor(surface) instanceof SolipsysAwtViewCanvasAdapter)) return;
        try {
            Map<String, Object> semantic = SolipsysAwtViewCanvasAdapter.selectedRenderedNode(
                    surface, screen.x, screen.y);
            if (semantic == null) return;
            target.put("semantic_node", semantic);
            Map<String, Object> promotion = castMap(target.get("promotion"));
            promotion.put("promoted", true);
            promotion.put("reason", "solipsys-native-selection");
            promotion.put("descendant_depth", 1);
        } catch (Throwable ignored) { }
    }

    private static Map<String, Object> selectionSnapshot(Component component) {
        Component surface = RenderedSurfaceRegistry.nearestSurface(component);
        if (surface == null) return null;
        RenderedSurfaceAdapter adapter = RenderedSurfaceRegistry.adapterFor(surface);
        if (!(adapter instanceof SolipsysAwtViewCanvasAdapter)) return null;
        try { return SolipsysAwtViewCanvasAdapter.selectionSnapshot(surface); }
        catch (Throwable ignored) { return null; }
    }

    private static void emitPointerObservation(
            RecordingBuffer destination, MouseEvent event, Point screen,
            Map<String, Object> target, String phase) {
        Map<String, Object> observation = new LinkedHashMap<String, Object>();
        observation.put("type", "pointer");
        observation.put("timestamp", System.nanoTime() / 1000000000.0);
        observation.put("target", target);
        observation.put(
                "button",
                event.getButton() == MouseEvent.BUTTON3 ? "secondary" : "primary");
        observation.put("phase", phase);
        List<Object> coordinates = new ArrayList<Object>();
        coordinates.add(screen.x);
        coordinates.add(screen.y);
        observation.put("coordinates", coordinates);
        destination.offer(observation);
    }

    private static Component componentAtEventWindow(
            Component physical, int screenX, int screenY) {
        Window owner = physical instanceof Window
                ? (Window)physical : SwingUtilities.getWindowAncestor(physical);
        if (owner == null || !owner.isShowing()) return physical;
        Rectangle bounds = owner.getBounds();
        if (!bounds.contains(screenX, screenY)) return physical;
        Component deepest = SwingUtilities.getDeepestComponentAt(
                owner, screenX - bounds.x, screenY - bounds.y);
        return deepest != null ? deepest : physical;
    }

    private static Component componentAt(int screenX, int screenY) {
        Window[] windows = Window.getWindows();
        for (int pass = 0; pass < 2; pass++) for (int index = windows.length - 1; index >= 0; index--) {
            Window window = windows[index]; if (!window.isShowing() || (pass == 0 && !window.isActive())) continue;
            Rectangle bounds = window.getBounds(); if (!bounds.contains(screenX, screenY)) continue;
            Component deepest = SwingUtilities.getDeepestComponentAt(window, screenX - bounds.x, screenY - bounds.y); return deepest != null ? deepest : window;
        }
        return null;
    }

    private static Map<String, Object> target(Component component, Double screenX, Double screenY) {
        Component surface = RenderedSurfaceRegistry.nearestSurface(component);
        Component semantic = surface == null ? component : surface;
        Map<String, Object> node = snapshot(semantic);
        Map<String, Object> promotion = new LinkedHashMap<String, Object>();
        promotion.put("promoted", surface != null && surface != component);
        promotion.put("descendant_depth", surface != null && surface != component ? 1 : 0);
        promotion.put("reason", surface == null ? "deepest-awt-component" : "rendered-surface-boundary");
        if (surface != null) {
            RenderedSurfaceAdapter adapter = RenderedSurfaceRegistry.adapterFor(surface);
            Map<String, Object> properties = castMap(node.get("properties"));
            properties.put("opaque_render_surface", true);
            properties.put("render_surface_adapter", adapter.name());
        }
        Map<String, Object> result = new LinkedHashMap<String, Object>(); result.put("physical_node", node); result.put("semantic_node", node); result.put("promotion", promotion); return result;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Object value) { return (Map<String, Object>) value; }

    private static Map<String, Object> snapshot(Component component) {
        Map<String, Object> result = new LinkedHashMap<String, Object>(); String className = component.getClass().getName();
        result.put("framework", "swing"); result.put("class", className); result.put("native_class", className); result.put("ref", Integer.toHexString(System.identityHashCode(component)));
        result.put("role", role(component)); result.put("object_type", objectType(component)); result.put("actions", actions(component));
        AccessibleContext accessible = component instanceof Accessible ? ((Accessible)component).getAccessibleContext() : null;
        String name = accessible == null ? component.getName() : accessible.getAccessibleName(); if (blank(name)) name = authoredText(component); if (!blank(name)) result.put("name", name);
        if (!blank(component.getName())) result.put("accessible_id", component.getName());
        Window owner = SwingUtilities.getWindowAncestor(component); if (owner != null) { String title = windowTitle(owner); result.put("window", title); result.put("application", title); }
        Point location = new Point(0, 0); try { SwingUtilities.convertPointToScreen(location, component); } catch (RuntimeException ignored) { }
        List<Object> bounds = new ArrayList<Object>(); bounds.add(location.x); bounds.add(location.y); bounds.add(component.getWidth()); bounds.add(component.getHeight()); result.put("bounds", bounds);
        result.put("hierarchy", hierarchy(component)); result.put("component_path", componentPath(component)); result.put("sibling_index", siblingIndex(component));
        Component parent = component.getParent(); if (parent != null) result.put("parent", parentIdentity(parent));
        Map<String, Object> state = new LinkedHashMap<String, Object>(); state.put("present", true); state.put("visible", component.isVisible()); state.put("showing", component.isShowing()); state.put("enabled", component.isEnabled()); state.put("focused", component.isFocusOwner()); result.put("state", state);
        if (component instanceof JMenuBar || component instanceof JMenu || component instanceof JPopupMenu) {
            result.put("menu_children", menuChildrenSnapshots(component));
        }
        Map<String, Object> properties = new LinkedHashMap<String, Object>(); properties.put("process_id", RuntimeDiagnostics.snapshot().get("pid")); properties.put("opaque_render_surface", isRenderSurface(component)); properties.put("window_active", owner != null && owner.isActive()); properties.put("window_focused", owner != null && owner.isFocused()); properties.put("semantic_ancestors", semanticAncestors(component)); result.put("properties", properties);
        return result;
    }

    private static Map<String, Object> parentIdentity(Component component) {
        Map<String, Object> result = new LinkedHashMap<String, Object>(); result.put("native_class", component.getClass().getName()); result.put("role", role(component));
        AccessibleContext accessible = component instanceof Accessible ? ((Accessible)component).getAccessibleContext() : null; String name = accessible == null ? component.getName() : accessible.getAccessibleName(); if (blank(name)) name = authoredText(component); if (!blank(name)) result.put("name", name); if (!blank(component.getName())) result.put("accessible_id", component.getName()); return result;
    }
    private static String componentPath(Component component) { List<String> segments = new ArrayList<String>(); Component current = component; while (current != null) { segments.add(current.getClass().getName() + "[" + siblingIndex(current) + "]"); current = current.getParent(); } Collections.reverse(segments); return join(segments, "/"); }
    private static int siblingIndex(Component component) { Container parent = component.getParent(); if (parent == null) return 0; int index = 0; for (Component sibling : parent.getComponents()) { if (sibling == component) return index; if (sibling.getClass().equals(component.getClass())) index++; } return index; }
    private static boolean isRenderSurface(Component component) { if (RenderedSurfaceRegistry.adapterFor(component) != null) return true; String name = component.getClass().getName().toLowerCase(Locale.ROOT); return name.startsWith("com.jogamp.opengl.") || name.startsWith("javax.media.opengl.") || name.contains("glcanvas") || name.contains("gljpanel") || name.contains("tdf"); }
    private static List<String> hierarchy(Component component) { List<String> result = new ArrayList<String>(); Component current = component; while (current != null) { result.add(current.getClass().getName()); current = current.getParent(); } Collections.reverse(result); return result; }
    private static List<Map<String, Object>> semanticAncestors(Component component) {
        List<Component> ancestry = new ArrayList<Component>();
        Component current = component.getParent();
        while (current != null) {
            ancestry.add(current);
            current = current.getParent();
        }
        Collections.reverse(ancestry);
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        for (Component ancestor : ancestry) {
            String type = objectType(ancestor);
            if (!isSemanticContainer(ancestor, type)) continue;
            Map<String, Object> descriptor = parentIdentity(ancestor);
            descriptor.put("object_type", type);
            descriptor.put("component_path", componentPath(ancestor));
            Window window = ancestor instanceof Window
                    ? (Window)ancestor : SwingUtilities.getWindowAncestor(ancestor);
            if (window != null) descriptor.put("window", windowTitle(window));
            result.add(descriptor);
        }
        return result;
    }

    private static boolean isSemanticContainer(Component component, String type) {
        if ("window".equals(type) || "dialog".equals(type)
                || "tab_container".equals(type) || "toolbar".equals(type)
                || "menu_bar".equals(type) || "menu".equals(type)
                || "context_menu".equals(type)) return true;
        if (!"panel".equals(type)) return false;
        String name = component.getName();
        AccessibleContext context = component instanceof Accessible
                ? ((Accessible)component).getAccessibleContext() : null;
        String accessibleName = context == null ? null : context.getAccessibleName();
        String candidate = !blank(accessibleName) ? accessibleName : name;
        if (blank(candidate)) return false;
        String folded = candidate.trim().toLowerCase(Locale.ROOT);
        return !folded.equals("contentpane") && !folded.equals("content pane")
                && !folded.equals("glasspane") && !folded.equals("glass pane")
                && !folded.equals("rootpane") && !folded.equals("root pane");
    }

    private static String authoredText(Component component) { if (component instanceof AbstractButton) return ((AbstractButton)component).getText(); if (component instanceof JLabel) return ((JLabel)component).getText(); if (component instanceof JTextComponent) return ((JTextComponent)component).getText(); return null; }
    private static String role(Component component) { AccessibleContext context = component instanceof Accessible ? ((Accessible)component).getAccessibleContext() : null; if (context != null && context.getAccessibleRole() != null && RenderedSurfaceRegistry.adapterFor(component) == null) return context.getAccessibleRole().toDisplayString(Locale.ROOT).toLowerCase(Locale.ROOT); return isRenderSurface(component) ? "canvas" : (component instanceof Container ? "panel" : "custom"); }
    private static String objectType(Component component) {
        if (isRenderSurface(component)) return "canvas";
        if (component instanceof JMenuBar) return "menu_bar";
        if (component instanceof JPopupMenu) return "context_menu";
        if (component instanceof JCheckBoxMenuItem) return "check_menu_item";
        if (component instanceof JRadioButtonMenuItem) return "radio_menu_item";
        if (component instanceof JMenu) return "menu";
        if (component instanceof JMenuItem) return "menu_item";
        if (component instanceof JSeparator) return "custom";
        if (component instanceof JCheckBox) return "check_box";
        if (component instanceof JRadioButton) return "radio_button";
        if (component instanceof JToggleButton && !(component instanceof JButton)) return "toggle_button";
        if (component instanceof JButton) return "button";
        if (component instanceof JPasswordField) return "password_field";
        if (component instanceof JTextArea) return "text_area";
        if (component instanceof JTextField || component instanceof JTextComponent) return "text_field";
        if (component instanceof JComboBox) return "combo_box";
        if (component instanceof JList) return "list";
        if (component instanceof JTree) return "tree";
        if (component instanceof JTable) return "table";
        if (component instanceof JSlider) return "slider";
        if (component instanceof JSpinner) return "spinner";
        if (component instanceof JTabbedPane) return "tab_container";
        if (component instanceof JToolBar) return "toolbar";
        if (component instanceof JLabel) return "label";
        if (component instanceof java.awt.Dialog) return "dialog";
        if (component instanceof Window) return "window";
        if (component instanceof Container) return "panel";
        return "custom";
    }
    private static List<String> actions(Component component) {
        List<String> result = new ArrayList<String>();
        result.add("resolve");
        if (component instanceof JMenuBar || component instanceof JMenu || component instanceof JPopupMenu) {
            result.add("select_menu_item");
            if (component instanceof JMenu) result.add("click");
            return result;
        }
        if (component instanceof JSeparator) return result;
        result.add("click");
        if (component.isFocusable()) result.add("focus");
        if (component instanceof JTextComponent && ((JTextComponent)component).isEditable()) {
            result.add("set_text");
        }
        if (component instanceof JComboBox || component instanceof JList
                || component instanceof JTree || component instanceof JTabbedPane) {
            result.add("select_item");
        }
        if (component instanceof JTable) result.add("select_row");
        if (component instanceof JSlider || component instanceof JSpinner) {
            result.add("set_value");
        }
        if (component instanceof AbstractButton) result.add("activate");
        return result;
    }
    private static String windowTitle(Window window) { if (window instanceof java.awt.Frame) return ((java.awt.Frame)window).getTitle(); if (window instanceof java.awt.Dialog) return ((java.awt.Dialog)window).getTitle(); return window.getName() == null ? window.getClass().getSimpleName() : window.getName(); }
    private static boolean blank(String value) { return value == null || value.trim().isEmpty(); }
    private static String join(List<String> values, String delimiter) { StringBuilder result = new StringBuilder(); for (String value : values) { if (result.length() > 0) result.append(delimiter); result.append(value); } return result.toString(); }
    private static void runOnEdtAndWait(Runnable work) { if (EventQueue.isDispatchThread()) { work.run(); return; } try { EventQueue.invokeAndWait(work); } catch (Exception exception) { throw new IllegalStateException("Swing EDT operation failed", exception); } }
}
