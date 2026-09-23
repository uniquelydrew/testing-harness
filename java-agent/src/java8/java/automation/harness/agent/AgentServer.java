package automation.harness.agent;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermission;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Authenticated loopback endpoint using APIs available in JDK 8. */
final class AgentServer {
    private final String token;
    private final RecordingBuffer recording = new RecordingBuffer();
    private final HttpServer server;
    private final Path discoveryFile;

    AgentServer(String token, int port, String discoveryDirectory) throws IOException {
        this.token = token;
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 16);
        HttpHandler handler = new HttpHandler() { public void handle(HttpExchange exchange) throws IOException { AgentServer.this.handle(exchange); }};
        String[] paths = {"/health", "/runtime", "/record_start", "/record_read", "/record_stop", "/capture_next_click", "/hit_test", "/resolve", "/activate", "/focus", "/activate_window", "/get_text", "/set_text", "/select_child", "/get_value", "/set_value", "/select_menu_path", "/windows", "/render_surface_inspect"};
        for (String path : paths) server.createContext(path, handler);
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        discoveryFile = writeDiscovery(token, discoveryDirectory);
        Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() { public void run() { cleanup(); }}, "automation-harness-agent-cleanup"));
    }

    int port() { return server.getAddress().getPort(); }

    private Path writeDiscovery(String token, String configuredDirectory) throws IOException {
        String configured = configuredDirectory;
        if (configured == null || configured.trim().isEmpty()) configured = System.getenv("AUTOMATION_HARNESS_JAVA_AGENT_DISCOVERY_DIR");
        Path directory = configured == null || configured.trim().isEmpty() ? Paths.get(System.getProperty("java.io.tmpdir"), "automation-harness-java-agent") : Paths.get(configured);
        Files.createDirectories(directory); restrict(directory, true);
        long pid = ((Number)RuntimeDiagnostics.snapshot().get("pid")).longValue();
        Path target = directory.resolve("java-" + pid + ".json");
        Map<String, Object> payload = new LinkedHashMap<String, Object>(); payload.put("protocol", "automation-harness-java-agent/1"); payload.put("pid", pid); payload.put("host", "127.0.0.1"); payload.put("port", port()); payload.put("token", token);
        Files.write(target, AgentJson.value(payload).getBytes(StandardCharsets.UTF_8)); restrict(target, false); return target;
    }

    private static void restrict(Path path, boolean directory) {
        try { Files.setPosixFilePermissions(path, directory ? EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE) : EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)); }
        catch (UnsupportedOperationException ignored) { } catch (IOException ignored) { }
    }
    private void cleanup() { try { Files.deleteIfExists(discoveryFile); } catch (IOException ignored) { } server.stop(0); }

    private void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod()) || !token.equals(exchange.getRequestHeaders().getFirst("X-Automation-Harness-Token"))) { exchange.sendResponseHeaders(401, -1); exchange.close(); return; }
        String path = exchange.getRequestURI().getPath(); String request = read(exchange.getRequestBody()); Map<String, Object> result = new LinkedHashMap<String, Object>();
        try {
            if (path.equals("/health")) { result.put("status", "ok"); result.put("recording", recording.active()); result.put("runtime", RuntimeDiagnostics.snapshot()); result.put("capabilities", java.util.Arrays.asList("swing", "awt", "swing-menu-model", "swing-text", "swing-selection", "swing-value", "jogl", "tdf-surface", "solipsys-awt-view-canvas")); }
            else if (path.equals("/runtime")) result.putAll(RuntimeDiagnostics.snapshot());
            else if (path.equals("/record_start")) { recording.start(); SwingRecorder.start(recording); result.put("observations", recording.drain()); }
            else if (path.equals("/record_read")) result.put("observations", recording.awaitAndDrain((long)(number(request, "timeout", 0.25) * 1000)));
            else if (path.equals("/record_stop")) { SwingRecorder.stop(); result.put("observations", recording.stop()); }
            else if (path.equals("/capture_next_click")) result.putAll(SwingRecorder.beginCapture().get((long)(number(request, "timeout", 30.0) * 1000), java.util.concurrent.TimeUnit.MILLISECONDS));
            else if (path.equals("/hit_test")) result.putAll(SwingRecorder.hitTest(number(request, "x", Double.NaN), number(request, "y", Double.NaN)));
            else if (path.equals("/resolve")) result.putAll(SwingRecorder.resolve(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path"), string(request, "rendered_class"), string(request, "track_class"), string(request, "track_identity_key"), string(request, "track_identity_value")));
            else if (path.equals("/activate")) result.putAll(SwingRecorder.activate(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path")));
            else if (path.equals("/focus")) result.putAll(SwingRecorder.focus(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path")));
            else if (path.equals("/activate_window")) result.putAll(SwingRecorder.activateWindow(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path")));
            else if (path.equals("/get_text")) result.put("text", SwingRecorder.getText(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path")));
            else if (path.equals("/set_text")) result.putAll(SwingRecorder.setText(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path"), string(request, "value")));
            else if (path.equals("/select_child")) result.putAll(SwingRecorder.selectChild(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path"), (int)number(request, "index", -1)));
            else if (path.equals("/get_value")) result.put("value", SwingRecorder.getValue(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path")));
            else if (path.equals("/set_value")) result.putAll(SwingRecorder.setValue(string(request, "name"), string(request, "accessible_id"), string(request, "native_class"), string(request, "window"), string(request, "component_path"), number(request, "value", Double.NaN)));
            else if (path.equals("/select_menu_path")) {
                int count = (int)number(request, "menu_count", 0);
                if (count <= 0) throw new IllegalArgumentException("menu path must contain at least one segment");
                String[] ids = new String[count];
                String[] texts = new String[count];
                int[] ordinals = new int[count];
                for (int index = 0; index < count; index++) {
                    ids[index] = string(request, "menu_" + index + "_id");
                    texts[index] = string(request, "menu_" + index + "_text");
                    ordinals[index] = (int)number(request, "menu_" + index + "_ordinal", -1);
                }
                result.putAll(SwingRecorder.selectMenuPath(
                        string(request, "name"), string(request, "accessible_id"),
                        string(request, "native_class"), string(request, "window"),
                        string(request, "component_path"), ids, texts, ordinals));
            }
            else if (path.equals("/windows")) result.put("windows", SwingRecorder.windows());
            else if (path.equals("/render_surface_inspect")) result.putAll(RenderedSurfaceDiagnostics.inspectAt(number(request, "x", Double.NaN), number(request, "y", Double.NaN)));
            else { send(exchange, 404, error("unknown operation")); return; }
        } catch (Exception exception) { send(exchange, 404, error(exception.getMessage() == null ? exception.toString() : exception.getMessage())); return; }
        Map<String, Object> payload = new LinkedHashMap<String, Object>(); payload.put("ok", true); payload.put("result", result); send(exchange, 200, payload);
    }

    private static Map<String, Object> error(String message) { Map<String, Object> result = new LinkedHashMap<String, Object>(); result.put("ok", false); result.put("error", message); return result; }
    private static void send(HttpExchange exchange, int status, Map<String, Object> payload) throws IOException { byte[] bytes = AgentJson.value(payload).getBytes(StandardCharsets.UTF_8); exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8"); exchange.sendResponseHeaders(status, bytes.length); exchange.getResponseBody().write(bytes); exchange.close(); }
    private static String read(InputStream input) throws IOException { ByteArrayOutputStream output = new ByteArrayOutputStream(); byte[] buffer = new byte[4096]; int count; while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count); return new String(output.toByteArray(), StandardCharsets.UTF_8); }
    private static double number(String payload, String key, double fallback) { Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*(-?(?:\\d+(?:\\.\\d*)?|\\.\\d+))").matcher(payload); return match.find() ? Double.parseDouble(match.group(1)) : fallback; }
    private static String string(String payload, String key) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\\\"])*)\\\"").matcher(payload);
        if (!match.find()) return null;
        return unescapeJsonString(match.group(1));
    }

    private static String unescapeJsonString(String value) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            boolean startsEscape = ch == '\\';
            if (!startsEscape || index + 1 >= value.length()) {
                result.append(ch);
                continue;
            }
            char escaped = value.charAt(++index);
            switch (escaped) {
                case '"': result.append('"'); break;
                case '\\': result.append('\\'); break;
                case '/': result.append('/'); break;
                case 'b': result.append('\b'); break;
                case 'f': result.append('\f'); break;
                case 'n': result.append('\n'); break;
                case 'r': result.append('\r'); break;
                case 't': result.append('\t'); break;
                case 'u':
                    if (index + 4 >= value.length()) {
                        throw new IllegalArgumentException("invalid JSON unicode escape");
                    }
                    String hex = value.substring(index + 1, index + 5);
                    try {
                        result.append((char)Integer.parseInt(hex, 16));
                    } catch (NumberFormatException error) {
                        throw new IllegalArgumentException("invalid JSON unicode escape: " + hex);
                    }
                    index += 4;
                    break;
                default:
                    result.append(escaped);
            }
        }
        return result.toString();
    }

}
