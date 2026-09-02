package automation.harness.agent;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;

/** Authenticated loopback-only endpoint for the Python JavaFX bridge. */
final class AgentServer {
    private final String token;
    private final RecordingBuffer recording = new RecordingBuffer();
    private final HttpServer server;

    AgentServer(String token, int port) throws IOException {
        this.token = token;
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 16);
        server.createContext("/health", this::handle);
        server.createContext("/record_start", this::handle);
        server.createContext("/record_read", this::handle);
        server.createContext("/record_stop", this::handle);
        server.createContext("/capture_next_click", this::handle);
        server.createContext("/hit_test", this::handle);
        server.setExecutor(Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "automation-harness-agent");
            thread.setDaemon(true);
            return thread;
        }));
        server.start();
    }

    RecordingBuffer recording() { return recording; }
    int port() { return server.getAddress().getPort(); }

    private void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod()) || !token.equals(exchange.getRequestHeaders().getFirst("X-Automation-Harness-Token"))) {
            exchange.sendResponseHeaders(401, -1);
            return;
        }
        String path = exchange.getRequestURI().getPath();
        String request = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Map<String, Object> result = new LinkedHashMap<>();
        if (path.equals("/health")) {
            result.put("status", "ok");
            result.put("recording", recording.active());
        } else if (path.equals("/record_start")) {
            recording.start();
            JavaFxRecorder.start(recording);
            result.put("observations", recording.drain());
        } else if (path.equals("/record_read")) {
            result.put("observations", recording.drain());
        } else if (path.equals("/record_stop")) {
            JavaFxRecorder.stop();
            result.put("observations", recording.stop());
        } else if (path.equals("/capture_next_click")) {
            try { result.putAll(JavaFxRecorder.captureNextClick((long) (number(request, "timeout", 30.0) * 1000))); }
            catch (Exception exception) { send(exchange, 408, Map.of("ok", false, "error", "capture timed out or failed: " + exception.getMessage())); return; }
        } else if (path.equals("/hit_test")) {
            try { result.putAll(JavaFxRecorder.hitTest(number(request, "x", Double.NaN), number(request, "y", Double.NaN))); }
            catch (Exception exception) { send(exchange, 404, Map.of("ok", false, "error", exception.getMessage())); return; }
        } else {
            send(exchange, 404, Map.of("ok", false, "error", "unknown operation"));
            return;
        }
        send(exchange, 200, Map.of("ok", true, "result", result));
    }

    private static void send(HttpExchange exchange, int status, Map<String, Object> payload) throws IOException {
        byte[] bytes = AgentJson.value(payload).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static double number(String payload, String key, double fallback) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*(-?(?:\\d+(?:\\.\\d*)?|\\.\\d+))").matcher(payload);
        return match.find() ? Double.parseDouble(match.group(1)) : fallback;
    }
}
