package automation.harness.agent;

import java.util.Iterator;
import java.util.Map;

/** Minimal dependency-free JSON writer compatible with Java 8. */
final class AgentJson {
    private AgentJson() { }

    static String value(Object value) {
        if (value == null) return "null";
        if (value instanceof String) return quote((String) value);
        if (value instanceof Number || value instanceof Boolean) return String.valueOf(value);
        if (value instanceof Map) {
            StringBuilder result = new StringBuilder("{");
            Iterator<? extends Map.Entry<?, ?>> entries = ((Map<?, ?>) value).entrySet().iterator();
            while (entries.hasNext()) {
                Map.Entry<?, ?> entry = entries.next();
                result.append(quote(String.valueOf(entry.getKey()))).append(':').append(value(entry.getValue()));
                if (entries.hasNext()) result.append(',');
            }
            return result.append('}').toString();
        }
        if (value instanceof Iterable) {
            StringBuilder result = new StringBuilder("[");
            Iterator<?> items = ((Iterable<?>) value).iterator();
            while (items.hasNext()) {
                result.append(value(items.next()));
                if (items.hasNext()) result.append(',');
            }
            return result.append(']').toString();
        }
        return quote(String.valueOf(value));
    }

    private static String quote(String value) {
        return '"' + value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + '"';
    }
}
