package automation.harness.agent;

import java.awt.Component;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Runtime discovery adapter for com.solipsys.view.AWTViewCanvas. */
final class SolipsysAwtViewCanvasAdapter implements RenderedSurfaceAdapter {
    private static final String TARGET_CLASS = "com.solipsys.view.AWTViewCanvas";
    private static final String[] DISCOVERY_TERMS = {
        "pick", "hit", "select", "track", "entity", "object", "model", "view",
        "screen", "world", "coordinate", "render", "layer", "symbol"
    };

    public boolean supports(Component component) {
        if (component == null) return false;
        Class<?> type = component.getClass();
        while (type != null) {
            if (TARGET_CLASS.equals(type.getName())) return true;
            type = type.getSuperclass();
        }
        return false;
    }

    public String name() { return "solipsys_awt_view_canvas"; }

    public Map<String, Object> inspect(Component component) {
        if (!supports(component)) throw new IllegalArgumentException("component is not an AWTViewCanvas");
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("adapter", name());
        result.put("surface_class", component.getClass().getName());
        result.put("surface_ref", Integer.toHexString(System.identityHashCode(component)));
        result.put("class_hierarchy", classHierarchy(component.getClass()));
        result.put("interfaces", interfaces(component.getClass()));
        result.put("candidate_methods", candidateMethods(component.getClass()));
        result.put("candidate_fields", candidateFields(component));
        result.put("diagnostic_only", true);
        return result;
    }

    private static List<String> classHierarchy(Class<?> type) {
        List<String> result = new ArrayList<String>();
        Class<?> current = type;
        while (current != null) {
            result.add(current.getName());
            current = current.getSuperclass();
        }
        return result;
    }

    private static List<String> interfaces(Class<?> type) {
        Set<String> names = new LinkedHashSet<String>();
        Class<?> current = type;
        while (current != null) {
            for (Class<?> iface : current.getInterfaces()) collectInterface(iface, names);
            current = current.getSuperclass();
        }
        return new ArrayList<String>(names);
    }

    private static void collectInterface(Class<?> type, Set<String> names) {
        if (!names.add(type.getName())) return;
        for (Class<?> parent : type.getInterfaces()) collectInterface(parent, names);
    }

    private static List<Map<String, Object>> candidateMethods(Class<?> type) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        Set<String> seen = new LinkedHashSet<String>();
        Class<?> current = type;
        while (current != null) {
            Method[] methods;
            try { methods = current.getDeclaredMethods(); }
            catch (Throwable ignored) { methods = new Method[0]; }
            for (Method method : methods) {
                String signature = signature(method);
                if (!seen.add(signature) || !interesting(method.getName())) continue;
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("declaring_class", current.getName());
                item.put("name", method.getName());
                item.put("return_type", method.getReturnType().getName());
                List<String> parameters = new ArrayList<String>();
                for (Class<?> parameter : method.getParameterTypes()) parameters.add(parameter.getName());
                item.put("parameter_types", parameters);
                item.put("modifiers", Modifier.toString(method.getModifiers()));
                result.add(item);
            }
            current = current.getSuperclass();
        }
        Collections.sort(result, new Comparator<Map<String, Object>>() {
            public int compare(Map<String, Object> left, Map<String, Object> right) {
                return String.valueOf(left.get("name")).compareTo(String.valueOf(right.get("name")));
            }
        });
        return result;
    }

    private static List<Map<String, Object>> candidateFields(Component component) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        Class<?> current = component.getClass();
        while (current != null) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); }
            catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                if (!interesting(field.getName()) && !interesting(field.getType().getName())) continue;
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("declaring_class", current.getName());
                item.put("name", field.getName());
                item.put("type", field.getType().getName());
                item.put("modifiers", Modifier.toString(field.getModifiers()));
                Object value = readField(field, component);
                if (value != null) {
                    item.put("runtime_type", value.getClass().getName());
                    item.put("runtime_ref", Integer.toHexString(System.identityHashCode(value)));
                }
                result.add(item);
            }
            current = current.getSuperclass();
        }
        return result;
    }

    private static Object readField(Field field, Object target) {
        try {
            if (!field.isAccessible()) field.setAccessible(true);
            return field.get(Modifier.isStatic(field.getModifiers()) ? null : target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static boolean interesting(String value) {
        if (value == null) return false;
        String lower = value.toLowerCase(Locale.ROOT);
        for (String term : DISCOVERY_TERMS) if (lower.contains(term)) return true;
        return false;
    }

    private static String signature(Method method) {
        StringBuilder value = new StringBuilder(method.getName()).append('(');
        for (Class<?> parameter : method.getParameterTypes()) value.append(parameter.getName()).append(',');
        return value.append(')').append(method.getReturnType().getName()).toString();
    }
}
