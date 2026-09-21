package automation.harness.agent;

import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.RuntimeMXBean;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;

/** Snapshot of the JVM that actually loaded the agent. */
final class RuntimeDiagnostics {
    private RuntimeDiagnostics() { }

    static Map<String, Object> snapshot() {
        RuntimeMXBean runtime = ManagementFactory.getRuntimeMXBean();
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("pid", processId(runtime));
        result.put("runtime_name", runtime.getName());
        result.put("java_version", property("java.version"));
        result.put("java_vendor", property("java.vendor"));
        result.put("java_home", property("java.home"));
        result.put("java_vm_name", property("java.vm.name"));
        result.put("java_vm_vendor", property("java.vm.vendor"));
        result.put("java_vm_version", property("java.vm.version"));
        result.put("java_specification_version", property("java.specification.version"));
        result.put("java_vm_specification_version", property("java.vm.specification.version"));
        result.put("java_class_version", property("java.class.version"));
        result.put("os_name", property("os.name"));
        result.put("os_arch", property("os.arch"));
        result.put("os_version", property("os.version"));
        result.put("start_time_ms", runtime.getStartTime());
        result.put("uptime_ms", runtime.getUptime());
        result.put("input_arguments", new ArrayList<String>(runtime.getInputArguments()));
        return result;
    }

    static Path write(String configuredDirectory) throws IOException {
        Map<String, Object> snapshot = snapshot();
        long pid = ((Number) snapshot.get("pid")).longValue();
        String configured = configuredDirectory;
        if (configured == null || configured.trim().isEmpty()) configured = System.getenv("AUTOMATION_HARNESS_JAVA_AGENT_DISCOVERY_DIR");
        Path directory = configured == null || configured.trim().isEmpty()
            ? Paths.get(System.getProperty("java.io.tmpdir"), "automation-harness-java-agent") : Paths.get(configured);
        Files.createDirectories(directory);
        Path target = directory.resolve("java-" + pid + "-runtime.json");
        Files.write(target, AgentJson.value(snapshot).getBytes(StandardCharsets.UTF_8));
        return target;
    }

    private static String property(String name) {
        try { return System.getProperty(name); }
        catch (SecurityException exception) { return null; }
    }

    private static long processId(RuntimeMXBean runtime) {
        String name = runtime.getName();
        if (name != null) {
            int separator = name.indexOf('@');
            String candidate = separator >= 0 ? name.substring(0, separator) : name;
            try { return Long.parseLong(candidate); }
            catch (NumberFormatException ignored) { }
        }
        return -1L;
    }
}
