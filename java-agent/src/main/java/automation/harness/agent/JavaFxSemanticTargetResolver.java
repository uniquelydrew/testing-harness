package automation.harness.agent;

import java.lang.reflect.Method;
import java.util.Set;

/**
 * Resolves a JavaFX physical event node to the closest semantic interaction
 * boundary without linking the agent itself against a particular OpenJFX
 * distribution.  The live adapter calls this while it still owns the scene
 * graph; only a serialized snapshot crosses the Python boundary.
 */
public final class JavaFxSemanticTargetResolver {
    private static final Set<String> BOUNDARY_NAMES = Set.of(
        "Button", "ToggleButton", "CheckBox", "RadioButton", "Hyperlink",
        "TextField", "PasswordField", "TextArea", "ComboBox", "ChoiceBox",
        "Spinner", "DatePicker", "Slider", "ListCell", "TableCell",
        "TreeCell", "MenuBar", "MenuButton", "MenuItem", "MenuItemContainer", "Tab"
    );
    private static final Set<String> LOGICAL_MENU_NAMES = Set.of(
        "Menu", "MenuItem", "CustomMenuItem", "CheckMenuItem", "RadioMenuItem"
    );

    private JavaFxSemanticTargetResolver() { }

    public static Resolution resolveSemanticTarget(Object physicalTarget) {
        if (physicalTarget == null) {
            throw new IllegalArgumentException("physicalTarget is required");
        }
        Object current = physicalTarget;
        int depth = 0;
        while (current != null) {
            Object logicalMenu = logicalMenuDelegate(current);
            if (logicalMenu != null) {
                return new Resolution(
                    physicalTarget, logicalMenu, depth,
                    depth > 0 ? "logical_menu_from_ancestor" : "logical_menu_from_skin"
                );
            }
            if (isInteractionBoundary(current)) {
                return new Resolution(physicalTarget, current, depth, depth > 0 ? "interactive_ancestor" : "physical_target");
            }
            current = parentOf(current);
            depth++;
        }
        // A standalone Label, Text, or custom pane remains meaningful when no
        // recognized control encloses it.  We intentionally never promote it
        // to a layout ancestor or scene root.
        return new Resolution(physicalTarget, physicalTarget, 0, "no_interactive_ancestor");
    }

    /**
     * JavaFX renders logical Menu/MenuItem values through disposable skin
     * controls such as MenuBarButton and ContextMenuContent.MenuItemContainer.
     * Those skin objects are useful only while the popup exists.  Resolve the
     * backing logical menu object at capture time so repository identity never
     * depends on com.sun.javafx.* implementation classes.
     */
    private static Object logicalMenuDelegate(Object candidate) {
        String className = candidate.getClass().getName();
        if (!className.startsWith("com.sun.javafx.")) return null;
        for (String accessor : new String[]{"getItem", "getMenu"}) {
            Object value = invokeNoArg(candidate, accessor);
            if (value != null && isLogicalMenuObject(value)) return value;
        }
        return null;
    }

    private static boolean isLogicalMenuObject(Object value) {
        Class<?> type = value.getClass();
        while (type != null) {
            if (LOGICAL_MENU_NAMES.contains(type.getSimpleName())) return true;
            type = type.getSuperclass();
        }
        return false;
    }

    static boolean isInteractionBoundary(Object node) {
        if (isLogicalMenuObject(node)) return true;
        Class<?> type = node.getClass();
        boolean controlSubclass = false;
        while (type != null) {
            if (BOUNDARY_NAMES.contains(type.getSimpleName())) {
                return true;
            }
            if ("Control".equals(type.getSimpleName())) controlSubclass = true;
            type = type.getSuperclass();
        }
        String className = node.getClass().getName();
        if (controlSubclass && !className.startsWith("javafx.") && !className.startsWith("com.sun.")) return true;
        // Applications can opt in without adding an agent-specific component
        // type; this property is intentionally evaluated at the source.
        Object marker = property(node, "getProperties", "automation.semanticBoundary");
        if (Boolean.TRUE.equals(marker)) return true;
        // A deliberately interactive custom Pane is semantically meaningful;
        // ordinary layout panes remain non-boundaries.
        return property(node, "getProperties", "automation.actions") != null || hasMouseHandler(node);
    }

    private static Object parentOf(Object node) {
        try {
            Method method = node.getClass().getMethod("getParent");
            return method.invoke(node);
        } catch (ReflectiveOperationException ignored) {
            return null;
        }
    }

    private static Object invokeNoArg(Object target, String methodName) {
        try {
            Method method = target.getClass().getMethod(methodName);
            if (method.getParameterCount() != 0) return null;
            return method.invoke(target);
        } catch (ReflectiveOperationException | RuntimeException ignored) {
            return null;
        }
    }

    private static boolean hasMouseHandler(Object node) {
        try {
            Object handler = node.getClass().getMethod("getOnMouseClicked").invoke(node);
            return handler != null;
        } catch (ReflectiveOperationException ignored) {
            return false;
        }
    }

    @SuppressWarnings("unchecked")
    private static Object property(Object node, String methodName, String key) {
        try {
            Object properties = node.getClass().getMethod(methodName).invoke(node);
            if (properties instanceof java.util.Map<?, ?> map) {
                return ((java.util.Map<String, Object>) map).get(key);
            }
        } catch (ReflectiveOperationException ignored) {
            // Not a JavaFX Node; it cannot participate in JavaFX promotion.
        }
        return null;
    }

    public record Resolution(Object physicalTarget, Object semanticTarget, int descendantDepth, String reason) {
        public boolean promoted() { return physicalTarget != semanticTarget; }
    }
}
