package automation.harness.agent;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/** Executed directly in CI-capable environments; no JUnit dependency needed. */
public final class AgentServerSmoke {
    private AgentServerSmoke() { }

    public static void main(String[] arguments) throws Exception {
        AgentServer server = new AgentServer("smoke-token", 0);
        String endpoint = "http://127.0.0.1:" + server.port();
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder(URI.create(endpoint + "/record_start"))
            .header("X-Automation-Harness-Token", "smoke-token")
            .POST(HttpRequest.BodyPublishers.ofString("{}"))
            .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200 || !response.body().contains("\"ok\":true")) {
            throw new AssertionError("agent recording endpoint failed: " + response.statusCode() + " " + response.body());
        }
        HttpRequest denied = HttpRequest.newBuilder(URI.create(endpoint + "/record_read"))
            .POST(HttpRequest.BodyPublishers.ofString("{}"))
            .build();
        if (client.send(denied, HttpResponse.BodyHandlers.ofString()).statusCode() != 401) {
            throw new AssertionError("agent accepted an unauthenticated request");
        }
        HttpRequest capture = HttpRequest.newBuilder(URI.create(endpoint + "/capture_next_click"))
            .header("X-Automation-Harness-Token", "smoke-token")
            .POST(HttpRequest.BodyPublishers.ofString("{\"timeout\":1.0}"))
            .build();
        CompletableFuture<HttpResponse<String>> pendingCapture = client.sendAsync(
            capture, HttpResponse.BodyHandlers.ofString()
        );
        Thread.sleep(100);
        HttpRequest health = HttpRequest.newBuilder(URI.create(endpoint + "/health"))
            .header("X-Automation-Harness-Token", "smoke-token")
            .timeout(Duration.ofMillis(500))
            .POST(HttpRequest.BodyPublishers.ofString("{}"))
            .build();
        HttpResponse<String> healthResponse = client.send(health, HttpResponse.BodyHandlers.ofString());
        if (healthResponse.statusCode() != 200) {
            throw new AssertionError("capture request starved agent health checks");
        }
        pendingCapture.get(2, TimeUnit.SECONDS);
        System.exit(0);
    }
}
