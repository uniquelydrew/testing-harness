package automation.harness.agent;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.lang.reflect.Proxy;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Installs focused JavaFX scene listeners only while a recording is active. */
final class JavaFxRecorder {
    private static final Set<Object> ATTACHED_SCENES = Collections.newSetFromMap(new IdentityHashMap<>());
    private static final Set<Object> OBSERVED_TARGETS = Collections.newSetFromMap(new IdentityHashMap<>());
    private static volatile RecordingBuffer buffer;
    private static volatile CompletableFuture<Map<String, Object>> captureFuture;

    private JavaFxRecorder() { }

    static void start(RecordingBuffer destination) {
        buffer = destination;
        runOnFxThread(JavaFxRecorder::attachOpenScenes);
    }

    static void stop() { buffer = null; }

    static Map<String, Object> captureNextClick(long timeoutMillis) throws Exception {
        CompletableFuture<Map<String, Object>> future = new CompletableFuture<>();
        captureFuture = future;
        runOnFxThread(JavaFxRecorder::attachOpenScenes);
        try { return future.get(timeoutMillis, TimeUnit.MILLISECONDS); }
        finally { captureFuture = null; }
    }

    static Map<String, Object> hitTest(double screenX, double screenY) {
        AtomicReference<Map<String, Object>> result = new AtomicReference<>();
        runOnFxThreadAndWait(() -> {
            try {
                Object windows = Class.forName("javafx.stage.Window").getMethod("getWindows").invoke(null);
                if (!(windows instanceof Iterable<?> iterable)) return;
                for (Object window : iterable) {
                    if (!Boolean.TRUE.equals(invoke(window, "isShowing"))) continue;
                    Object scene = invoke(window, "getScene");
                    if (scene == null) continue;
                    double x = screenX - ((Number) invoke(window, "getX")).doubleValue();
                    double y = screenY - ((Number) invoke(window, "getY")).doubleValue();
                    Object pick = scene.getClass().getMethod("pick", double.class, double.class).invoke(scene, x, y);
                    Object node = invoke(pick, "getIntersectedNode");
                    if (node != null) { result.set(target(JavaFxSemanticTargetResolver.resolveSemanticTarget(node))); return; }
                }
            } catch (ReflectiveOperationException ignored) { }
        });
        if (result.get() == null) throw new IllegalArgumentException("no JavaFX node found at screen coordinate");
        return result.get();
    }

    private static void attachOpenScenes() {
        try {
            Object windows = Class.forName("javafx.stage.Window").getMethod("getWindows").invoke(null);
            if (windows instanceof Iterable<?> iterable) {
                for (Object window : iterable) attachScene(invoke(window, "getScene"));
            }
        } catch (ReflectiveOperationException ignored) {
            // JavaFX is not present in this application; health remains usable.
        }
    }

    private static void attachScene(Object scene) {
        if (scene == null || !ATTACHED_SCENES.add(scene)) return;
        try {
            Method addFilter = Arrays.stream(scene.getClass().getMethods())
                .filter(method -> method.getName().equals("addEventFilter") && method.getParameterCount() == 2)
                .findFirst().orElseThrow();
            Class<?> eventHandler = addFilter.getParameterTypes()[1];
            addFilter.invoke(scene, eventType("javafx.scene.input.MouseEvent", "MOUSE_RELEASED"), handler(eventHandler, event -> pointer(event)));
            addFilter.invoke(scene, eventType("javafx.event.ActionEvent", "ACTION"), handler(eventHandler, event -> action(event)));
            Object focusProperty = invoke(scene, "focusOwnerProperty");
            addListener(focusProperty, () -> focus(scene));
        } catch (ReflectiveOperationException | RuntimeException ignored) {
            debug("could not attach JavaFX scene listeners", ignored);
            // A partial JavaFX runtime should not prevent the target from running.
        }
    }

    private static void pointer(Object event) {
        Object physical = invoke(event, "getTarget");
        if (physical == null) return;
        Object button = invoke(event, "getButton");
        String buttonText = String.valueOf(button).equals("SECONDARY") ? "secondary" : "primary";
        Map<String, Object> target = target(JavaFxSemanticTargetResolver.resolveSemanticTarget(physical));
        CompletableFuture<Map<String, Object>> pending = captureFuture;
        if (pending != null) pending.complete(target);
        Map<String, Object> result = event("pointer", physical);
        result.put("button", buttonText);
        result.put("phase", "released");
        offer(result);
        observe(JavaFxSemanticTargetResolver.resolveSemanticTarget(physical).semanticTarget());
    }

    private static void action(Object event) {
        Object target = invoke(event, "getTarget");
        if (target == null) return;
        Map<String, Object> result = event("action", target);
        result.put("action", "activate");
        offer(result);
    }

    private static void focus(Object scene) {
        Object target = invoke(scene, "getFocusOwner");
        if (target != null) {
            offer(event("focus", target));
            observe(JavaFxSemanticTargetResolver.resolveSemanticTarget(target).semanticTarget());
        }
    }

    private static void observe(Object target) {
        if (target == null || !OBSERVED_TARGETS.add(target)) return;
        observeProperty(target, "textProperty", "getText", "text_changed");
        observeProperty(target, "selectedProperty", "isSelected", "state_changed");
        observeProperty(target, "valueProperty", "getValue", "state_changed");
    }

    private static void observeProperty(Object target, String propertyMethod, String valueMethod, String type) {
        try {
            Object property = invoke(target, propertyMethod);
            if (property == null) return;
            Object[] previous = {invoke(target, valueMethod)};
            addListener(property, () -> {
                Object current = invoke(target, valueMethod);
                if (java.util.Objects.equals(previous[0], current)) return;
                Map<String, Object> result = event(type, target);
                if (type.equals("text_changed")) {
                    result.put("before", previous[0]); result.put("after", current);
                } else {
                    result.put("property", propertyMethod.equals("selectedProperty") ? "selected" : "value");
                    result.put("before", previous[0]); result.put("after", current);
                }
                previous[0] = current;
                offer(result);
            });
        } catch (RuntimeException ignored) {
            debug("could not observe JavaFX property " + propertyMethod, ignored);
            // A custom control can expose a nonstandard property surface.
        }
    }

    private static Map<String, Object> event(String type, Object physical) {
        JavaFxSemanticTargetResolver.Resolution resolution = JavaFxSemanticTargetResolver.resolveSemanticTarget(physical);
        Map<String, Object> target = target(resolution);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("type", type); result.put("timestamp", System.nanoTime() / 1_000_000_000.0); result.put("target", target);
        return result;
    }

    private static Map<String, Object> target(JavaFxSemanticTargetResolver.Resolution resolution) {
        Map<String, Object> target = new LinkedHashMap<>();
        target.put("physical_node", snapshot(resolution.physicalTarget()));
        target.put("semantic_node", snapshot(resolution.semanticTarget()));
        target.put("promotion", Map.of("promoted", resolution.promoted(), "descendant_depth", resolution.descendantDepth(), "reason", resolution.reason()));
        return target;
    }

    private static Map<String, Object> snapshot(Object node) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("class", node.getClass().getName());
        Object id = invoke(node, "getId");
        Object text = invoke(node, "getText");
        Object accessibleText = invoke(node, "getAccessibleText");
        if (id != null) result.put("accessible_id", String.valueOf(id));
        if (text != null && !String.valueOf(text).isBlank()) result.put("name", String.valueOf(text));
        else if (accessibleText != null && !String.valueOf(accessibleText).isBlank()) result.put("name", String.valueOf(accessibleText));
        String role = role(node);
        result.put("role", role); result.put("ref", Integer.toHexString(System.identityHashCode(node)));
        if (role.equals("menu") || role.equals("menu bar") || role.equals("context menu")) {
            Object children = invoke(node, "getItems");
            if (!(children instanceof Iterable<?>)) children = invoke(node, "getMenus");
            if (children instanceof Iterable<?> iterable) {
                List<Map<String, Object>> snapshots = new java.util.ArrayList<>();
                for (Object child : iterable) snapshots.add(snapshot(child));
                result.put("menu_children", snapshots);
            }
        }
        return result;
    }

    private static String role(Object node) {
        String name = node.getClass().getSimpleName();
        return switch (name) {
            case "Button", "ToggleButton", "CheckBox", "RadioButton", "Hyperlink" -> "button";
            case "TextField", "PasswordField", "TextArea" -> "text";
            case "ComboBox", "ChoiceBox" -> "combo box";
            case "MenuBar" -> "menu bar";
            case "Menu" -> "menu";
            case "MenuItem", "CustomMenuItem" -> "menu item";
            case "CheckMenuItem" -> "check menu item";
            case "RadioMenuItem" -> "radio menu item";
            case "ContextMenu" -> "context menu";
            case "Tab" -> "tab";
            case "Label", "Text" -> "label";
            default -> "custom";
        };
    }

    private static Object eventType(String className, String field) throws ReflectiveOperationException {
        return Class.forName(className).getField(field).get(null);
    }

    private static Object handler(Class<?> type, java.util.function.Consumer<Object> consumer) {
        return Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, (_proxy, method, args) -> { if (method.getName().equals("handle")) consumer.accept(args[0]); return null; });
    }

    private static void addListener(Object property, Runnable action) {
        try {
            Class<?> listener = Class.forName("javafx.beans.InvalidationListener");
            Object proxy = Proxy.newProxyInstance(listener.getClassLoader(), new Class<?>[]{listener}, (_proxy, method, _args) -> { if (method.getName().equals("invalidated")) action.run(); return null; });
            Class.forName("javafx.beans.Observable").getMethod("addListener", listener).invoke(property, proxy);
        } catch (ReflectiveOperationException ignored) { debug("could not attach JavaFX property listener", ignored); }
    }

    private static Object invoke(Object target, String method) {
        if (target == null) return null;
        try { return target.getClass().getMethod(method).invoke(target); }
        catch (ReflectiveOperationException ignored) { return null; }
    }

    private static void offer(Map<String, Object> event) { if (buffer != null) buffer.offer(event); }

    private static void runOnFxThread(Runnable action) {
        try { Class.forName("javafx.application.Platform").getMethod("runLater", Runnable.class).invoke(null, action); }
        catch (ReflectiveOperationException ignored) { /* JavaFX not initialized. */ }
    }

    private static void runOnFxThreadAndWait(Runnable action) {
        try {
            Class<?> platform = Class.forName("javafx.application.Platform");
            if (Boolean.TRUE.equals(platform.getMethod("isFxApplicationThread").invoke(null))) { action.run(); return; }
            CompletableFuture<Void> completed = new CompletableFuture<>();
            platform.getMethod("runLater", Runnable.class).invoke(null, (Runnable) () -> {
                try { action.run(); completed.complete(null); }
                catch (Throwable error) { completed.completeExceptionally(error); }
            });
            completed.get(2, TimeUnit.SECONDS);
        } catch (Exception ignored) { }
    }

    private static void debug(String message, Exception error) {
        if (Boolean.getBoolean("automation.harness.agent.debug")) {
            System.err.println("[automation-harness-agent] " + message + ": " + error);
        }
    }
}
