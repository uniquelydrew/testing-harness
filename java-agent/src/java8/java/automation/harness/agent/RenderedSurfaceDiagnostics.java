package automation.harness.agent;

import java.awt.Component;
import java.awt.EventQueue;
import java.awt.Point;
import java.awt.Rectangle;
import java.awt.Window;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.swing.SwingUtilities;

/** Safe diagnostic inspection of rendered surfaces at a screen coordinate. */
final class RenderedSurfaceDiagnostics {
    private RenderedSurfaceDiagnostics() { }

    static Map<String, Object> inspectAt(final double screenX, final double screenY) {
        final Map<String, Object>[] result = new Map[]{null};
        runOnEdtAndWait(new Runnable() {
            public void run() {
                Component hit = componentAt((int)Math.round(screenX), (int)Math.round(screenY));
                Component surface = RenderedSurfaceRegistry.nearestSurface(hit);
                if (surface == null) throw new IllegalArgumentException("no registered rendered surface found at screen coordinate");
                RenderedSurfaceAdapter adapter = RenderedSurfaceRegistry.adapterFor(surface);
                Map<String, Object> payload = new LinkedHashMap<String, Object>(adapter.inspect(surface));
                payload.put("screen_point", point((int)Math.round(screenX), (int)Math.round(screenY)));
                Point origin = new Point(0, 0);
                SwingUtilities.convertPointToScreen(origin, surface);
                payload.put("canvas_relative_point", point((int)Math.round(screenX) - origin.x, (int)Math.round(screenY) - origin.y));
                payload.put("surface_bounds", bounds(origin.x, origin.y, surface.getWidth(), surface.getHeight()));
                payload.put("surface_component_path", componentPath(surface));
                result[0] = payload;
            }
        });
        return result[0];
    }

    private static Component componentAt(int screenX, int screenY) {
        Window[] windows = Window.getWindows();
        for (int pass = 0; pass < 2; pass++) {
            for (int index = windows.length - 1; index >= 0; index--) {
                Window window = windows[index];
                if (!window.isShowing() || (pass == 0 && !window.isActive())) continue;
                Rectangle bounds = window.getBounds();
                if (!bounds.contains(screenX, screenY)) continue;
                Component deepest = SwingUtilities.getDeepestComponentAt(window, screenX - bounds.x, screenY - bounds.y);
                return deepest != null ? deepest : window;
            }
        }
        return null;
    }

    private static List<Object> point(int x, int y) {
        List<Object> result = new ArrayList<Object>();
        result.add(x); result.add(y); return result;
    }

    private static List<Object> bounds(int x, int y, int width, int height) {
        List<Object> result = new ArrayList<Object>();
        result.add(x); result.add(y); result.add(width); result.add(height); return result;
    }

    private static String componentPath(Component component) {
        List<String> segments = new ArrayList<String>();
        Component current = component;
        while (current != null) {
            segments.add(0, current.getClass().getName());
            current = current.getParent();
        }
        StringBuilder path = new StringBuilder();
        for (String segment : segments) {
            if (path.length() > 0) path.append('/');
            path.append(segment);
        }
        return path.toString();
    }

    private static void runOnEdtAndWait(Runnable work) {
        if (EventQueue.isDispatchThread()) { work.run(); return; }
        try { EventQueue.invokeAndWait(work); }
        catch (Exception exception) { throw new IllegalStateException("rendered surface inspection failed", exception); }
    }
}
