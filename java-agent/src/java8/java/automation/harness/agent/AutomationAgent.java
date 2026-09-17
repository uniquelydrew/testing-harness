package automation.harness.agent;

import java.util.UUID;

/** Java 8 compatible bootstrap for Swing/AWT/JOGL targets such as MSCT. */
public final class AutomationAgent {
    private static volatile AgentServer server;
    private AutomationAgent() { }

    public static void premain(String arguments) {
        System.setProperty("automation.harness.agent.enabled", "true");
        String token = argument(arguments, "token");
        String port = argument(arguments, "port");
        String discovery = argument(arguments, "discovery");
        if (token == null || token.trim().isEmpty()) token = UUID.randomUUID().toString();
        if (port == null || port.trim().isEmpty()) port = "0";
        try {
            server = new AgentServer(token, Integer.parseInt(port), discovery);
            RuntimeDiagnostics.write(discovery);
        } catch (Exception exception) {
            throw new IllegalStateException("could not start automation harness agent", exception);
        }
    }

    private static String argument(String arguments, String key) {
        if (arguments == null) return null;
        String[] parts = arguments.split("[,;]");
        for (String part : parts) {
            String[] pair = part.split("=", 2);
            if (pair.length == 2 && pair[0].trim().equals(key)) return pair[1].trim();
        }
        return null;
    }
}
