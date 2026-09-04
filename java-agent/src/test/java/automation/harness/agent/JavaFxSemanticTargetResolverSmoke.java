package automation.harness.agent;

import java.util.HashMap;
import java.util.Map;

/** Dependency-free coverage of source-side JavaFX boundary decisions. */
public final class JavaFxSemanticTargetResolverSmoke {
    private JavaFxSemanticTargetResolverSmoke() { }

    static class Node {
        private final Node parent;
        private final Map<String, Object> properties = new HashMap<>();
        Node(Node parent) { this.parent = parent; }
        public Node getParent() { return parent; }
        public Map<String, Object> getProperties() { return properties; }
        public Object getOnMouseClicked() { return null; }
    }
    static class Button extends Node { Button(Node parent) { super(parent); } }
    static class CheckBox extends Node { CheckBox(Node parent) { super(parent); } }
    static class RadioButton extends Node { RadioButton(Node parent) { super(parent); } }
    static class Hyperlink extends Node { Hyperlink(Node parent) { super(parent); } }
    static class TextField extends Node { TextField(Node parent) { super(parent); } }
    static class ComboBox extends Node { ComboBox(Node parent) { super(parent); } }
    static class MenuButton extends Node { MenuButton(Node parent) { super(parent); } }
    static class Tab extends Node { Tab(Node parent) { super(parent); } }
    static class Control extends Node { Control(Node parent) { super(parent); } }
    static class CustomControl extends Control { CustomControl(Node parent) { super(parent); } }
    static class Skin extends Node { Skin(Node parent) { super(parent); } }
    static class Text extends Node { Text(Node parent) { super(parent); } }
    static class Label extends Node { Label(Node parent) { super(parent); } }
    static class Pane extends Node {
        Pane(Node parent) { super(parent); }
        @Override public Object getOnMouseClicked() { return new Object(); }
    }

    public static void main(String[] arguments) {
        Button button = new Button(null);
        assertTarget(new Text(new Skin(button)), button, 2);
        CheckBox checkBox = new CheckBox(null);
        assertTarget(new Text(checkBox), checkBox, 1);
        RadioButton radio = new RadioButton(null);
        assertTarget(new Text(radio), radio, 1);
        Hyperlink hyperlink = new Hyperlink(null);
        assertTarget(new Text(hyperlink), hyperlink, 1);
        TextField textField = new TextField(null);
        assertTarget(new Text(new Skin(textField)), textField, 2);
        ComboBox comboBox = new ComboBox(null);
        assertTarget(new Text(new Skin(comboBox)), comboBox, 2);
        MenuButton menu = new MenuButton(null);
        assertTarget(new Label(menu), menu, 1);
        Tab tab = new Tab(null);
        assertTarget(new Text(tab), tab, 1);
        Text standalone = new Text(null);
        assertTarget(standalone, standalone, 0);
        Label label = new Label(null);
        assertTarget(label, label, 0);
        Pane pane = new Pane(null);
        assertTarget(new Text(pane), pane, 1);
        CustomControl custom = new CustomControl(null);
        assertTarget(new Text(custom), custom, 1);
        Button outer = new Button(null), inner = new Button(outer);
        assertTarget(new Text(inner), inner, 1);
    }

    private static void assertTarget(Node physical, Node semantic, int depth) {
        JavaFxSemanticTargetResolver.Resolution result = JavaFxSemanticTargetResolver.resolveSemanticTarget(physical);
        if (result.semanticTarget() != semantic || result.descendantDepth() != depth) {
            throw new AssertionError("unexpected target resolution: " + result);
        }
    }
}
