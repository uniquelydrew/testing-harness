package automation.harness.agent;

import java.util.UUID;

/**
 * Deliberately small agent bootstrap. Transport and adapters are supplied by
 * the host application's Java integration.
 */
public final class AutomationAgent {
    private static volatile AgentServer server;
    private AutomationAgent() { }

    public static void premain(String arguments) {
        System.setProperty("automation.harness.agent.enabled", "true");
        String token = argument(arguments, "token");
        String port = argument(arguments, "port");
        String discovery = argument(arguments, "discovery");
        if (token == null || token.isBlank()) token = UUID.randomUUID().toString();
        if (port == null || port.isBlank()) port = "0";
        try {
            server = new AgentServer(token, Integer.parseInt(port), discovery);
            RuntimeDiagnostics.write(discovery);
        } catch (Exception exception) {
            throw new IllegalStateException("could not start automation harness agent", exception);
        }
    }

    /** Entry point used by the JavaFX event adapter before snapshotting. */
    public static JavaFxSemanticTargetResolver.Resolution resolveSemanticTarget(Object physicalTarget) {
        return JavaFxSemanticTargetResolver.resolveSemanticTarget(physicalTarget);
    }

    /** JavaFX adapters publish already-normalized, compact event maps here. */
    public static void recordJavaFxEvent(java.util.Map<String, Object> event) {
        AgentServer current = server;
        if (current != null) current.recording().offer(event);
    }

    private static String argument(String arguments, String key) {
        if (arguments == null) return null;
        for (String part : arguments.split("[,;]")) {
            String[] pair = part.split("=", 2);
            if (pair.length == 2 && pair[0].trim().equals(key)) return pair[1].trim();
        }
        return null;
    }
}
