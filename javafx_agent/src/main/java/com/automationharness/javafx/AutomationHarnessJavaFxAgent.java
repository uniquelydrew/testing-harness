package com.automationharness.javafx;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermission;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.EnumSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Zero-dependency Java agent exposing the live JavaFX scene graph to the
 * Automation Harness over a loopback-only JSON-lines protocol.
 *
 * The agent deliberately links to JavaFX only through reflection so one build
 * works with the JavaFX 21/22 SDKs used by the target applications. In addition
 * to generic Node metadata it exposes application-authored properties,
 * userData, layout constraints, and stable ancestor lineage so automation does
 * not have to rely on JavaFX skin class names or literal tree paths.
 */
public final class AutomationHarnessJavaFxAgent {
    private static final String PROTOCOL = "automation-harness-javafx/1";
    private static final AtomicLong NEXT_REF = new AtomicLong(1L);
    private static final Map<Object, String> REFERENCES = Collections.synchronizedMap(new IdentityHashMap<Object, String>());
    private static final String TOKEN = UUID.randomUUID().toString();
    private static final long PID = ProcessHandle.current().pid();
    private static volatile ServerSocket server;
    private static volatile Path discoveryFile;

    private AutomationHarnessJavaFxAgent() {
    }

    public static void premain(String agentArgs, Instrumentation instrumentation) {
        final Map<String, String> config = parseAgentArgs(agentArgs);
        Thread thread = new Thread(() -> startServer(config), "automation-harness-javafx-agent");
        thread.setDaemon(true);
        thread.start();
    }

    private static void startServer(Map<String, String> config) {
        try {
            int requestedPort = parseInt(config.get("port"), 0);
            ServerSocket socket = new ServerSocket();
            socket.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), requestedPort));
            server = socket;
            Path directory = discoveryDirectory(config);
            writeDiscovery(directory, socket.getLocalPort());
            Runtime.getRuntime().addShutdownHook(new Thread(AutomationHarnessJavaFxAgent::cleanup, "automation-harness-javafx-cleanup"));

            while (!socket.isClosed()) {
                Socket client = socket.accept();
                Thread handler = new Thread(() -> handleClient(client), "automation-harness-javafx-client");
                handler.setDaemon(true);
                handler.start();
            }
        } catch (Throwable error) {
            System.err.println("[automation-harness-javafx] agent startup failed: " + error);
            error.printStackTrace(System.err);
            cleanup();
        }
    }

    private static void handleClient(Socket socket) {
        try (Socket client = socket;
             BufferedReader reader = new BufferedReader(new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
             BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(client.getOutputStream(), StandardCharsets.UTF_8))) {
            String line = reader.readLine();
            if (line == null) {
                return;
            }
            Object parsed = Json.parse(line);
            if (!(parsed instanceof Map)) {
                writeResponse(writer, error("request must be a JSON object"));
                return;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> request = (Map<String, Object>) parsed;
            if (!TOKEN.equals(stringValue(request.get("token")))) {
                writeResponse(writer, error("invalid bridge token"));
                return;
            }
            writeResponse(writer, dispatch(request));
        } catch (Throwable error) {
            try {
                BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8));
                writeResponse(writer, error(error.getClass().getSimpleName() + ": " + error.getMessage()));
            } catch (Throwable ignored) {
                // Client may already have disconnected.
            }
        }
    }

    private static void writeResponse(BufferedWriter writer, Map<String, Object> response) throws IOException {
        writer.write(Json.stringify(response));
        writer.write("\n");
        writer.flush();
    }

    private static Map<String, Object> dispatch(Map<String, Object> request) throws Exception {
        String op = stringValue(request.get("op"));
        if (op == null || op.isEmpty()) {
            return error("request has no op");
        }
        if ("ping".equals(op)) {
            return ok(FxRuntime.ping());
        }
        if ("windows".equals(op)) {
            return ok(FxRuntime.windowsPayload());
        }
        if ("tree".equals(op)) {
            return ok(FxRuntime.treePayload(intValue(request.get("max_depth"), 12)));
        }
        if ("capture_next_click".equals(op)) {
            return ok(singleton("node", FxRuntime.captureNextClick(longValue(request.get("timeout_ms"), 30000L))));
        }
        if ("hit_test".equals(op)) {
            double x = doubleValue(request.get("x"), Double.NaN);
            double y = doubleValue(request.get("y"), Double.NaN);
            if (Double.isNaN(x) || Double.isNaN(y)) {
                return error("hit_test requires numeric x and y");
            }
            Map<String, Object> node = FxRuntime.hitTest(x, y);
            return node == null ? error("no JavaFX node at requested point") : ok(singleton("node", node));
        }
        if ("find".equals(op)) {
            return ok(FxRuntime.findPayload(mapValue(request.get("identification"))));
        }
        if ("state".equals(op)) {
            return ok(singleton("node", FxRuntime.findUnique(mapValue(request.get("identification")))));
        }
        if ("activate_window".equals(op)) {
            return ok(FxRuntime.activateWindow(mapValue(request.get("identification"))));
        }
        if ("focus".equals(op)) {
            return ok(FxRuntime.focus(mapValue(request.get("identification"))));
        }
        if ("activate".equals(op)) {
            return ok(FxRuntime.activate(mapValue(request.get("identification"))));
        }
        if ("get_text".equals(op)) {
            return ok(singleton("text", FxRuntime.getText(mapValue(request.get("identification")))));
        }
        if ("set_text".equals(op)) {
            return ok(FxRuntime.setText(mapValue(request.get("identification")), stringValue(request.get("value"))));
        }
        if ("select_menu_path".equals(op)) {
            return ok(FxRuntime.selectMenuPath(
                    mapValue(request.get("identification")), FxRuntime.listValue(request.get("selectors"))));
        }
        return error("unsupported op: " + op);
    }

    private static Map<String, Object> ok(Map<String, Object> payload) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("ok", Boolean.TRUE);
        result.put("protocol", PROTOCOL);
        result.put("pid", PID);
        result.putAll(payload);
        return result;
    }

    private static Map<String, Object> error(String message) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("ok", Boolean.FALSE);
        result.put("protocol", PROTOCOL);
        result.put("pid", PID);
        result.put("error", message == null ? "unknown error" : message);
        return result;
    }

    private static Map<String, Object> singleton(String key, Object value) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put(key, value);
        return result;
    }

    private static Path discoveryDirectory(Map<String, String> config) {
        String configured = config.get("discovery");
        if (configured == null || configured.trim().isEmpty()) {
            configured = System.getenv("AUTOMATION_HARNESS_JAVAFX_DISCOVERY_DIR");
        }
        if (configured == null || configured.trim().isEmpty()) {
            configured = "/tmp/automation-harness-javafx";
        }
        return Paths.get(configured).toAbsolutePath().normalize();
    }

    private static void writeDiscovery(Path directory, int port) throws IOException {
        Files.createDirectories(directory);
        try {
            Files.setPosixFilePermissions(directory, EnumSet.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE,
                    PosixFilePermission.OWNER_EXECUTE));
        } catch (UnsupportedOperationException ignored) {
        }

        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("protocol", PROTOCOL);
        payload.put("pid", PID);
        payload.put("host", "127.0.0.1");
        payload.put("port", port);
        payload.put("token", TOKEN);
        payload.put("java_version", System.getProperty("java.version"));
        payload.put("started_at", Instant.now().toString());
        payload.put("command", System.getProperty("sun.java.command"));

        Path target = directory.resolve("javafx-" + PID + ".json");
        Files.write(target, (Json.stringify(payload) + "\n").getBytes(StandardCharsets.UTF_8));
        try {
            Files.setPosixFilePermissions(target, EnumSet.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE));
        } catch (UnsupportedOperationException ignored) {
        }
        discoveryFile = target;
    }

    private static void cleanup() {
        try {
            ServerSocket socket = server;
            if (socket != null && !socket.isClosed()) {
                socket.close();
            }
        } catch (Throwable ignored) {
        }
        try {
            Path file = discoveryFile;
            if (file != null) {
                Files.deleteIfExists(file);
            }
        } catch (Throwable ignored) {
        }
    }

    private static Map<String, String> parseAgentArgs(String raw) {
        Map<String, String> values = new LinkedHashMap<String, String>();
        if (raw == null || raw.trim().isEmpty()) {
            return values;
        }
        for (String item : raw.split(",")) {
            int separator = item.indexOf('=');
            if (separator < 0) {
                values.put(item.trim(), "true");
            } else {
                values.put(item.substring(0, separator).trim(), item.substring(separator + 1).trim());
            }
        }
        return values;
    }

    private static int parseInt(String value, int fallback) {
        try {
            return value == null ? fallback : Integer.parseInt(value);
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    private static String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> mapValue(Object value) {
        if (value == null) {
            return new LinkedHashMap<String, Object>();
        }
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected JSON object");
        }
        return (Map<String, Object>) value;
    }

    private static int intValue(Object value, int fallback) {
        return value instanceof Number ? ((Number) value).intValue() : fallback;
    }

    private static long longValue(Object value, long fallback) {
        return value instanceof Number ? ((Number) value).longValue() : fallback;
    }

    private static double doubleValue(Object value, double fallback) {
        return value instanceof Number ? ((Number) value).doubleValue() : fallback;
    }

    private static final class FxRuntime {
        private static final String PLATFORM = "javafx.application.Platform";
        private static final String WINDOW = "javafx.stage.Window";
        private static final String PARENT = "javafx.scene.Parent";
        private static final String NODE = "javafx.scene.Node";
        private static final String MOUSE_EVENT = "javafx.scene.input.MouseEvent";
        private static final String EVENT_HANDLER = "javafx.event.EventHandler";
        private static final Set<String> INSTALLED_SCENES = Collections.newSetFromMap(new ConcurrentHashMap<String, Boolean>());
        private static final Set<String> INTERACTION_BOUNDARIES = Collections.unmodifiableSet(
                new java.util.HashSet<String>(java.util.Arrays.asList(
                        "Button", "ToggleButton", "CheckBox", "RadioButton", "Hyperlink",
                        "TextField", "PasswordField", "TextArea", "ComboBox", "ChoiceBox",
                        "Spinner", "DatePicker", "Slider", "ListCell", "TableCell",
                        "TreeCell", "MenuBar", "MenuButton", "MenuItem", "MenuItemContainer", "Tab")));

        private FxRuntime() {
        }

        static Map<String, Object> ping() {
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            boolean available = classAvailable(PLATFORM) && classAvailable(WINDOW) && classAvailable(NODE);
            payload.put("javafx_available", available);
            payload.put("java_version", System.getProperty("java.version"));
            payload.put("javafx_version", System.getProperty("javafx.version"));
            payload.put("command", System.getProperty("sun.java.command"));
            if (available) {
                try {
                    payload.put("windows", onFx(() -> windows().size()));
                } catch (Throwable error) {
                    payload.put("windows", 0);
                    payload.put("javafx_error", error.getClass().getSimpleName() + ": " + error.getMessage());
                }
            }
            return payload;
        }

        static Map<String, Object> windowsPayload() throws Exception {
            return onFx(() -> {
                List<Object> values = new ArrayList<Object>();
                int index = 0;
                for (Object window : windows()) {
                    Map<String, Object> item = new LinkedHashMap<String, Object>();
                    item.put("index", index++);
                    item.put("title", windowTitle(window));
                    item.put("showing", boolCall(window, "isShowing", false));
                    Object scene = call(window, "getScene");
                    item.put("scene", scene == null ? null : ref(scene));
                    Object root = scene == null ? null : call(scene, "getRoot");
                    item.put("root", root == null ? null : ref(root));
                    values.add(item);
                }
                return singleton("windows", values);
            });
        }

        static Map<String, Object> treePayload(final int maxDepth) throws Exception {
            return onFx(() -> {
                List<Object> values = new ArrayList<Object>();
                for (Object window : windows()) {
                    Object scene = call(window, "getScene");
                    Object root = scene == null ? null : call(scene, "getRoot");
                    if (root == null) {
                        continue;
                    }
                    Map<String, Object> item = new LinkedHashMap<String, Object>();
                    item.put("title", windowTitle(window));
                    item.put("root", treeNode(root, window, 0, Math.max(0, maxDepth)));
                    values.add(item);
                }
                return singleton("windows", values);
            });
        }

        static Map<String, Object> captureNextClick(long timeoutMs) throws Exception {
            if (!classAvailable(PLATFORM)) {
                throw new IllegalStateException("JavaFX runtime is not loaded in this JVM");
            }
            final AtomicReference<Map<String, Object>> captured = new AtomicReference<Map<String, Object>>();
            final CountDownLatch latch = new CountDownLatch(1);
            final List<SceneFilter> filters = new ArrayList<SceneFilter>();
            final long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(Math.max(1L, timeoutMs));

            while (captured.get() == null && System.nanoTime() < deadline) {
                filters.addAll(onFx(() -> installCaptureFilters(captured, latch)));
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) {
                    break;
                }
                if (latch.await(Math.min(TimeUnit.NANOSECONDS.toMillis(remaining), 100L), TimeUnit.MILLISECONDS)) {
                    break;
                }
            }

            try {
                if (captured.get() == null) {
                    throw new java.util.concurrent.TimeoutException("no JavaFX click captured within " + timeoutMs + " ms");
                }
                return captured.get();
            } finally {
                try {
                    onFx(() -> {
                        for (SceneFilter filter : filters) {
                            filter.remove();
                        }
                        return null;
                    });
                } catch (Throwable ignored) {
                }
            }
        }

        private static List<SceneFilter> installCaptureFilters(
                final AtomicReference<Map<String, Object>> captured,
                final CountDownLatch latch) throws Exception {
            List<SceneFilter> installed = new ArrayList<SceneFilter>();
            Class<?> handlerClass = Class.forName(EVENT_HANDLER);
            Class<?> mouseClass = Class.forName(MOUSE_EVENT);
            Object mousePressed = mouseClass.getField("MOUSE_PRESSED").get(null);

            for (Object window : windows()) {
                Object scene = call(window, "getScene");
                if (scene == null) {
                    continue;
                }
                final String key = ref(scene) + ":" + System.identityHashCode(captured);
                if (!INSTALLED_SCENES.add(key)) {
                    continue;
                }
                final Object currentWindow = window;
                InvocationHandler invocation = (proxy, method, args) -> {
                    if ("handle".equals(method.getName()) && args != null && args.length == 1 && captured.get() == null) {
                        Object target = call(args[0], "getTarget");
                        Object semantic = nearestNode(target);
                        if (semantic != null) {
                            Map<String, Object> payload = nodePayload(semantic, currentWindow);
                            payload.put("capture_source", "javafx-event-filter");
                            if (captured.compareAndSet(null, payload)) {
                                latch.countDown();
                            }
                        }
                    }
                    return null;
                };
                Object handler = Proxy.newProxyInstance(handlerClass.getClassLoader(), new Class<?>[]{handlerClass}, invocation);
                Method add = scene.getClass().getMethod("addEventFilter", Class.forName("javafx.event.EventType"), handlerClass);
                add.invoke(scene, mousePressed, handler);
                installed.add(new SceneFilter(scene, mousePressed, handler, key));
            }
            return installed;
        }

        static Map<String, Object> hitTest(final double x, final double y) throws Exception {
            return onFx(() -> {
                Object best = null;
                Object bestWindow = null;
                double bestArea = Double.POSITIVE_INFINITY;
                for (Object window : windows()) {
                    Object scene = call(window, "getScene");
                    Object root = scene == null ? null : call(scene, "getRoot");
                    if (root == null) {
                        continue;
                    }
                    for (Object node : flatten(root)) {
                        if (!boolCall(node, "isVisible", true)) {
                            continue;
                        }
                        double[] bounds = boundsOnScreen(node);
                        if (bounds == null || !contains(bounds, x, y)) {
                            continue;
                        }
                        double area = bounds[2] * bounds[3];
                        if (area <= bestArea) {
                            best = node;
                            bestWindow = window;
                            bestArea = area;
                        }
                    }
                }
                return best == null ? null : nodePayload(best, bestWindow);
            });
        }

        static Map<String, Object> findPayload(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                Resolution resolution = resolve(identification, false);
                List<Object> matches = new ArrayList<Object>();
                for (NodeMatch match : resolution.matches) {
                    matches.add(nodePayload(match.node, match.window));
                }
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("matches", matches);
                result.put("match_count", matches.size());
                result.put("stages", resolution.stages);
                return result;
            });
        }

        static Map<String, Object> findUnique(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                Resolution resolution = resolve(identification, true);
                NodeMatch match = unique(resolution, identification);
                Map<String, Object> payload = nodePayload(match.node, match.window);
                payload.put("resolution_stages", resolution.stages);
                return payload;
            });
        }

        static Map<String, Object> activateWindow(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                NodeMatch match = unique(resolve(identification, true), identification);
                Method toFront = findMethod(match.window.getClass(), "toFront");
                Method requestFocus = findMethod(match.window.getClass(), "requestFocus");
                if (toFront == null || toFront.getParameterCount() != 0
                        || requestFocus == null || requestFocus.getParameterCount() != 0) {
                    throw new UnsupportedOperationException(
                            "JavaFX owning window does not expose toFront()/requestFocus()");
                }
                toFront.invoke(match.window);
                requestFocus.invoke(match.window);
                boolean focused = boolCall(match.window, "isFocused", false);
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("operation", "activate_window");
                result.put("window", windowTitle(match.window));
                result.put("focused", focused);
                return result;
            });
        }

        static Map<String, Object> focus(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                NodeMatch match = unique(resolve(identification, true), identification);
                Method requestFocus = findMethod(match.node.getClass(), "requestFocus");
                if (requestFocus == null || requestFocus.getParameterCount() != 0) {
                    throw new UnsupportedOperationException(
                            "JavaFX node does not expose requestFocus(): " + match.node.getClass().getName());
                }
                requestFocus.invoke(match.node);
                boolean focused = boolCall(match.node, "isFocused", false);
                if (!focused) {
                    throw new IllegalStateException("JavaFX node did not accept focus");
                }
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("operation", "focus");
                result.put("focused", Boolean.TRUE);
                result.put("node", nodePayload(match.node, match.window));
                return result;
            });
        }

        static Map<String, Object> activate(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                NodeMatch match = unique(resolve(identification, true), identification);
                Method fire = findMethod(match.node.getClass(), "fire");
                if (fire == null || fire.getParameterCount() != 0) {
                    throw new UnsupportedOperationException("JavaFX node has no semantic fire() action: " + match.node.getClass().getName());
                }
                fire.invoke(match.node);
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("action", "fire");
                result.put("node", nodePayload(match.node, match.window));
                return result;
            });
        }

        static String getText(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                NodeMatch match = unique(resolve(identification, true), identification);
                Method method = findMethod(match.node.getClass(), "getText");
                if (method == null || method.getParameterCount() != 0) {
                    throw new UnsupportedOperationException("JavaFX node has no getText() operation");
                }
                Object value = method.invoke(match.node);
                return value == null ? "" : String.valueOf(value);
            });
        }

        static Map<String, Object> setText(final Map<String, Object> identification, final String value) throws Exception {
            return onFx(() -> {
                NodeMatch match = unique(resolve(identification, true), identification);
                Method method = findCompatibleMethod(match.node.getClass(), "setText", String.class);
                if (method == null) {
                    throw new UnsupportedOperationException("JavaFX node has no setText(String) operation");
                }
                method.invoke(match.node, value == null ? "" : value);
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("action", "set_text");
                result.put("node", nodePayload(match.node, match.window));
                return result;
            });
        }

        static Map<String, Object> selectMenuPath(
                final Map<String, Object> identification,
                final List<Object> selectors) throws Exception {
            return onFx(() -> {
                if (selectors.isEmpty()) {
                    throw new IllegalArgumentException("menu path must not be empty");
                }
                NodeMatch root = unique(resolve(identification, true), identification);
                Object current = root.node;
                List<Object> traversed = new ArrayList<Object>();
                for (int index = 0; index < selectors.size(); index++) {
                    if (!(selectors.get(index) instanceof Map)) {
                        throw new IllegalArgumentException("menu path selector must be an object");
                    }
                    @SuppressWarnings("unchecked")
                    Map<String, Object> selector = (Map<String, Object>) selectors.get(index);
                    Object child = menuChild(current, selector);
                    boolean terminal = index == selectors.size() - 1;
                    Method operation = findMethod(child.getClass(), terminal ? "fire" : "show");
                    if (operation == null || operation.getParameterCount() != 0) {
                        throw new UnsupportedOperationException(
                                "JavaFX menu segment has no " + (terminal ? "fire()" : "show()")
                                        + ": " + child.getClass().getName());
                    }
                    operation.invoke(child);
                    traversed.add(menuSnapshot(child));
                    current = child;
                }
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("action", "select_menu_item");
                result.put("path", traversed);
                return result;
            });
        }

        private static Object menuChild(Object parent, Map<String, Object> selector) throws Exception {
            List<Object> children = menuChildren(parent);
            Map<String, Object> criteria = mapValueOrEmpty(selector.get("criteria"));
            Integer ordinal = selector.get("ordinal") instanceof Number
                    ? ((Number) selector.get("ordinal")).intValue() : null;
            if (ordinal != null && ordinal >= 0 && ordinal < children.size()
                    && payloadMatches(menuSnapshot(children.get(ordinal)), criteria)) {
                return children.get(ordinal);
            }
            List<Object> matches = new ArrayList<Object>();
            for (Object child : children) {
                if (payloadMatches(menuSnapshot(child), criteria)) {
                    matches.add(child);
                }
            }
            if (matches.size() != 1) {
                throw new IllegalArgumentException(
                        "JavaFX menu selector matched " + matches.size() + " children: " + criteria);
            }
            return matches.get(0);
        }

        private static List<Object> menuChildren(Object parent) throws Exception {
            Method method = findMethod(parent.getClass(), "getMenus");
            if (method == null || method.getParameterCount() != 0) {
                method = findMethod(parent.getClass(), "getItems");
            }
            if (method == null || method.getParameterCount() != 0) {
                throw new UnsupportedOperationException(
                        "JavaFX object is not a MenuBar, Menu, or ContextMenu: " + parent.getClass().getName());
            }
            return listValue(method.invoke(parent));
        }

        private static Map<String, Object> menuSnapshot(Object item) throws Exception {
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("class", item.getClass().getName());
            payload.put("simple_class", item.getClass().getSimpleName());
            payload.put("id", stringOrNull(call(item, "getId")));
            payload.put("accessible_id", stringOrNull(call(item, "getId")));
            payload.put("text", optionalNoArgString(item, "getText"));
            payload.put("name", optionalNoArgString(item, "getText"));
            String simple = item.getClass().getSimpleName();
            String role = "Menu".equals(simple) ? "menu"
                    : "CheckMenuItem".equals(simple) ? "check menu item"
                    : "RadioMenuItem".equals(simple) ? "radio menu item" : "menu item";
            payload.put("role", role);
            payload.put("accessible_role", role);
            List<Object> children = menuChildrenIfPresent(item);
            if (!children.isEmpty()) {
                List<Object> snapshots = new ArrayList<Object>();
                for (Object child : children) snapshots.add(menuSnapshot(child));
                payload.put("menu_children", snapshots);
            }
            return payload;
        }

        private static List<Object> menuChildrenIfPresent(Object parent) throws Exception {
            if (!isInstance("javafx.scene.control.MenuBar", parent)
                    && !isInstance("javafx.scene.control.Menu", parent)
                    && !isInstance("javafx.scene.control.ContextMenu", parent)) {
                return Collections.emptyList();
            }
            Method method = findMethod(parent.getClass(), "getMenus");
            if (method == null || method.getParameterCount() != 0) method = findMethod(parent.getClass(), "getItems");
            return method == null || method.getParameterCount() != 0
                    ? Collections.emptyList() : listValue(method.invoke(parent));
        }

        private static boolean isInstance(String className, Object value) {
            try {
                return value != null && Class.forName(className).isInstance(value);
            } catch (ClassNotFoundException ignored) {
                return false;
            }
        }

        private static NodeMatch unique(Resolution resolution, Map<String, Object> identification) {
            if (resolution.matches.isEmpty()) {
                throw new IllegalArgumentException("JavaFX component not found: " + identification);
            }
            if (resolution.matches.size() != 1) {
                throw new IllegalArgumentException("JavaFX component is ambiguous: " + identification + " (" + resolution.matches.size() + " matches)");
            }
            return resolution.matches.get(0);
        }

        private static Resolution resolve(Map<String, Object> identification, boolean applyOrdinal) throws Exception {
            Map<String, Object> mandatory = mapOrSelf(identification.get("mandatory"), identification);
            Map<String, Object> assistive = mapValueOrEmpty(identification.get("assistive"));
            Integer ordinal = identification.get("ordinal") instanceof Number ? ((Number) identification.get("ordinal")).intValue() : null;

            List<NodeMatch> matches = filter(allNodeMatches(), mandatory);
            List<Object> stages = new ArrayList<Object>();
            stages.add(stage("mandatory", mandatory, matches.size()));
            for (Map.Entry<String, Object> entry : assistive.entrySet()) {
                if (applyOrdinal && matches.size() <= 1) {
                    break;
                }
                Map<String, Object> criterion = new LinkedHashMap<String, Object>();
                criterion.put(entry.getKey(), entry.getValue());
                matches = filter(matches, criterion);
                stages.add(stage("assistive:" + entry.getKey(), criterion, matches.size()));
            }
            if (applyOrdinal && matches.size() > 1 && ordinal != null) {
                if (ordinal < 0 || ordinal >= matches.size()) {
                    throw new IllegalArgumentException("JavaFX ordinal " + ordinal + " outside " + matches.size() + " matches");
                }
                NodeMatch selected = matches.get(ordinal);
                matches = new ArrayList<NodeMatch>();
                matches.add(selected);
                stages.add(stage("ordinal", singleton("ordinal", ordinal), 1));
            }
            return new Resolution(matches, stages);
        }

        private static List<NodeMatch> filter(List<NodeMatch> input, Map<String, Object> criteria) throws Exception {
            List<NodeMatch> output = new ArrayList<NodeMatch>();
            for (NodeMatch match : input) {
                if (matches(match, criteria)) {
                    output.add(match);
                }
            }
            return output;
        }

        private static boolean matches(NodeMatch match, Map<String, Object> criteria) throws Exception {
            Map<String, Object> node = nodePayload(match.node, match.window);
            for (Map.Entry<String, Object> entry : criteria.entrySet()) {
                String key = entry.getKey();
                Object expected = entry.getValue();
                if ("parent".equals(key)) {
                    Object parent = call(match.node, "getParent");
                    if (parent == null || !(expected instanceof Map)) {
                        return false;
                    }
                    @SuppressWarnings("unchecked")
                    Map<String, Object> parentCriteria = (Map<String, Object>) expected;
                    if (!payloadMatches(briefPayload(parent), parentCriteria)) {
                        return false;
                    }
                    continue;
                }
                if ("ancestor".equals(key)) {
                    if (!(expected instanceof Map) || !hasMatchingAncestor(match.node, expected)) {
                        return false;
                    }
                    continue;
                }
                if ("lineage".equals(key)) {
                    if (!(expected instanceof List) || !lineageMatches(match.node, (List<?>) expected)) {
                        return false;
                    }
                    continue;
                }
                if (!valueMatches(node.get(key), expected)) {
                    return false;
                }
            }
            return true;
        }

        @SuppressWarnings("unchecked")
        private static boolean hasMatchingAncestor(Object node, Object rawExpected) throws Exception {
            Map<String, Object> expected = (Map<String, Object>) rawExpected;
            Object current = call(node, "getParent");
            int guard = 0;
            while (current != null && guard++ < 64) {
                if (payloadMatches(briefPayload(current), expected)) {
                    return true;
                }
                current = call(current, "getParent");
            }
            return false;
        }

        @SuppressWarnings("unchecked")
        private static boolean lineageMatches(Object node, List<?> expected) throws Exception {
            if (expected.isEmpty()) {
                return true;
            }
            List<Object> actual = stableAncestors(node);
            int cursor = 0;
            for (Object candidate : actual) {
                if (!(candidate instanceof Map) || cursor >= expected.size() || !(expected.get(cursor) instanceof Map)) {
                    continue;
                }
                if (payloadMatches((Map<String, Object>) candidate, (Map<String, Object>) expected.get(cursor))) {
                    cursor++;
                }
            }
            return cursor == expected.size();
        }

        private static boolean payloadMatches(Map<String, Object> payload, Map<String, Object> criteria) {
            for (Map.Entry<String, Object> entry : criteria.entrySet()) {
                if (!valueMatches(payload.get(entry.getKey()), entry.getValue())) {
                    return false;
                }
            }
            return true;
        }

        @SuppressWarnings("unchecked")
        private static boolean valueMatches(Object actual, Object expected) {
            if (actual == null || expected == null) {
                return actual == expected;
            }
            if (actual instanceof Number && expected instanceof Number) {
                return Double.compare(((Number) actual).doubleValue(), ((Number) expected).doubleValue()) == 0;
            }
            if (actual instanceof Map && expected instanceof Map) {
                return payloadMatches((Map<String, Object>) actual, (Map<String, Object>) expected);
            }
            if (actual instanceof List && expected instanceof List) {
                List<?> left = (List<?>) actual;
                List<?> right = (List<?>) expected;
                if (left.size() != right.size()) {
                    return false;
                }
                for (int index = 0; index < left.size(); index++) {
                    if (!valueMatches(left.get(index), right.get(index))) {
                        return false;
                    }
                }
                return true;
            }
            return actual.equals(expected);
        }

        private static Map<String, Object> stage(String source, Map<String, Object> criteria, int matches) {
            Map<String, Object> stage = new LinkedHashMap<String, Object>();
            stage.put("source", source);
            stage.put("criteria", criteria);
            stage.put("matches", matches);
            return stage;
        }

        private static List<NodeMatch> allNodeMatches() throws Exception {
            List<NodeMatch> result = new ArrayList<NodeMatch>();
            for (Object window : windows()) {
                Object scene = call(window, "getScene");
                Object root = scene == null ? null : call(scene, "getRoot");
                if (root == null) {
                    continue;
                }
                for (Object node : flatten(root)) {
                    result.add(new NodeMatch(node, window));
                }
            }
            return result;
        }

        private static List<Object> flatten(Object root) throws Exception {
            List<Object> result = new ArrayList<Object>();
            List<Object> stack = new ArrayList<Object>();
            stack.add(root);
            while (!stack.isEmpty()) {
                Object node = stack.remove(stack.size() - 1);
                result.add(node);
                List<Object> children = children(node);
                for (int index = children.size() - 1; index >= 0; index--) {
                    stack.add(children.get(index));
                }
            }
            return result;
        }

        private static Map<String, Object> treeNode(Object node, Object window, int depth, int maxDepth) throws Exception {
            Map<String, Object> payload = nodePayload(node, window);
            if (depth >= maxDepth) {
                return payload;
            }
            List<Object> values = new ArrayList<Object>();
            for (Object child : children(node)) {
                values.add(treeNode(child, window, depth + 1, maxDepth));
            }
            if (!values.isEmpty()) {
                payload.put("children", values);
            }
            return payload;
        }

        private static Map<String, Object> nodePayload(Object node, Object window) throws Exception {
            Map<String, Object> payload = briefPayload(node);
            payload.put("window", windowTitle(window));
            payload.put("visible", boolCall(node, "isVisible", true));
            payload.put("disabled", boolCall(node, "isDisable", false));
            payload.put("focused", boolCall(node, "isFocused", false));
            payload.put("managed", boolCall(node, "isManaged", true));
            payload.put("focus_traversable", boolCall(node, "isFocusTraversable", false));
            payload.put("style_classes", listValue(call(node, "getStyleClass")));
            payload.put("bounds", boundsList(boundsOnScreen(node)));
            payload.put("hierarchy", hierarchy(node));
            payload.put("stable_ancestors", stableAncestors(node));
            payload.put("user_data", scalarValue(call(node, "getUserData")));
            payload.put("properties", scalarProperties(node));
            payload.put("layout", layoutConstraints(node));
            payload.put("sibling_index", siblingIndex(node));
            payload.put("sibling_count", siblingCount(node));
            payload.put("actions", actions(node));
            Object parent = call(node, "getParent");
            if (parent != null) {
                payload.put("parent", briefPayload(parent));
            }
            List<Object> menuChildren = menuChildrenIfPresent(node);
            if (!menuChildren.isEmpty()) {
                List<Object> snapshots = new ArrayList<Object>();
                for (Object child : menuChildren) snapshots.add(menuSnapshot(child));
                payload.put("menu_children", snapshots);
            }
            return payload;
        }

        private static Map<String, Object> briefPayload(Object node) throws Exception {
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("ref", ref(node));
            payload.put("class", node.getClass().getName());
            payload.put("simple_class", node.getClass().getSimpleName());
            payload.put("id", stringOrNull(call(node, "getId")));
            payload.put("accessible_role", enumName(call(node, "getAccessibleRole")));
            payload.put("accessible_text", stringOrNull(call(node, "getAccessibleText")));
            payload.put("accessible_help", stringOrNull(call(node, "getAccessibleHelp")));
            payload.put("text", optionalNoArgString(node, "getText"));
            return payload;
        }

        private static Map<String, Object> scalarProperties(Object node) {
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            try {
                Object raw = call(node, "getProperties");
                if (!(raw instanceof Map)) {
                    return result;
                }
                for (Object entryObject : ((Map<?, ?>) raw).entrySet()) {
                    Map.Entry<?, ?> entry = (Map.Entry<?, ?>) entryObject;
                    if (entry.getKey() == null) {
                        continue;
                    }
                    Object scalar = scalarValue(entry.getValue());
                    if (scalar != null) {
                        result.put(String.valueOf(entry.getKey()), scalar);
                    }
                }
            } catch (Throwable ignored) {
            }
            return result;
        }

        private static Object scalarValue(Object value) {
            if (value == null) {
                return null;
            }
            if (value instanceof String || value instanceof Number || value instanceof Boolean) {
                return value;
            }
            if (value instanceof Character) {
                return String.valueOf(value);
            }
            if (value instanceof Enum) {
                return ((Enum<?>) value).name();
            }
            return null;
        }

        private static boolean hasSemanticProperty(Object node) {
            try {
                Object userData = scalarValue(call(node, "getUserData"));
                if (userData != null && !String.valueOf(userData).isEmpty()) {
                    return true;
                }
                for (String key : scalarProperties(node).keySet()) {
                    String folded = key.toLowerCase(java.util.Locale.ROOT);
                    if (folded.startsWith("automation.") || folded.startsWith("test.") || folded.startsWith("qa.")) {
                        return true;
                    }
                }
            } catch (Throwable ignored) {
            }
            return false;
        }

        private static Map<String, Object> layoutConstraints(Object node) {
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            try {
                Object parent = call(node, "getParent");
                if (parent != null && Class.forName("javafx.scene.layout.GridPane").isInstance(parent)) {
                    Integer row = integerStaticNodeCall("javafx.scene.layout.GridPane", "getRowIndex", node);
                    Integer column = integerStaticNodeCall("javafx.scene.layout.GridPane", "getColumnIndex", node);
                    Integer rowSpan = integerStaticNodeCall("javafx.scene.layout.GridPane", "getRowSpan", node);
                    Integer columnSpan = integerStaticNodeCall("javafx.scene.layout.GridPane", "getColumnSpan", node);
                    result.put("grid_row", row == null ? 0 : row);
                    result.put("grid_column", column == null ? 0 : column);
                    result.put("grid_row_span", rowSpan == null ? 1 : rowSpan);
                    result.put("grid_column_span", columnSpan == null ? 1 : columnSpan);
                }
            } catch (Throwable ignored) {
            }
            return result;
        }

        private static Integer integerStaticNodeCall(String className, String methodName, Object node) throws Exception {
            Class<?> type = Class.forName(className);
            Class<?> nodeClass = Class.forName(NODE);
            Object value = type.getMethod(methodName, nodeClass).invoke(null, node);
            return value instanceof Number ? ((Number) value).intValue() : null;
        }

        private static int siblingIndex(Object node) {
            try {
                Object parent = call(node, "getParent");
                if (parent == null) {
                    return 0;
                }
                List<Object> siblings = children(parent);
                for (int index = 0; index < siblings.size(); index++) {
                    if (siblings.get(index) == node) {
                        return index;
                    }
                }
            } catch (Throwable ignored) {
            }
            return 0;
        }

        private static int siblingCount(Object node) {
            try {
                Object parent = call(node, "getParent");
                return parent == null ? 1 : children(parent).size();
            } catch (Throwable ignored) {
                return 1;
            }
        }

        private static List<Object> stableAncestors(Object node) throws Exception {
            List<Object> result = new ArrayList<Object>();
            Object current = call(node, "getParent");
            int guard = 0;
            while (current != null && guard++ < 64) {
                Map<String, Object> descriptor = stableDescriptor(current);
                if (!descriptor.isEmpty()) {
                    result.add(0, descriptor);
                }
                current = call(current, "getParent");
            }
            return result;
        }

        private static Map<String, Object> stableDescriptor(Object node) throws Exception {
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            String className = node.getClass().getName();
            String id = stringOrNull(call(node, "getId"));
            String accessibleText = stringOrNull(call(node, "getAccessibleText"));
            String text = optionalNoArgString(node, "getText");
            String role = enumName(call(node, "getAccessibleRole"));
            boolean applicationClass = isApplicationClass(className);

            if (id != null && !id.isEmpty()) {
                result.put("id", id);
            }
            if (accessibleText != null && !accessibleText.isEmpty()) {
                result.put("accessible_text", accessibleText);
            } else if (text != null && !text.isEmpty()) {
                result.put("text", text);
            }
            if (applicationClass || !result.isEmpty()) {
                if (!isInternalJavaFxClass(className)) {
                    result.put("class", className);
                }
                if (role != null && !"PARENT".equals(role) && !"NODE".equals(role)) {
                    result.put("accessible_role", role);
                }
            }
            if (applicationClass && result.isEmpty()) {
                result.put("class", className);
            }
            return result;
        }

        private static boolean isApplicationClass(String className) {
            return className != null
                    && !className.startsWith("java.")
                    && !className.startsWith("javax.")
                    && !className.startsWith("javafx.")
                    && !className.startsWith("com.sun.");
        }

        private static boolean isInternalJavaFxClass(String className) {
            return className != null && (className.startsWith("com.sun.javafx.") || className.startsWith("com.sun.glass."));
        }

        private static List<Object> actions(Object node) {
            List<Object> result = new ArrayList<Object>();
            Method fire = findMethod(node.getClass(), "fire");
            if (fire != null && fire.getParameterCount() == 0) {
                result.add("activate");
                result.add("click");
            }
            if (findCompatibleMethod(node.getClass(), "setText", String.class) != null) {
                result.add("set_text");
            }
            Method getText = findMethod(node.getClass(), "getText");
            if (getText != null && getText.getParameterCount() == 0) {
                result.add("get_text");
            }
            return result;
        }

        private static List<Object> hierarchy(Object node) throws Exception {
            List<Object> values = new ArrayList<Object>();
            Object current = node;
            int guard = 0;
            while (current != null && guard++ < 64) {
                String id = stringOrNull(call(current, "getId"));
                String label = current.getClass().getSimpleName();
                if (id != null && !id.isEmpty()) {
                    label += "#" + id;
                }
                values.add(0, label);
                current = call(current, "getParent");
            }
            return values;
        }

        private static Object nearestNode(Object target) throws Exception {
            if (target == null) {
                return null;
            }
            Class<?> nodeClass = Class.forName(NODE);
            Object current = target;
            while (current != null) {
                if (nodeClass.isInstance(current)) {
                    return semanticNode(current);
                }
                Method parent = findMethod(current.getClass(), "getParent");
                current = parent == null ? null : parent.invoke(current);
            }
            return null;
        }

        private static Object semanticNode(Object node) throws Exception {
            Object current = node;
            Object fallback = node;
            for (int depth = 0; current != null && depth < 64; depth++) {
                if (isInteractionBoundary(current)) {
                    return current;
                }
                current = call(current, "getParent");
            }
            return fallback;
        }

        private static boolean isInteractionBoundary(Object node) {
            Class<?> type = node.getClass();
            boolean controlSubclass = false;
            while (type != null) {
                if (INTERACTION_BOUNDARIES.contains(type.getSimpleName())) return true;
                if ("Control".equals(type.getSimpleName())) controlSubclass = true;
                type = type.getSuperclass();
            }
            if (controlSubclass && isApplicationClass(node.getClass().getName())) return true;
            if (hasSemanticProperty(node)) return true;
            Method handler = findMethod(node.getClass(), "getOnMouseClicked");
            if (handler != null && handler.getParameterCount() == 0) {
                try {
                    return handler.invoke(node) != null;
                } catch (Throwable ignored) {
                }
            }
            return false;
        }

        private static List<Object> windows() throws Exception {
            Class<?> windowClass = Class.forName(WINDOW);
            return listValue(windowClass.getMethod("getWindows").invoke(null));
        }

        private static List<Object> children(Object node) throws Exception {
            Class<?> parentClass = Class.forName(PARENT);
            if (!parentClass.isInstance(node)) {
                return Collections.emptyList();
            }
            return listValue(parentClass.getMethod("getChildrenUnmodifiable").invoke(node));
        }

        private static String windowTitle(Object window) {
            if (window == null) {
                return null;
            }
            Method method = findMethod(window.getClass(), "getTitle");
            if (method == null) {
                return window.getClass().getSimpleName();
            }
            try {
                Object value = method.invoke(window);
                return value == null ? null : String.valueOf(value);
            } catch (Throwable ignored) {
                return null;
            }
        }

        private static double[] boundsOnScreen(Object node) {
            try {
                Object bounds = call(node, "getBoundsInLocal");
                Method localToScreen = node.getClass().getMethod("localToScreen", Class.forName("javafx.geometry.Bounds"));
                Object screen = localToScreen.invoke(node, bounds);
                if (screen == null) {
                    return null;
                }
                return new double[]{
                        numberCall(screen, "getMinX"),
                        numberCall(screen, "getMinY"),
                        numberCall(screen, "getWidth"),
                        numberCall(screen, "getHeight")
                };
            } catch (Throwable ignored) {
                return null;
            }
        }

        private static boolean contains(double[] bounds, double x, double y) {
            return x >= bounds[0] && y >= bounds[1]
                    && x < bounds[0] + bounds[2] && y < bounds[1] + bounds[3];
        }

        private static List<Object> boundsList(double[] values) {
            if (values == null) {
                return null;
            }
            List<Object> result = new ArrayList<Object>();
            for (double value : values) {
                result.add(value);
            }
            return result;
        }

        private static String ref(Object value) {
            synchronized (REFERENCES) {
                String current = REFERENCES.get(value);
                if (current == null) {
                    current = "n" + NEXT_REF.getAndIncrement();
                    REFERENCES.put(value, current);
                }
                return current;
            }
        }

        private static boolean classAvailable(String name) {
            try {
                Class.forName(name, false, ClassLoader.getSystemClassLoader());
                return true;
            } catch (Throwable ignored) {
                return false;
            }
        }

        private static <T> T onFx(Callable<T> callable) throws Exception {
            Class<?> platform = Class.forName(PLATFORM);
            boolean onThread = Boolean.TRUE.equals(platform.getMethod("isFxApplicationThread").invoke(null));
            if (onThread) {
                return callable.call();
            }
            final AtomicReference<T> value = new AtomicReference<T>();
            final AtomicReference<Throwable> failure = new AtomicReference<Throwable>();
            final CountDownLatch latch = new CountDownLatch(1);
            Runnable runnable = () -> {
                try {
                    value.set(callable.call());
                } catch (Throwable error) {
                    failure.set(error);
                } finally {
                    latch.countDown();
                }
            };
            try {
                platform.getMethod("runLater", Runnable.class).invoke(null, runnable);
            } catch (Throwable error) {
                throw new IllegalStateException("JavaFX toolkit is not running", error);
            }
            if (!latch.await(10, TimeUnit.SECONDS)) {
                throw new java.util.concurrent.TimeoutException("JavaFX application thread did not respond within 10 seconds");
            }
            if (failure.get() != null) {
                Throwable error = failure.get();
                if (error instanceof Exception) {
                    throw (Exception) error;
                }
                throw new RuntimeException(error);
            }
            return value.get();
        }

        private static Object call(Object target, String method) throws Exception {
            if (target == null) {
                return null;
            }
            Method resolved = findMethod(target.getClass(), method);
            if (resolved == null || resolved.getParameterCount() != 0) {
                return null;
            }
            return resolved.invoke(target);
        }

        private static boolean boolCall(Object target, String method, boolean fallback) {
            try {
                Object value = call(target, method);
                return value instanceof Boolean ? ((Boolean) value).booleanValue() : fallback;
            } catch (Throwable ignored) {
                return fallback;
            }
        }

        private static double numberCall(Object target, String method) throws Exception {
            Object value = call(target, method);
            return value instanceof Number ? ((Number) value).doubleValue() : 0.0;
        }

        private static Method findMethod(Class<?> type, String name) {
            for (Method method : type.getMethods()) {
                if (method.getName().equals(name)) {
                    return method;
                }
            }
            return null;
        }

        private static Method findCompatibleMethod(Class<?> type, String name, Class<?> parameter) {
            for (Method method : type.getMethods()) {
                if (!method.getName().equals(name) || method.getParameterCount() != 1) {
                    continue;
                }
                if (method.getParameterTypes()[0].isAssignableFrom(parameter)
                        || parameter.isAssignableFrom(method.getParameterTypes()[0])) {
                    return method;
                }
            }
            return null;
        }

        private static String optionalNoArgString(Object target, String method) {
            Method resolved = findMethod(target.getClass(), method);
            if (resolved == null || resolved.getParameterCount() != 0) {
                return null;
            }
            try {
                return stringOrNull(resolved.invoke(target));
            } catch (Throwable ignored) {
                return null;
            }
        }

        private static String stringOrNull(Object value) {
            return value == null ? null : String.valueOf(value);
        }

        private static String enumName(Object value) {
            if (value == null) {
                return null;
            }
            return value instanceof Enum ? ((Enum<?>) value).name() : String.valueOf(value);
        }

        private static List<Object> listValue(Object value) {
            if (value == null) {
                return new ArrayList<Object>();
            }
            if (value instanceof Collection) {
                return new ArrayList<Object>((Collection<?>) value);
            }
            if (value instanceof Iterable) {
                List<Object> result = new ArrayList<Object>();
                for (Object item : (Iterable<?>) value) {
                    result.add(item);
                }
                return result;
            }
            return new ArrayList<Object>();
        }

        @SuppressWarnings("unchecked")
        private static Map<String, Object> mapOrSelf(Object candidate, Map<String, Object> fallback) {
            return candidate instanceof Map ? (Map<String, Object>) candidate : fallback;
        }

        @SuppressWarnings("unchecked")
        private static Map<String, Object> mapValueOrEmpty(Object candidate) {
            return candidate instanceof Map ? (Map<String, Object>) candidate : new LinkedHashMap<String, Object>();
        }

        private static final class SceneFilter {
            final Object scene;
            final Object eventType;
            final Object handler;
            final String key;

            SceneFilter(Object scene, Object eventType, Object handler, String key) {
                this.scene = scene;
                this.eventType = eventType;
                this.handler = handler;
                this.key = key;
            }

            void remove() {
                try {
                    Class<?> handlerClass = Class.forName(EVENT_HANDLER);
                    Method method = scene.getClass().getMethod("removeEventFilter", Class.forName("javafx.event.EventType"), handlerClass);
                    method.invoke(scene, eventType, handler);
                } catch (Throwable ignored) {
                } finally {
                    INSTALLED_SCENES.remove(key);
                }
            }
        }

        private static final class NodeMatch {
            final Object node;
            final Object window;

            NodeMatch(Object node, Object window) {
                this.node = node;
                this.window = window;
            }
        }

        private static final class Resolution {
            final List<NodeMatch> matches;
            final List<Object> stages;

            Resolution(List<NodeMatch> matches, List<Object> stages) {
                this.matches = matches;
                this.stages = stages;
            }
        }
    }

    /** Minimal JSON parser/serializer sufficient for the bridge protocol. */
    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.value();
            parser.space();
            if (!parser.end()) {
                throw new IllegalArgumentException("trailing JSON content at " + parser.index);
            }
            return value;
        }

        static String stringify(Object value) {
            StringBuilder out = new StringBuilder();
            write(out, value);
            return out.toString();
        }

        private static void write(StringBuilder out, Object value) {
            if (value == null) {
                out.append("null");
                return;
            }
            if (value instanceof String || value instanceof Character) {
                string(out, String.valueOf(value));
                return;
            }
            if (value instanceof Number || value instanceof Boolean) {
                out.append(String.valueOf(value));
                return;
            }
            if (value instanceof Map) {
                out.append('{');
                boolean first = true;
                for (Object raw : ((Map<?, ?>) value).entrySet()) {
                    Map.Entry<?, ?> entry = (Map.Entry<?, ?>) raw;
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    string(out, String.valueOf(entry.getKey()));
                    out.append(':');
                    write(out, entry.getValue());
                }
                out.append('}');
                return;
            }
            if (value instanceof Iterable) {
                out.append('[');
                boolean first = true;
                for (Object item : (Iterable<?>) value) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    write(out, item);
                }
                out.append(']');
                return;
            }
            string(out, String.valueOf(value));
        }

        private static void string(StringBuilder out, String value) {
            out.append('"');
            for (int i = 0; i < value.length(); i++) {
                char ch = value.charAt(i);
                switch (ch) {
                    case '"': out.append("\\\""); break;
                    case '\\': out.append("\\\\"); break;
                    case '\b': out.append("\\b"); break;
                    case '\f': out.append("\\f"); break;
                    case '\n': out.append("\\n"); break;
                    case '\r': out.append("\\r"); break;
                    case '\t': out.append("\\t"); break;
                    default:
                        if (ch < 0x20) {
                            out.append(String.format("\\u%04x", (int) ch));
                        } else {
                            out.append(ch);
                        }
                }
            }
            out.append('"');
        }

        private static final class Parser {
            final String text;
            int index;

            Parser(String text) {
                this.text = text == null ? "" : text;
            }

            boolean end() {
                return index >= text.length();
            }

            void space() {
                while (!end() && Character.isWhitespace(text.charAt(index))) {
                    index++;
                }
            }

            Object value() {
                space();
                if (end()) {
                    throw new IllegalArgumentException("unexpected end of JSON");
                }
                char ch = text.charAt(index);
                if (ch == '{') return object();
                if (ch == '[') return array();
                if (ch == '"') return string();
                if (ch == 't') return literal("true", Boolean.TRUE);
                if (ch == 'f') return literal("false", Boolean.FALSE);
                if (ch == 'n') return literal("null", null);
                if (ch == '-' || Character.isDigit(ch)) return number();
                throw new IllegalArgumentException("unexpected JSON character '" + ch + "' at " + index);
            }

            Map<String, Object> object() {
                expect('{');
                LinkedHashMap<String, Object> result = new LinkedHashMap<String, Object>();
                space();
                if (peek('}')) {
                    index++;
                    return result;
                }
                while (true) {
                    space();
                    String key = string();
                    space();
                    expect(':');
                    result.put(key, value());
                    space();
                    if (peek('}')) {
                        index++;
                        return result;
                    }
                    expect(',');
                }
            }

            List<Object> array() {
                expect('[');
                List<Object> result = new ArrayList<Object>();
                space();
                if (peek(']')) {
                    index++;
                    return result;
                }
                while (true) {
                    result.add(value());
                    space();
                    if (peek(']')) {
                        index++;
                        return result;
                    }
                    expect(',');
                }
            }

            String string() {
                expect('"');
                StringBuilder out = new StringBuilder();
                while (!end()) {
                    char ch = text.charAt(index++);
                    if (ch == '"') {
                        return out.toString();
                    }
                    if (ch != '\\') {
                        out.append(ch);
                        continue;
                    }
                    if (end()) {
                        throw new IllegalArgumentException("unterminated JSON escape");
                    }
                    char escaped = text.charAt(index++);
                    switch (escaped) {
                        case '"': out.append('"'); break;
                        case '\\': out.append('\\'); break;
                        case '/': out.append('/'); break;
                        case 'b': out.append('\b'); break;
                        case 'f': out.append('\f'); break;
                        case 'n': out.append('\n'); break;
                        case 'r': out.append('\r'); break;
                        case 't': out.append('\t'); break;
                        case 'u':
                            if (index + 4 > text.length()) {
                                throw new IllegalArgumentException("short unicode escape");
                            }
                            out.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                            index += 4;
                            break;
                        default:
                            throw new IllegalArgumentException("unsupported JSON escape: \\" + escaped);
                    }
                }
                throw new IllegalArgumentException("unterminated JSON string");
            }

            Object number() {
                int start = index;
                if (peek('-')) index++;
                while (!end() && Character.isDigit(text.charAt(index))) index++;
                boolean decimal = false;
                if (!end() && text.charAt(index) == '.') {
                    decimal = true;
                    index++;
                    while (!end() && Character.isDigit(text.charAt(index))) index++;
                }
                if (!end() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                    decimal = true;
                    index++;
                    if (!end() && (text.charAt(index) == '+' || text.charAt(index) == '-')) index++;
                    while (!end() && Character.isDigit(text.charAt(index))) index++;
                }
                String raw = text.substring(start, index);
                return decimal ? Double.valueOf(raw) : Long.valueOf(raw);
            }

            Object literal(String expected, Object value) {
                if (!text.regionMatches(index, expected, 0, expected.length())) {
                    throw new IllegalArgumentException("invalid JSON literal at " + index);
                }
                index += expected.length();
                return value;
            }

            boolean peek(char ch) {
                return !end() && text.charAt(index) == ch;
            }

            void expect(char ch) {
                space();
                if (end() || text.charAt(index) != ch) {
                    throw new IllegalArgumentException("expected '" + ch + "' at " + index);
                }
                index++;
            }
        }
    }
}
