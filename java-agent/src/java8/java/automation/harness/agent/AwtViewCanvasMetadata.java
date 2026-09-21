package automation.harness.agent;

import java.awt.Component;
import java.awt.Point;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.swing.SwingUtilities;

/** Stable metadata helper for Solipsys AWTViewCanvas surfaces. */
final class AwtViewCanvasMetadata {
    private AwtViewCanvasMetadata() { }

    static boolean isAwtViewCanvas(Component component) {
        if (component == null) return false;
        Class<?> type = component.getClass();
        while (type != null) {
            if ("com.solipsys.view.AWTViewCanvas".equals(type.getName())) return true;
            type = type.getSuperclass();
        }
        return false;
    }

    static Map<String, Object> describe(Component component) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("adapter", "solipsys_awt_view_canvas");
        result.put("surface_class", component.getClass().getName());
        result.put("surface_ref", Integer.toHexString(System.identityHashCode(component)));
        result.put("render_strategy", "solipsys_rendered_surface");
        Point origin = new Point(0, 0);
        try { SwingUtilities.convertPointToScreen(origin, component); } catch (RuntimeException ignored) { }
        List<Object> bounds = new ArrayList<Object>();
        bounds.add(origin.x); bounds.add(origin.y); bounds.add(component.getWidth()); bounds.add(component.getHeight());
        result.put("surface_bounds", bounds);
        return result;
    }
}
