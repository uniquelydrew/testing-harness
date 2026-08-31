package automation.harness.agent;

/**
 * Deliberately small agent bootstrap.  Transport and adapters are supplied by
 * the host application's Java integration in the next vertical slice; keeping
 * this entry point buildable makes the -javaagent contract explicit now.
 */
public final class AutomationAgent {
    private static volatile AgentServer server;
    private AutomationAgent() { }

    public static void premain(String arguments) {
        // Never create a network listener without a run-scoped token supplied
        // by the Python java-desktop backend.
        if (arguments == null || !arguments.contains("token=")) {
            return;
        }
        System.setProperty("automation.harness.agent.enabled", "true");
        String token = argument(arguments, "token");
        String port = argument(arguments, "port");
        if (token == null || port == null) return;
        try {
            server = new AgentServer(token, Integer.parseInt(port));
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
        for (String part : arguments.split("[,;]")) {
            String[] pair = part.split("=", 2);
            if (pair.length == 2 && pair[0].trim().equals(key)) return pair[1].trim();
        }
        return null;
    }
}
