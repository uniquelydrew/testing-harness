package automation.harness.agent;

import java.awt.Component;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Ordered registry of rendered-surface adapters. */
final class RenderedSurfaceRegistry {
    private static final List<RenderedSurfaceAdapter> ADAPTERS = Collections.unmodifiableList(
        Arrays.<RenderedSurfaceAdapter>asList(new SolipsysAwtViewCanvasAdapter())
    );

    private RenderedSurfaceRegistry() { }

    static RenderedSurfaceAdapter adapterFor(Component component) {
        if (component == null) return null;
        for (RenderedSurfaceAdapter adapter : ADAPTERS) if (adapter.supports(component)) return adapter;
        return null;
    }

    static Component nearestSurface(Component component) {
        Component current = component;
        while (current != null) {
            if (adapterFor(current) != null) return current;
            current = current.getParent();
        }
        return null;
    }
}
