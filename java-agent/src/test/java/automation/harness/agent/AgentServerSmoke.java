package automation.harness.agent;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

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
        System.exit(0);
    }
}
