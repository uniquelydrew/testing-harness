package automation.harness.agent;

import java.awt.AWTEvent;
import java.awt.Component;
import java.awt.Container;
import java.awt.EventQueue;
import java.awt.MouseInfo;
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
import javax.accessibility.AccessibleContext;
import javax.accessibility.Accessible;
import javax.swing.AbstractButton;
import javax.swing.JLabel;
import javax.swing.SwingUtilities;
import javax.swing.text.JTextComponent;

/** Native Swing/AWT discovery and recording for instrumented mixed Java UIs. */
final class SwingRecorder {
    private static volatile RecordingBuffer buffer;
    private static volatile CompletableFuture<Map<String, Object>> captureFuture;
    private static volatile boolean listenerInstalled;

    private SwingRecorder() { }

    static synchronized void start(RecordingBuffer destination) {
        buffer = destination;
        installListener();
    }

    static void stop() { buffer = null; }

    static synchronized CompletableFuture<Map<String, Object>> beginCapture() {
        installListener();
        CompletableFuture<Map<String, Object>> future = new CompletableFuture<>();
        captureFuture = future;
        return future;
    }

    static void endCapture(CompletableFuture<Map<String, Object>> future) {
        if (captureFuture == future) captureFuture = null;
    }

    static Map<String, Object> hitTest(double screenX, double screenY) {
        final Map<String, Object>[] result = new Map[]{null};
        runOnEdtAndWait(() -> {
            Component component = componentAt((int) Math.round(screenX), (int) Math.round(screenY));
            if (component != null) result[0] = target(component);
        });
        if (result[0] == null) throw new IllegalArgumentException("no Swing/AWT component found at screen coordinate");
        return result[0];
    }

    static List<Map<String, Object>> windows() {
        List<Map<String, Object>> result = new ArrayList<>();
        runOnEdtAndWait(() -> {
            for (Window window : Window.getWindows()) {
                if (!window.isShowing()) continue;
                Map<String, Object> item = snapshot(window);
                item.put("active", window.isActive());
                item.put("focused", window.isFocused());
                result.add(item);
            }
        });
        return result;
    }

    static Map<String, Object> resolve(String name, String accessibleId, String nativeClass, String windowTitle) {
        return target(find(name, accessibleId, nativeClass, windowTitle));
    }

    static Map<String, Object> activate(String name, String accessibleId, String nativeClass, String windowTitle) {
        Component component = find(name, accessibleId, nativeClass, windowTitle);
        runOnEdtAndWait(() -> {
            if (component instanceof AbstractButton button) button.doClick();
            else component.requestFocusInWindow();
        });
        Map<String, Object> result = new LinkedHashMap<>(target(component));
        result.put("activated", true);
        return result;
    }

    private static Component find(String name, String accessibleId, String nativeClass, String windowTitle) {
        final List<Component> matches = new ArrayList<>();
        runOnEdtAndWait(() -> {
            for (Window window : Window.getWindows()) {
                if (!window.isShowing()) continue;
                collect(window, matches, name, accessibleId, nativeClass, windowTitle);
            }
        });
        if (matches.isEmpty()) throw new IllegalArgumentException("no Swing/AWT component matched the locator");
        if (matches.size() > 1) throw new IllegalArgumentException("Swing/AWT locator is ambiguous: " + matches.size() + " matches");
        return matches.get(0);
    }

    private static void collect(Component component, List<Component> matches, String name, String accessibleId, String nativeClass, String windowTitle) {
        if (!component.isShowing()) return;
        Map<String, Object> item = snapshot(component);
        if (matches(item.get("name"), name)
                && matches(item.get("accessible_id"), accessibleId)
                && matches(item.get("native_class"), nativeClass)
                && matches(item.get("window"), windowTitle)) {
            matches.add(component);
        }
        if (component instanceof Container container) {
            for (Component child : container.getComponents()) collect(child, matches, name, accessibleId, nativeClass, windowTitle);
        }
    }

    private static boolean matches(Object actual, String expected) {
        return expected == null || expected.isBlank() || (actual != null && expected.equals(String.valueOf(actual)));
    }

    private static synchronized void installListener() {
        if (listenerInstalled) return;
        Toolkit.getDefaultToolkit().addAWTEventListener(SwingRecorder::event, AWTEvent.MOUSE_EVENT_MASK);
        listenerInstalled = true;
    }

    private static void event(AWTEvent value) {
        if (!(value instanceof MouseEvent event) || event.getID() != MouseEvent.MOUSE_RELEASED) return;
        Component physical = event.getComponent();
        if (physical == null) return;
        Point screen = event.getLocationOnScreen();
        Component deepest = componentAt(screen.x, screen.y);
        if (deepest == null) deepest = physical;
        Map<String, Object> target = target(deepest);
        CompletableFuture<Map<String, Object>> pending = captureFuture;
        if (pending != null) pending.complete(target);
        RecordingBuffer destination = buffer;
        if (destination != null) {
            Map<String, Object> observation = new LinkedHashMap<>();
            observation.put("type", "pointer");
            observation.put("timestamp", System.nanoTime() / 1_000_000_000.0);
            observation.put("target", target);
            observation.put("button", event.getButton() == MouseEvent.BUTTON3 ? "secondary" : "primary");
            observation.put("phase", "released");
            observation.put("coordinates", List.of(screen.x, screen.y));
            destination.offer(observation);
        }
    }

    private static Component componentAt(int screenX, int screenY) {
        Window[] windows = Window.getWindows();
        for (int pass = 0; pass < 2; pass++) {
            for (int index = windows.length - 1; index >= 0; index--) {
                Window window = windows[index];
                if (!window.isShowing() || (pass == 0 && !window.isActive())) continue;
                Rectangle bounds = window.getBounds();
                if (!bounds.contains(screenX, screenY)) continue;
                int localX = screenX - bounds.x;
                int localY = screenY - bounds.y;
                Component deepest = SwingUtilities.getDeepestComponentAt(window, localX, localY);
                return deepest != null ? deepest : window;
            }
        }
        return null;
    }

    private static Map<String, Object> target(Component component) {
        Map<String, Object> node = snapshot(component);
        return Map.of(
            "physical_node", node,
            "semantic_node", node,
            "promotion", Map.of("promoted", false, "descendant_depth", 0, "reason", "deepest-awt-component")
        );
    }

    private static Map<String, Object> snapshot(Component component) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("framework", "swing");
        result.put("class", component.getClass().getName());
        result.put("native_class", component.getClass().getName());
        result.put("ref", Integer.toHexString(System.identityHashCode(component)));
        result.put("role", role(component));
        result.put("object_type", objectType(component));
        result.put("actions", actions(component));
        AccessibleContext accessible = component instanceof Accessible value ? value.getAccessibleContext() : null;
        String name = accessible == null ? component.getName() : accessible.getAccessibleName();
        if (name == null || name.isBlank()) name = authoredText(component);
        if (name != null && !name.isBlank()) result.put("name", name);
        if (component.getName() != null && !component.getName().isBlank()) result.put("accessible_id", component.getName());
        Window owner = SwingUtilities.getWindowAncestor(component);
        if (owner != null) {
            String title = windowTitle(owner);
            result.put("window", title);
            result.put("application", title);
        }
        Point location = new Point(0, 0);
        SwingUtilities.convertPointToScreen(location, component);
        result.put("bounds", List.of(location.x, location.y, component.getWidth(), component.getHeight()));
        result.put("hierarchy", hierarchy(component));
        result.put("state", Map.of(
            "present", true,
            "visible", component.isVisible(),
            "showing", component.isShowing(),
            "enabled", component.isEnabled(),
            "focused", component.isFocusOwner()
        ));
        Map<String, Object> properties = new LinkedHashMap<>();
        properties.put("process_id", ProcessHandle.current().pid());
        properties.put("opaque_render_surface", isRenderSurface(component));
        properties.put("window_active", owner != null && owner.isActive());
        properties.put("window_focused", owner != null && owner.isFocused());
        result.put("properties", properties);
        return result;
    }

    private static boolean isRenderSurface(Component component) {
        String name = component.getClass().getName().toLowerCase(Locale.ROOT);
        return name.startsWith("com.jogamp.opengl.") || name.startsWith("javax.media.opengl.")
            || name.contains("glcanvas") || name.contains("gljpanel");
    }

    private static List<String> hierarchy(Component component) {
        List<String> result = new ArrayList<>();
        Component current = component;
        while (current != null) {
            result.add(current.getClass().getName());
            current = current.getParent();
        }
        Collections.reverse(result);
        return result;
    }

    private static String authoredText(Component component) {
        if (component instanceof AbstractButton button) return button.getText();
        if (component instanceof JLabel label) return label.getText();
        if (component instanceof JTextComponent text) return text.getText();
        return null;
    }

    private static String role(Component component) {
        AccessibleContext context = component instanceof Accessible value ? value.getAccessibleContext() : null;
        if (context != null && context.getAccessibleRole() != null) {
            return context.getAccessibleRole().toDisplayString(Locale.ROOT).toLowerCase(Locale.ROOT);
        }
        return isRenderSurface(component) ? "canvas" : (component instanceof Container ? "panel" : "custom");
    }

    private static String objectType(Component component) {
        if (isRenderSurface(component)) return "canvas";
        if (component instanceof AbstractButton) return "button";
        if (component instanceof JTextComponent) return "text_field";
        if (component instanceof JLabel) return "label";
        if (component instanceof Window) return "window";
        if (component instanceof Container) return "panel";
        return "custom";
    }

    private static List<String> actions(Component component) {
        if (component instanceof AbstractButton) return List.of("resolve", "activate", "click");
        return List.of("resolve", "click");
    }

    private static String windowTitle(Window window) {
        if (window instanceof java.awt.Frame frame) return frame.getTitle();
        if (window instanceof java.awt.Dialog dialog) return dialog.getTitle();
        return window.getName() == null ? window.getClass().getSimpleName() : window.getName();
    }

    private static void runOnEdtAndWait(Runnable work) {
        if (EventQueue.isDispatchThread()) { work.run(); return; }
        try { EventQueue.invokeAndWait(work); }
        catch (Exception exception) { throw new IllegalStateException("Swing EDT operation failed", exception); }
    }
}
