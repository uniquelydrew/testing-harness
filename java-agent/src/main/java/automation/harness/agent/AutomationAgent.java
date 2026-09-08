package automation.harness.agent;

/**
 * Deliberately small agent bootstrap.  Transport and adapters are supplied by
 * the host application's Java integration in the next vertical slice; keeping
 * this entry point buildable makes the -javaagent contract explicit now.
 */
public final class AutomationAgent {
    private AutomationAgent() { }

    public static void premain(String arguments) {
        // Never create a network listener without a run-scoped token supplied
        // by the Python java-desktop backend.
        if (arguments == null || !arguments.contains("token=")) {
            return;
        }
        System.setProperty("automation.harness.agent.enabled", "true");
    }
}
