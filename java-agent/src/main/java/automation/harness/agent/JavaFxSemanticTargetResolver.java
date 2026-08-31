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
        "TreeCell", "MenuItem", "Tab", "Control"
    );

    private JavaFxSemanticTargetResolver() { }

    public static Resolution resolveSemanticTarget(Object physicalTarget) {
        if (physicalTarget == null) {
            throw new IllegalArgumentException("physicalTarget is required");
        }
        Object current = physicalTarget;
        int depth = 0;
        while (current != null) {
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

    static boolean isInteractionBoundary(Object node) {
        Class<?> type = node.getClass();
        while (type != null) {
            if (BOUNDARY_NAMES.contains(type.getSimpleName())) {
                return true;
            }
            type = type.getSuperclass();
        }
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
