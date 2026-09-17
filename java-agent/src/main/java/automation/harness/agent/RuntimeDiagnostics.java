package automation.harness.agent;

import java.lang.management.ManagementFactory;
import java.lang.management.RuntimeMXBean;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Snapshot of the JVM that actually loaded the Automation Harness agent. */
final class RuntimeDiagnostics {
    private RuntimeDiagnostics() { }

    static Map<String, Object> snapshot() {
        RuntimeMXBean runtime = ManagementFactory.getRuntimeMXBean();
        Map<String, Object> result = new LinkedHashMap<>();
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
        result.put("java_class_path", property("java.class.path"));
        result.put("java_library_path", property("java.library.path"));
        result.put("os_name", property("os.name"));
        result.put("os_arch", property("os.arch"));
        result.put("os_version", property("os.version"));
        result.put("start_time_ms", runtime.getStartTime());
        result.put("uptime_ms", runtime.getUptime());
        result.put("input_arguments", new ArrayList<String>(runtime.getInputArguments()));
        return result;
    }

    private static String property(String name) {
        try {
            return System.getProperty(name);
        } catch (SecurityException exception) {
            return null;
        }
    }

    private static long processId(RuntimeMXBean runtime) {
        String name = runtime.getName();
        if (name != null) {
            int separator = name.indexOf('@');
            String candidate = separator >= 0 ? name.substring(0, separator) : name;
            try {
                return Long.parseLong(candidate);
            } catch (NumberFormatException ignored) { }
        }
        return -1L;
    }
}
