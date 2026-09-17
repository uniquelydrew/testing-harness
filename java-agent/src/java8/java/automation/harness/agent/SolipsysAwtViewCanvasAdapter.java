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
    private static final int MAX_BACKING_OBJECTS = 24;
    private static final String[] DISCOVERY_TERMS = {
        "pick", "hit", "select", "track", "entity", "object", "model", "view",
        "screen", "world", "coordinate", "render", "layer", "symbol", "display",
        "scene", "canvas", "graphic", "feature", "element", "location", "position"
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
        result.put("backing_objects", backingObjects(component));
        result.put("diagnostic_only", true);
        return result;
    }

    private static List<String> classHierarchy(Class<?> type) {
        List<String> result = new ArrayList<String>();
        Class<?> current = type;
        while (current != null) { result.add(current.getName()); current = current.getSuperclass(); }
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
                if (!seen.add(signature) || !interesting(method.getName()) && !interesting(method.getReturnType().getName())) continue;
                result.add(describeMethod(current, method));
            }
            current = current.getSuperclass();
        }
        sortMethods(result);
        return result;
    }

    private static Map<String, Object> describeMethod(Class<?> declaring, Method method) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("declaring_class", declaring.getName());
        item.put("name", method.getName());
        item.put("return_type", method.getReturnType().getName());
        List<String> parameters = new ArrayList<String>();
        for (Class<?> parameter : method.getParameterTypes()) parameters.add(parameter.getName());
        item.put("parameter_types", parameters);
        item.put("modifiers", Modifier.toString(method.getModifiers()));
        return item;
    }

    private static void sortMethods(List<Map<String, Object>> result) {
        Collections.sort(result, new Comparator<Map<String, Object>>() {
            public int compare(Map<String, Object> left, Map<String, Object> right) {
                return String.valueOf(left.get("name")).compareTo(String.valueOf(right.get("name")));
            }
        });
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
                Map<String, Object> item = describeField(field, component);
                result.add(item);
            }
            current = current.getSuperclass();
        }
        return result;
    }

    /**
     * Describe interesting live objects referenced by the canvas without invoking target methods.
     * This is intentionally shallow and bounded: it discovers the proprietary model/view API
     * needed for a later native picker without mutating MSCT state.
     */
    private static List<Map<String, Object>> backingObjects(Component component) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        Set<Integer> seen = new LinkedHashSet<Integer>();
        Class<?> current = component.getClass();
        while (current != null && result.size() < MAX_BACKING_OBJECTS) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); }
            catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                if (result.size() >= MAX_BACKING_OBJECTS) break;
                Object value = readField(field, component);
                if (value == null || isSimple(value.getClass())) continue;
                if (!interesting(field.getName()) && !interesting(field.getType().getName()) && !interesting(value.getClass().getName())) continue;
                int identity = System.identityHashCode(value);
                if (!seen.add(Integer.valueOf(identity))) continue;
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("via_field", field.getName());
                item.put("declaring_class", current.getName());
                item.put("runtime_type", value.getClass().getName());
                item.put("runtime_ref", Integer.toHexString(identity));
                item.put("interfaces", interfaces(value.getClass()));
                item.put("candidate_methods", candidateMethods(value.getClass()));
                item.put("candidate_fields", shallowFieldTypes(value));
                result.add(item);
            }
            current = current.getSuperclass();
        }
        return result;
    }

    private static List<Map<String, Object>> shallowFieldTypes(Object target) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        Class<?> current = target.getClass();
        int count = 0;
        while (current != null && count < 40) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); }
            catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                if (count >= 40) break;
                if (!interesting(field.getName()) && !interesting(field.getType().getName())) continue;
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("name", field.getName());
                item.put("type", field.getType().getName());
                Object value = readField(field, target);
                if (value != null) item.put("runtime_type", value.getClass().getName());
                result.add(item); count++;
            }
            current = current.getSuperclass();
        }
        return result;
    }

    private static Map<String, Object> describeField(Field field, Object target) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("declaring_class", field.getDeclaringClass().getName());
        item.put("name", field.getName());
        item.put("type", field.getType().getName());
        item.put("modifiers", Modifier.toString(field.getModifiers()));
        Object value = readField(field, target);
        if (value != null) {
            item.put("runtime_type", value.getClass().getName());
            item.put("runtime_ref", Integer.toHexString(System.identityHashCode(value)));
        }
        return item;
    }

    private static Object readField(Field field, Object target) {
        try {
            if (!field.isAccessible()) field.setAccessible(true);
            return field.get(Modifier.isStatic(field.getModifiers()) ? null : target);
        } catch (Throwable ignored) { return null; }
    }

    private static boolean isSimple(Class<?> type) {
        return type.isPrimitive() || Number.class.isAssignableFrom(type) || CharSequence.class.isAssignableFrom(type)
                || Boolean.class.equals(type) || Character.class.equals(type) || Class.class.equals(type) || type.isEnum();
    }

    private static boolean interesting(String value) {
        if (value == null) return false;
        String lower = value.toLowerCase(Locale.ROOT);
        for (String term : DISCOVERY_TERMS) if (lower.contains(term)) return true;
        return lower.startsWith("com.solipsys.");
    }

    private static String signature(Method method) {
        StringBuilder value = new StringBuilder(method.getName()).append('(');
        for (Class<?> parameter : method.getParameterTypes()) value.append(parameter.getName()).append(',');
        return value.append(')').append(method.getReturnType().getName()).toString();
    }
}
