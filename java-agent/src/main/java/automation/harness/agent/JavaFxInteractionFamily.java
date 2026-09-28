package automation.harness.agent;

/** Native JavaFX counterpart to the Python interaction-family contract. */
enum JavaFxInteractionFamily {
    POPUP_SELECTOR, MENU_OWNER, SELECTABLE, TEXT_INPUT, VALUE_CONTROL, COLLECTION_SELECTOR;

    static JavaFxInteractionFamily forNode(Object node) {
        if (node == null) return null;
        if (is(node, "ComboBox") || is(node, "ChoiceBox") || is(node, "DatePicker") || is(node, "ColorPicker")) return POPUP_SELECTOR;
        if (is(node, "MenuBar") || is(node, "MenuButton") || is(node, "SplitMenuButton") || is(node, "Menu") || is(node, "ContextMenu")) return MENU_OWNER;
        if (is(node, "ToggleButton") || is(node, "CheckBox") || is(node, "RadioButton")) return SELECTABLE;
        if (is(node, "TextInputControl") || is(node, "TextField") || is(node, "PasswordField") || is(node, "TextArea")) return TEXT_INPUT;
        if (is(node, "Slider") || is(node, "Spinner")) return VALUE_CONTROL;
        if (is(node, "ListView") || is(node, "TreeView") || is(node, "TableView") || is(node, "TreeTableView") || is(node, "TabPane")) return COLLECTION_SELECTOR;
        return null;
    }

    private static boolean is(Object node, String simpleName) {
        for (Class<?> type = node.getClass(); type != null; type = type.getSuperclass()) {
            if (simpleName.equals(type.getSimpleName())) return true;
        }
        return false;
    }
}
