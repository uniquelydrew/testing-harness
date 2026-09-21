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
import javax.swing.JLabel;
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

    static Map<String, Object> resolve(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        return target(find(name, accessibleId, nativeClass, windowTitle, componentPath), null, null);
    }

    static Map<String, Object> activate(String name, String accessibleId, String nativeClass, String windowTitle, String componentPath) {
        final Component component = find(name, accessibleId, nativeClass, windowTitle, componentPath);
        runOnEdtAndWait(new Runnable() { public void run() {
            Window owner = SwingUtilities.getWindowAncestor(component);
            if (owner != null) { owner.toFront(); owner.requestFocus(); }
            component.requestFocusInWindow();
        }});
        Map<String, Object> result = new LinkedHashMap<String, Object>(target(component, null, null)); result.put("activated", true); return result;
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
            Component deepest = componentAt(screen.x, screen.y);
            if (deepest == null) deepest = physical;
            selectionBefore = selectionSnapshot(deepest);
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
        Component deepest = componentAt(screen.x, screen.y);
        if (deepest == null) deepest = physical;
        Map<String, Object> target = target(deepest, Double.valueOf(screen.x), Double.valueOf(screen.y));
        Map<String, Object> after = selectionSnapshot(deepest);
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
            Map<String, Object> observation = new LinkedHashMap<String, Object>();
            observation.put("type", "pointer");
            observation.put("timestamp", System.nanoTime() / 1000000000.0);
            observation.put("target", target);
            observation.put("button", event.getButton() == MouseEvent.BUTTON3 ? "secondary" : "primary");
            observation.put("phase", "released");
            List<Object> coordinates = new ArrayList<Object>();
            coordinates.add(screen.x); coordinates.add(screen.y);
            observation.put("coordinates", coordinates);
            destination.offer(observation);
        }
    }

    private static Map<String, Object> selectionSnapshot(Component component) {
        Component surface = RenderedSurfaceRegistry.nearestSurface(component);
        if (surface == null) return null;
        RenderedSurfaceAdapter adapter = RenderedSurfaceRegistry.adapterFor(surface);
        if (!(adapter instanceof SolipsysAwtViewCanvasAdapter)) return null;
        try { return SolipsysAwtViewCanvasAdapter.selectionSnapshot(surface); }
        catch (Throwable ignored) { return null; }
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
            if (screenX != null && screenY != null) {
                try {
                    Map<String, Object> inspection = RenderedSurfaceDiagnostics.inspectAt(screenX.doubleValue(), screenY.doubleValue());
                    properties.put("render_surface_inspection", inspection);
                } catch (Throwable error) {
                    properties.put("render_surface_inspection_error", error.getClass().getName() + ": " + String.valueOf(error.getMessage()));
                }
            }
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
        Map<String, Object> properties = new LinkedHashMap<String, Object>(); properties.put("process_id", RuntimeDiagnostics.snapshot().get("pid")); properties.put("opaque_render_surface", isRenderSurface(component)); properties.put("window_active", owner != null && owner.isActive()); properties.put("window_focused", owner != null && owner.isFocused()); result.put("properties", properties);
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
    private static String authoredText(Component component) { if (component instanceof AbstractButton) return ((AbstractButton)component).getText(); if (component instanceof JLabel) return ((JLabel)component).getText(); if (component instanceof JTextComponent) return ((JTextComponent)component).getText(); return null; }
    private static String role(Component component) { AccessibleContext context = component instanceof Accessible ? ((Accessible)component).getAccessibleContext() : null; if (context != null && context.getAccessibleRole() != null && RenderedSurfaceRegistry.adapterFor(component) == null) return context.getAccessibleRole().toDisplayString(Locale.ROOT).toLowerCase(Locale.ROOT); return isRenderSurface(component) ? "canvas" : (component instanceof Container ? "panel" : "custom"); }
    private static String objectType(Component component) { if (isRenderSurface(component)) return "canvas"; if (component instanceof AbstractButton) return "button"; if (component instanceof JTextComponent) return "text_field"; if (component instanceof JLabel) return "label"; if (component instanceof Window) return "window"; if (component instanceof Container) return "panel"; return "custom"; }
    private static List<String> actions(Component component) { List<String> result = new ArrayList<String>(); result.add("resolve"); result.add("click"); if (component instanceof AbstractButton) result.add("activate"); return result; }
    private static String windowTitle(Window window) { if (window instanceof java.awt.Frame) return ((java.awt.Frame)window).getTitle(); if (window instanceof java.awt.Dialog) return ((java.awt.Dialog)window).getTitle(); return window.getName() == null ? window.getClass().getSimpleName() : window.getName(); }
    private static boolean blank(String value) { return value == null || value.trim().isEmpty(); }
    private static String join(List<String> values, String delimiter) { StringBuilder result = new StringBuilder(); for (String value : values) { if (result.length() > 0) result.append(delimiter); result.append(value); } return result.toString(); }
    private static void runOnEdtAndWait(Runnable work) { if (EventQueue.isDispatchThread()) { work.run(); return; } try { EventQueue.invokeAndWait(work); } catch (Exception exception) { throw new IllegalStateException("Swing EDT operation failed", exception); } }
}
