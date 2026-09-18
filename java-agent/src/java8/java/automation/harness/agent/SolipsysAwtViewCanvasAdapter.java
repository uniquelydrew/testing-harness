package automation.harness.agent;

import java.awt.Component;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Enumeration;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Runtime discovery adapter for com.solipsys.view.AWTViewCanvas. */
final class SolipsysAwtViewCanvasAdapter implements RenderedSurfaceAdapter {
    private static final String TARGET_CLASS = "com.solipsys.view.AWTViewCanvas";
    private static final int MAX_BACKING_OBJECTS = 24;
    private static final int MAX_SELECTION_ITEMS = 32;
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
        result.put("selection_model", selectionSnapshot(component));
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

    /** Targeted, bounded snapshot of Solipsys/TDF selection and rendered-object state. */
    static Map<String, Object> selectionSnapshot(Component component) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        Object view = findReferencedObject(component, "com.solipsys.view.View", "view");
        if (view == null) {
            result.put("available", false);
            return result;
        }
        result.put("available", true);
        result.put("view", describeRuntimeObject(view));
        String[] fields = {"viewSelectionManager", "viewObjects", "models", "drawables", "regions"};
        for (String fieldName : fields) {
            Object value = readNamedField(view, fieldName);
            if (value != null) {
                result.put(fieldName, describeValue(value));
                if ("regions".equals(fieldName)) result.put("region_elements", regionElementsSnapshot(value));
            }
        }
        Object manager = readNamedField(view, "viewSelectionManager");
        if (manager != null) {
            result.put("selection_manager", describeRuntimeObject(manager));
            result.put("selection_manager_state", targetedFields(manager));
            result.put("native_selection", nativeSelectionSnapshot(manager));
        }
        return result;
    }

    /**
     * Invoke only documented-by-runtime, public, zero-argument selection accessors.
     * These are read-only queries on DefaultSelectionManager and give us the actual
     * Selectable instances instead of inferring selection from private fields.
     */
    private static Map<String, Object> nativeSelectionSnapshot(Object manager) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("manager_type", manager.getClass().getName());
        Object count = invokePublicZeroArg(manager, "getNumberOfSelections");
        if (count != null) result.put("count", count);
        Object selections = invokePublicZeroArg(manager, "getSelections");
        if (selections != null) result.put("selections", describeEnumeration(selections));
        Object nodes = invokePublicZeroArg(manager, "getSelectionNodes");
        if (nodes != null) result.put("selection_nodes", describeEnumeration(nodes));
        Object detailed = invokePublicZeroArg(manager, "getDetailedSelections");
        if (detailed != null) result.put("detailed_selections", describeEnumeration(detailed));
        return result;
    }

    /**
     * Enumerate the live region container through its public read-only getElements() API.
     * Region containers are the first runtime structure discovered that can expose the
     * Selectable/rendered objects behind AWTViewCanvas without changing selection state.
     */
    private static Map<String, Object> regionElementsSnapshot(Object regions) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("container_type", regions.getClass().getName());
        Object elements = invokePublicZeroArg(regions, "getElements");
        if (elements == null) {
            result.put("available", false);
            return result;
        }
        result.put("available", true);
        result.put("elements", describeEnumeration(elements));
        result.put("rendered_candidates", renderedCandidatesFromRegions(regions));
        return result;
    }

    /** Extract concrete rendered Selectable/Region objects and safe identity/position accessors. */
    private static Map<String, Object> renderedCandidatesFromRegions(Object regions) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        Object raw = invokePublicZeroArg(regions, "getElements");
        if (!(raw instanceof Enumeration)) {
            result.put("available", false);
            return result;
        }
        result.put("available", true);
        List<Map<String, Object>> candidates = new ArrayList<Map<String, Object>>();
        Enumeration<?> enumeration = (Enumeration<?>)raw;
        int count = 0;
        while (enumeration.hasMoreElements() && count++ < MAX_SELECTION_ITEMS) {
            Object value = enumeration.nextElement();
            if (value == null) continue;
            Map<String, Object> candidate = describeSelectedObject(value);
            Map<String, Object> state = safeRenderedAccessors(value);
            if (!state.isEmpty()) candidate.put("rendered_state", state);
            candidates.add(candidate);
        }
        result.put("elements", candidates);
        result.put("sampled_count", Integer.valueOf(candidates.size()));
        return result;
    }

    private static Map<String, Object> safeRenderedAccessors(Object value) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        String[] accessors = {"getPosition", "getVelocityPosition", "getDrawLayer", "getTrackClass"};
        for (String accessor : accessors) {
            Object observed = invokePublicZeroArg(value, accessor);
            if (observed == null) continue;
            if (isSimple(observed.getClass())) result.put(accessor, String.valueOf(observed));
            else if (observed instanceof java.awt.Point) {
                java.awt.Point point = (java.awt.Point)observed;
                Map<String, Object> coordinates = new LinkedHashMap<String, Object>();
                coordinates.put("x", Integer.valueOf(point.x));
                coordinates.put("y", Integer.valueOf(point.y));
                result.put(accessor, coordinates);
            } else result.put(accessor, describeRuntimeObject(observed));
        }
        Object track = invokePublicZeroArg(value, "getTrack");
        if (track != null) {
            Map<String, Object> trackInfo = describeRuntimeObject(track);
            Map<String, Object> identity = new LinkedHashMap<String, Object>();
            String[] trackAccessors = {"getName", "getId", "getID", "getIdentifier", "getCallsign", "getTrackId", "getNumber", "getKey", "getDescription"};
            for (String accessor : trackAccessors) {
                Object observed = invokePublicZeroArg(track, accessor);
                if (observed != null && isSimple(observed.getClass())) identity.put(accessor, String.valueOf(observed));
            }
            if (!identity.isEmpty()) trackInfo.put("identity", identity);
            trackInfo.put("candidate_fields", shallowFieldTypes(track));
            Map<String, String> durableIdentity = trackIdentity(track);
            if (!durableIdentity.isEmpty()) {
                trackInfo.put("durable_identity_candidates", new LinkedHashMap<String, Object>(durableIdentity));
                String preferred = preferredIdentityKey(durableIdentity);
                if (preferred != null) {
                    trackInfo.put("preferred_identity_key", preferred);
                    trackInfo.put("preferred_identity_value", durableIdentity.get(preferred));
                } else {
                    trackInfo.put("identity_status", "no-trusted-instance-identity");
                }
            } else {
                trackInfo.put("identity_status", "no-trusted-instance-identity");
            }
            result.put("getTrack", trackInfo);
        }
        return result;
    }

    static Map<String, Object> selectedRenderedNode(Component surface) {
        Object view = findReferencedObject(surface, "com.solipsys.view.View", "view");
        if (view == null) return null;
        Object manager = readNamedField(view, "viewSelectionManager");
        if (manager == null) return null;
        Object selections = invokePublicZeroArg(manager, "getSelections");
        if (!(selections instanceof Enumeration)) return null;
        Enumeration<?> values = (Enumeration<?>)selections;
        while (values.hasMoreElements()) {
            Object selectable = values.nextElement();
            if (selectable == null) continue;
            Map<String, Object> node = renderedNode(surface, selectable);
            if (node != null) return node;
        }
        return null;
    }

    static Map<String, Object> resolveRenderedNode(
            Component surface, String renderedClass, String trackClass,
            String identityKey, String identityValue) {
        Map<String, Object> resolution = new LinkedHashMap<String, Object>();
        Object view = findReferencedObject(surface, "com.solipsys.view.View", "view");
        if (view == null) {
            resolution.put("resolution_status", "surface_not_present");
            return resolution;
        }
        Object regions = readNamedField(view, "regions");
        if (regions == null) {
            resolution.put("resolution_status", "surface_not_present");
            return resolution;
        }
        if (!isTrustedIdentityKey(identityKey) || identityValue == null || identityValue.isEmpty()) {
            resolution.put("resolution_status", "identity_unavailable");
            resolution.put("identity_key", identityKey);
            return resolution;
        }
        List<Object> candidates = new ArrayList<Object>();
        collectRenderedCandidates(regions, renderedClass, trackClass, identityKey, identityValue, 0, candidates);
        resolution.put("candidate_count", Integer.valueOf(candidates.size()));
        resolution.put("identity_key", identityKey);
        resolution.put("identity_value", identityValue);
        if (candidates.isEmpty()) {
            resolution.put("resolution_status", "not_present");
            return resolution;
        }
        if (candidates.size() > 1) {
            resolution.put("resolution_status", "ambiguous_identity");
            return resolution;
        }
        Map<String, Object> node = renderedNode(surface, candidates.get(0));
        if (node == null) {
            resolution.put("resolution_status", "identity_unavailable");
            return resolution;
        }
        node.put("resolution_status", "resolved");
        node.put("candidate_count", Integer.valueOf(1));
        return node;
    }

    private static void collectRenderedCandidates(
            Object container, String renderedClass, String trackClass,
            String identityKey, String identityValue, int depth, List<Object> result) {
        if (container == null || depth > 8) return;
        Object raw = invokePublicZeroArg(container, "getElements");
        if (!(raw instanceof Enumeration)) return;
        Enumeration<?> values = (Enumeration<?>)raw;
        while (values.hasMoreElements()) {
            Object value = values.nextElement();
            if (value == null) continue;
            Object track = invokePublicZeroArg(value, "getTrack");
            if (track != null && matchesRenderedIdentity(
                    value, track, renderedClass, trackClass, identityKey, identityValue)) addUniqueReference(result, value);
            collectRenderedCandidates(value, renderedClass, trackClass, identityKey, identityValue, depth + 1, result);
        }
    }

    private static void addUniqueReference(List<Object> values, Object candidate) {
        for (Object existing : values) if (existing == candidate) return;
        values.add(candidate);
    }

    private static boolean matchesRenderedIdentity(
            Object rendered, Object track, String renderedClass, String trackClass,
            String identityKey, String identityValue) {
        if (renderedClass != null && !renderedClass.isEmpty()
                && !renderedClass.equals(rendered.getClass().getName())) return false;
        if (trackClass != null && !trackClass.isEmpty()
                && !trackClass.equals(track.getClass().getName())) return false;
        if (!isTrustedIdentityKey(identityKey)) return false;
        Map<String, String> identity = trackIdentity(track);
        String actual = identity.get(identityKey);
        return actual != null && actual.equals(identityValue);
    }

    private static Map<String, Object> renderedNode(Component surface, Object selectable) {
        Object track = invokePublicZeroArg(selectable, "getTrack");
        if (track == null) return null;
        Map<String, String> identity = trackIdentity(track);
        String identityKey = preferredIdentityKey(identity);
        if (identityKey == null) return null;
        String identityValue = identity.get(identityKey);

        Map<String, Object> node = new LinkedHashMap<String, Object>();
        node.put("framework", "solipsys_rendered");
        node.put("class", selectable.getClass().getName());
        node.put("native_class", selectable.getClass().getName());
        node.put("role", "rendered_object");
        node.put("object_type", "custom");
        node.put("actions", java.util.Arrays.asList("resolve", "click"));
        node.put("ref", Integer.toHexString(System.identityHashCode(selectable)));
        List<String> lineage = new ArrayList<String>();
        List<Component> ancestors = new ArrayList<Component>();
        Component currentComponent = surface;
        while (currentComponent != null) {
            ancestors.add(currentComponent);
            currentComponent = currentComponent.getParent();
        }
        for (int index = ancestors.size() - 1; index >= 0; index--)
            lineage.add(ancestors.get(index).getClass().getName());
        lineage.add(selectable.getClass().getName());
        node.put("hierarchy", lineage);
        Map<String, Object> geometry = renderedGeometry(surface, selectable);
        if (geometry.get("bounds") != null) node.put("bounds", geometry.get("bounds"));
        java.awt.Window owner = javax.swing.SwingUtilities.getWindowAncestor(surface);
        if (owner != null) {
            String title = owner instanceof java.awt.Frame ? ((java.awt.Frame)owner).getTitle()
                    : owner instanceof java.awt.Dialog ? ((java.awt.Dialog)owner).getTitle()
                    : owner.getName();
            node.put("window", title);
            node.put("application", title);
        }
        Map<String, Object> parent = new LinkedHashMap<String, Object>();
        parent.put("native_class", surface.getClass().getName());
        if (surface.getName() != null && !surface.getName().isEmpty()) parent.put("accessible_id", surface.getName());
        parent.put("role", "canvas");
        node.put("parent", parent);
        Map<String, Object> state = new LinkedHashMap<String, Object>();
        state.put("present", true); state.put("visible", true); state.put("showing", surface.isShowing());
        node.put("state", state);
        Map<String, Object> properties = new LinkedHashMap<String, Object>();
        properties.put("process_id", RuntimeDiagnostics.snapshot().get("pid"));
        properties.put("render_surface_adapter", "solipsys_awt_view_canvas");
        properties.put("surface_native_class", surface.getClass().getName());
        properties.put("surface_accessible_id", surface.getName());
        properties.put("surface_ref", Integer.toHexString(System.identityHashCode(surface)));
        properties.put("rendered_object_ref", Integer.toHexString(System.identityHashCode(selectable)));
        properties.put("rendered_class", selectable.getClass().getName());
        properties.put("track_class", track.getClass().getName());
        properties.put("track_identity", new LinkedHashMap<String, Object>(identity));
        properties.put("track_identity_key", identityKey);
        properties.put("track_identity_value", identityValue);
        properties.put("geometry_source", geometry.get("source"));
        properties.put("geometry_is_fallback", geometry.get("fallback"));
        properties.put("identity_state", "candidate");
        properties.put("identity_rejection_reason", null);
        Object view = findReferencedObject(surface, "com.solipsys.view.View", "view");
        Object regions = view == null ? null : readNamedField(view, "regions");
        if (regions != null) {
            List<Object> visibleMatches = new ArrayList<Object>();
            collectRenderedCandidates(
                    regions, selectable.getClass().getName(), track.getClass().getName(),
                    identityKey, identityValue, 0, visibleMatches);
            properties.put("identity_visible_match_count", Integer.valueOf(visibleMatches.size()));
            properties.put("identity_unique_in_visible_scope", Boolean.valueOf(visibleMatches.size() == 1));
        }
        node.put("properties", properties);
        node.put("name", identityValue);
        return node;
    }

    /** Resolve current hit geometry after semantic identity resolution.
     *
     * Geometry is deliberately not used by {@link #matchesRenderedIdentity}; it
     * is transient state for highlighting and pointer injection only.
     */
    private static Map<String, Object> renderedGeometry(Component surface, Object selectable) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        java.awt.Point origin = new java.awt.Point(0, 0);
        try { javax.swing.SwingUtilities.convertPointToScreen(origin, surface); } catch (Throwable ignored) { }

        String[] boundsAccessors = {"getBounds", "getBoundingBox", "getDisplayBounds", "getSymbolBounds"};
        for (String accessor : boundsAccessors) {
            Object value = invokePublicZeroArg(selectable, accessor);
            java.awt.Rectangle bounds = rectangleValue(value);
            if (bounds == null || bounds.width <= 0 || bounds.height <= 0) continue;
            result.put("bounds", java.util.Arrays.asList(
                    Integer.valueOf(origin.x + bounds.x), Integer.valueOf(origin.y + bounds.y),
                    Integer.valueOf(bounds.width), Integer.valueOf(bounds.height)));
            result.put("source", accessor);
            result.put("fallback", Boolean.FALSE);
            return result;
        }

        Object selectionNode = invokePublicZeroArg(selectable, "getSelectionNode");
        if (selectionNode != null) {
            for (String accessor : boundsAccessors) {
                java.awt.Rectangle bounds = rectangleValue(invokePublicZeroArg(selectionNode, accessor));
                if (bounds == null || bounds.width <= 0 || bounds.height <= 0) continue;
                result.put("bounds", java.util.Arrays.asList(
                        Integer.valueOf(origin.x + bounds.x), Integer.valueOf(origin.y + bounds.y),
                        Integer.valueOf(bounds.width), Integer.valueOf(bounds.height)));
                result.put("source", "getSelectionNode." + accessor);
                result.put("fallback", Boolean.FALSE);
                return result;
            }
        }

        Object position = invokePublicZeroArg(selectable, "getPosition");
        if (position instanceof java.awt.Point) {
            java.awt.Point point = (java.awt.Point)position;
            int radius = 6;
            result.put("bounds", java.util.Arrays.asList(
                    Integer.valueOf(origin.x + point.x - radius), Integer.valueOf(origin.y + point.y - radius),
                    Integer.valueOf(radius * 2 + 1), Integer.valueOf(radius * 2 + 1)));
            result.put("source", "getPosition");
            result.put("fallback", Boolean.TRUE);
            return result;
        }
        result.put("source", "unavailable");
        result.put("fallback", Boolean.TRUE);
        return result;
    }

    private static java.awt.Rectangle rectangleValue(Object value) {
        if (value instanceof java.awt.Rectangle) return new java.awt.Rectangle((java.awt.Rectangle)value);
        if (value instanceof java.awt.geom.Rectangle2D) {
            java.awt.geom.Rectangle2D rectangle = (java.awt.geom.Rectangle2D)value;
            return new java.awt.Rectangle(
                    (int)Math.floor(rectangle.getX()), (int)Math.floor(rectangle.getY()),
                    Math.max(1, (int)Math.ceil(rectangle.getWidth())),
                    Math.max(1, (int)Math.ceil(rectangle.getHeight())));
        }
        return null;
    }

    private static Map<String, String> trackIdentity(Object track) {
        Map<String, String> result = new LinkedHashMap<String, String>();
        String[] accessors = {
            "getTrackId", "getTrackID", "getIdentifier", "getID", "getId",
            "getKey", "getNumber", "getTrackNumber", "getCallsign", "getName", "getDescription"
        };
        for (String accessor : accessors) {
            Object observed = invokePublicZeroArg(track, accessor);
            if (observed != null && isSimple(observed.getClass())) {
                String value = String.valueOf(observed);
                if (!value.isEmpty()) result.put(accessor, value);
            }
        }
        Class<?> current = track.getClass();
        while (current != null) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); } catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                if (!isInstanceIdentityField(field)) continue;
                Object observed = readField(field, track);
                if (observed != null && isSimple(observed.getClass())) {
                    String value = String.valueOf(observed);
                    if (!value.isEmpty()) result.put("field:" + field.getName(), value);
                }
            }
            current = current.getSuperclass();
        }
        return result;
    }

    private static String preferredIdentityKey(Map<String, String> identity) {
        String[] preferred = {
            "getTrackId", "getTrackID", "getIdentifier", "getID", "getId",
            "getKey", "getNumber", "getTrackNumber", "getCallsign"
        };
        for (String key : preferred) if (identity.containsKey(key)) return key;
        String[] fieldPreferred = {
            "field:identity", "field:trackId", "field:trackID", "field:identifier",
            "field:id", "field:ID", "field:key", "field:trackNumber",
            "field:number", "field:callsign"
        };
        for (String key : fieldPreferred) if (identity.containsKey(key)) return key;
        return null;
    }

    private static boolean isTrustedIdentityKey(String key) {
        if (key == null || key.isEmpty()) return false;
        String[] accessors = {
            "getTrackId", "getTrackID", "getIdentifier", "getID", "getId",
            "getKey", "getNumber", "getTrackNumber", "getCallsign"
        };
        for (String accessor : accessors) if (accessor.equals(key)) return true;
        if (!key.startsWith("field:")) return false;
        String fieldName = key.substring("field:".length());
        try {
            Class<?> ignored = String.class; // keeps this helper Java-8-simple; field trust is name based here.
            String lower = fieldName.toLowerCase(Locale.ROOT);
            return "identity".equals(lower) || "id".equals(lower) || "identifier".equals(lower)
                    || "key".equals(lower) || "number".equals(lower) || "callsign".equals(lower)
                    || "trackid".equals(lower) || "track_id".equals(lower)
                    || "tracknumber".equals(lower) || "track_number".equals(lower)
                    || lower.endsWith("identity") || lower.endsWith("identifier")
                    || lower.endsWith("trackid") || lower.endsWith("track_id")
                    || lower.endsWith("tracknumber") || lower.endsWith("track_number")
                    || lower.endsWith("callsign");
        } catch (Throwable ignored) { return false; }
    }

    private static boolean isInstanceIdentityField(Field field) {
        int modifiers = field.getModifiers();
        if (Modifier.isStatic(modifiers)) return false;
        if (field.isSynthetic()) return false;
        String raw = field.getName();
        String name = raw.toLowerCase(Locale.ROOT);
        if ("identity".equals(name) || "id".equals(name) || "identifier".equals(name)
                || "key".equals(name) || "number".equals(name) || "callsign".equals(name)
                || "trackid".equals(name) || "track_id".equals(name)
                || "tracknumber".equals(name) || "track_number".equals(name)) return true;
        return name.endsWith("identity") || name.endsWith("identifier")
                || name.endsWith("trackid") || name.endsWith("track_id")
                || name.endsWith("tracknumber") || name.endsWith("track_number")
                || name.endsWith("callsign");
    }

    private static Object invokePublicZeroArg(Object target, String name) {
        try {
            Method method = target.getClass().getMethod(name);
            if (!Modifier.isPublic(method.getModifiers()) || method.getParameterTypes().length != 0) return null;
            return method.invoke(target);
        } catch (Throwable ignored) { return null; }
    }

    private static Map<String, Object> describeEnumeration(Object value) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("runtime_type", value.getClass().getName());
        if (!(value instanceof Enumeration)) {
            result.put("value", describeValue(value));
            return result;
        }
        List<Map<String, Object>> elements = new ArrayList<Map<String, Object>>();
        Enumeration<?> enumeration = (Enumeration<?>)value;
        int count = 0;
        while (enumeration.hasMoreElements() && count++ < MAX_SELECTION_ITEMS) {
            Object element = enumeration.nextElement();
            if (element != null) elements.add(describeSelectedObject(element));
        }
        result.put("elements", elements);
        result.put("sampled_count", Integer.valueOf(elements.size()));
        return result;
    }

    private static Map<String, Object> describeSelectedObject(Object value) {
        Map<String, Object> item = describeRuntimeObject(value);
        item.put("candidate_fields", shallowFieldTypes(value));
        String[] accessors = {
            "getName", "getId", "getID", "getIdentifier", "getObject", "getModel",
            "getTrack", "getTrackId", "getCallsign", "getDescription"
        };
        Map<String, Object> identity = new LinkedHashMap<String, Object>();
        for (String accessor : accessors) {
            Object result = invokePublicZeroArg(value, accessor);
            if (result == null || !isSimple(result.getClass())) continue;
            identity.put(accessor, String.valueOf(result));
        }
        if (!identity.isEmpty()) item.put("identity", identity);
        return item;
    }

    private static Object findReferencedObject(Object target, String exactType, String fieldTerm) {
        if (target == null) return null;
        Class<?> current = target.getClass();
        while (current != null) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); } catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                Object value = readField(field, target);
                if (value == null) continue;
                if (exactType.equals(value.getClass().getName())) return value;
                if (field.getName().toLowerCase(Locale.ROOT).contains(fieldTerm) &&
                        value.getClass().getName().startsWith("com.solipsys.")) return value;
            }
            current = current.getSuperclass();
        }
        return null;
    }

    private static Object readNamedField(Object target, String name) {
        Class<?> current = target.getClass();
        while (current != null) {
            try { return readField(current.getDeclaredField(name), target); }
            catch (Throwable ignored) { current = current.getSuperclass(); }
        }
        return null;
    }

    private static Map<String, Object> describeRuntimeObject(Object value) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("runtime_type", value.getClass().getName());
        item.put("runtime_ref", Integer.toHexString(System.identityHashCode(value)));
        item.put("interfaces", interfaces(value.getClass()));
        item.put("candidate_methods", candidateMethods(value.getClass()));
        return item;
    }

    private static Object describeValue(Object value) {
        Map<String, Object> item = describeRuntimeObject(value);
        List<Map<String, Object>> elements = new ArrayList<Map<String, Object>>();
        if (value.getClass().isArray()) {
            int length = Math.min(Array.getLength(value), MAX_SELECTION_ITEMS);
            item.put("size", Integer.valueOf(Array.getLength(value)));
            for (int i = 0; i < length; i++) {
                Object element = Array.get(value, i);
                if (element != null) elements.add(describeRuntimeObject(element));
            }
        } else if (value instanceof Collection) {
            Collection<?> collection = (Collection<?>)value;
            item.put("size", Integer.valueOf(collection.size()));
            int count = 0;
            for (Object element : collection) {
                if (count++ >= MAX_SELECTION_ITEMS) break;
                if (element != null) elements.add(describeRuntimeObject(element));
            }
        } else if (value instanceof Map) {
            Map<?, ?> map = (Map<?, ?>)value;
            item.put("size", Integer.valueOf(map.size()));
            int count = 0;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (count++ >= MAX_SELECTION_ITEMS) break;
                Object element = entry.getValue();
                if (element != null) elements.add(describeRuntimeObject(element));
            }
        }
        if (!elements.isEmpty()) item.put("elements", elements);
        return item;
    }

    private static List<Map<String, Object>> targetedFields(Object target) {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>();
        Class<?> current = target.getClass();
        int count = 0;
        while (current != null && count < MAX_SELECTION_ITEMS) {
            Field[] fields;
            try { fields = current.getDeclaredFields(); } catch (Throwable ignored) { fields = new Field[0]; }
            for (Field field : fields) {
                if (count >= MAX_SELECTION_ITEMS) break;
                String key = field.getName().toLowerCase(Locale.ROOT);
                if (!(key.contains("select") || key.contains("rollover") || key.contains("pick") ||
                        key.contains("object") || key.contains("model") || key.contains("view"))) continue;
                Map<String, Object> item = describeField(field, target);
                Object value = readField(field, target);
                if (value != null && !isSimple(value.getClass())) item.put("value", describeValue(value));
                result.add(item);
                count++;
            }
            current = current.getSuperclass();
        }
        return result;
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
