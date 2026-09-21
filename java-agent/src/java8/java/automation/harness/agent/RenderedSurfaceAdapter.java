package automation.harness.agent;

import java.awt.Component;
import java.util.Map;

/** Adapter boundary for components that render semantic objects outside the AWT child tree. */
interface RenderedSurfaceAdapter {
    boolean supports(Component component);
    String name();
    Map<String, Object> inspect(Component component);
}
