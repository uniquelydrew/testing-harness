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
 * Zero-dependency Java agent exposing the public JavaFX scene graph to the
 * Automation Harness over a loopback-only JSON-lines protocol.
 *
 * The implementation deliberately uses reflection instead of linking against
 * JavaFX at compile time. This keeps one agent usable with the JavaFX 21/22
 * SDKs used by the target applications and allows the jar to be built with a
 * plain JDK.
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
            Map<String, Object> response = dispatch(request);
            writeResponse(writer, response);
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
            int maxDepth = intValue(request.get("max_depth"), 12);
            return ok(FxRuntime.treePayload(maxDepth));
        }
        if ("capture_next_click".equals(op)) {
            long timeoutMs = longValue(request.get("timeout_ms"), 30000L);
            Map<String, Object> captured = FxRuntime.captureNextClick(timeoutMs);
            return ok(singleton("node", captured));
        }
        if ("hit_test".equals(op)) {
            double x = doubleValue(request.get("x"), Double.NaN);
            double y = doubleValue(request.get("y"), Double.NaN);
            if (Double.isNaN(x) || Double.isNaN(y)) {
                return error("hit_test requires numeric x and y");
            }
            Map<String, Object> node = FxRuntime.hitTest(x, y);
            if (node == null) {
                return error("no JavaFX node at requested point");
            }
            return ok(singleton("node", node));
        }
        if ("find".equals(op)) {
            Map<String, Object> identification = mapValue(request.get("identification"));
            return ok(FxRuntime.findPayload(identification));
        }
        if ("state".equals(op)) {
            Map<String, Object> identification = mapValue(request.get("identification"));
            Map<String, Object> node = FxRuntime.findUnique(identification);
            return ok(singleton("node", node));
        }
        if ("activate".equals(op)) {
            Map<String, Object> identification = mapValue(request.get("identification"));
            return ok(FxRuntime.activate(identification));
        }
        if ("get_text".equals(op)) {
            Map<String, Object> identification = mapValue(request.get("identification"));
            return ok(singleton("text", FxRuntime.getText(identification)));
        }
        if ("set_text".equals(op)) {
            Map<String, Object> identification = mapValue(request.get("identification"));
            return ok(FxRuntime.setText(identification, stringValue(request.get("value"))));
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
            // Non-POSIX filesystem; loopback binding and token still protect the endpoint.
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
        if (value == null) {
            return fallback;
        }
        try {
            return Integer.parseInt(value);
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
        if (value instanceof Number) {
            return ((Number) value).intValue();
        }
        return fallback;
    }

    private static long longValue(Object value, long fallback) {
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        return fallback;
    }

    private static double doubleValue(Object value, double fallback) {
        if (value instanceof Number) {
            return ((Number) value).doubleValue();
        }
        return fallback;
    }

    private static final class FxRuntime {
        private static final String PLATFORM = "javafx.application.Platform";
        private static final String WINDOW = "javafx.stage.Window";
        private static final String PARENT = "javafx.scene.Parent";
        private static final String NODE = "javafx.scene.Node";
        private static final String MOUSE_EVENT = "javafx.scene.input.MouseEvent";
        private static final String EVENT_HANDLER = "javafx.event.EventHandler";
        private static final Set<String> INSTALLED_SCENES = Collections.newSetFromMap(new ConcurrentHashMap<String, Boolean>());

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
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("windows", values);
                return result;
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
                    Map<String, Object> windowPayload = new LinkedHashMap<String, Object>();
                    windowPayload.put("title", windowTitle(window));
                    windowPayload.put("root", treeNode(root, window, 0, Math.max(0, maxDepth)));
                    values.add(windowPayload);
                }
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("windows", values);
                return result;
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
                final List<SceneFilter> added = onFx(() -> installCaptureFilters(captured, latch));
                filters.addAll(added);
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
                        Object event = args[0];
                        Object target = call(event, "getTarget");
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
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                List<Object> matches = new ArrayList<Object>();
                for (NodeMatch match : resolution.matches) {
                    matches.add(nodePayload(match.node, match.window));
                }
                result.put("matches", matches);
                result.put("match_count", matches.size());
                result.put("stages", resolution.stages);
                return result;
            });
        }

        static Map<String, Object> findUnique(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                Resolution resolution = resolve(identification, true);
                if (resolution.matches.isEmpty()) {
                    throw new IllegalArgumentException("JavaFX component not found: " + identification);
                }
                if (resolution.matches.size() != 1) {
                    throw new IllegalArgumentException("JavaFX component is ambiguous: " + identification + " (" + resolution.matches.size() + " matches)");
                }
                NodeMatch match = resolution.matches.get(0);
                Map<String, Object> payload = nodePayload(match.node, match.window);
                payload.put("resolution_stages", resolution.stages);
                return payload;
            });
        }

        static Map<String, Object> activate(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                Resolution resolution = resolve(identification, true);
                NodeMatch match = unique(resolution, identification);
                Object node = match.node;
                Method fire = findMethod(node.getClass(), "fire");
                if (fire == null || fire.getParameterCount() != 0) {
                    throw new UnsupportedOperationException("JavaFX node has no semantic fire() action: " + node.getClass().getName());
                }
                fire.invoke(node);
                Map<String, Object> result = new LinkedHashMap<String, Object>();
                result.put("action", "fire");
                result.put("node", nodePayload(node, match.window));
                return result;
            });
        }

        static String getText(final Map<String, Object> identification) throws Exception {
            return onFx(() -> {
                Resolution resolution = resolve(identification, true);
                NodeMatch match = unique(resolution, identification);
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
                Resolution resolution = resolve(identification, true);
                NodeMatch match = unique(resolution, identification);
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

            List<NodeMatch> matches = allNodeMatches();
            List<Object> stages = new ArrayList<Object>();
            matches = filter(matches, mandatory);
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
                    if (!payloadMatches(nodePayload(parent, match.window), parentCriteria)) {
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

        private static boolean payloadMatches(Map<String, Object> payload, Map<String, Object> criteria) {
            for (Map.Entry<String, Object> entry : criteria.entrySet()) {
                if (!valueMatches(payload.get(entry.getKey()), entry.getValue())) {
                    return false;
                }
            }
            return true;
        }

        private static boolean valueMatches(Object actual, Object expected) {
            if (actual == null || expected == null) {
                return actual == expected;
            }
            if (actual instanceof String && expected instanceof String) {
                return ((String) actual).equals(expected);
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
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("ref", ref(node));
            payload.put("class", node.getClass().getName());
            payload.put("simple_class", node.getClass().getSimpleName());
            payload.put("id", stringOrNull(call(node, "getId")));
            payload.put("accessible_role", enumName(call(node, "getAccessibleRole")));
            payload.put("accessible_text", stringOrNull(call(node, "getAccessibleText")));
            payload.put("accessible_help", stringOrNull(call(node, "getAccessibleHelp")));
            payload.put("visible", boolCall(node, "isVisible", true));
            payload.put("disabled", boolCall(node, "isDisable", false));
            payload.put("focused", boolCall(node, "isFocused", false));
            payload.put("managed", boolCall(node, "isManaged", true));
            payload.put("focus_traversable", boolCall(node, "isFocusTraversable", false));
            payload.put("window", windowTitle(window));
            payload.put("style_classes", listValue(call(node, "getStyleClass")));
            payload.put("text", optionalNoArgString(node, "getText"));
            payload.put("bounds", boundsList(boundsOnScreen(node)));
            payload.put("hierarchy", hierarchy(node));
            payload.put("actions", actions(node));
            Object parent = call(node, "getParent");
            if (parent != null) {
                Map<String, Object> parentPayload = new LinkedHashMap<String, Object>();
                parentPayload.put("ref", ref(parent));
                parentPayload.put("class", parent.getClass().getName());
                parentPayload.put("simple_class", parent.getClass().getSimpleName());
                parentPayload.put("id", stringOrNull(call(parent, "getId")));
                parentPayload.put("accessible_role", enumName(call(parent, "getAccessibleRole")));
                parentPayload.put("accessible_text", stringOrNull(call(parent, "getAccessibleText")));
                parentPayload.put("text", optionalNoArgString(parent, "getText"));
                payload.put("parent", parentPayload);
            }
            return payload;
        }

        private static List<Object> actions(Object node) {
            List<Object> actions = new ArrayList<Object>();
            Method fire = findMethod(node.getClass(), "fire");
            if (fire != null && fire.getParameterCount() == 0) {
                actions.add("activate");
                actions.add("click");
            }
            if (findCompatibleMethod(node.getClass(), "setText", String.class) != null) {
                actions.add("set_text");
            }
            if (findMethod(node.getClass(), "getText") != null) {
                actions.add("get_text");
            }
            return actions;
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
            for (int depth = 0; current != null && depth < 12; depth++) {
                String id = stringOrNull(call(current, "getId"));
                String accessibleText = stringOrNull(call(current, "getAccessibleText"));
                String role = enumName(call(current, "getAccessibleRole"));
                String className = current.getClass().getName();
                if ((id != null && !id.isEmpty())
                        || (accessibleText != null && !accessibleText.isEmpty())
                        || (role != null && !"PARENT".equals(role) && !"NODE".equals(role))
                        || className.startsWith("javafx.scene.control.")) {
                    return current;
                }
                fallback = current;
                current = call(current, "getParent");
            }
            return fallback;
        }

        private static List<Object> windows() throws Exception {
            Class<?> windowClass = Class.forName(WINDOW);
            Object result = windowClass.getMethod("getWindows").invoke(null);
            return listValue(result);
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
            if (resolved == null) {
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
                Object value = resolved.invoke(target);
                return stringOrNull(value);
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
