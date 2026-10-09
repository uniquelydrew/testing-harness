package automation.harness.agent;

/** Deterministic smoke coverage for popup selection value precedence. */
public final class JavaFxPopupSelectionSmoke {
    private JavaFxPopupSelectionSmoke() { }

    public static void main(String[] args) {
        expect("B", JavaFxRecorder.choosePopupSelectionValue("B", null, "B", "A", "A"));
        expect("B", JavaFxRecorder.choosePopupSelectionValue(null, "B", "B", "A", "A"));
        expect("B", JavaFxRecorder.choosePopupSelectionValue(null, null, "B", "A", "A"));
        expect("B", JavaFxRecorder.choosePopupSelectionValue(null, null, null, "B", "A"));
        expect("B", JavaFxRecorder.choosePopupSelectionValue(null, null, null, null, "B"));
        expect(null, JavaFxRecorder.choosePopupSelectionValue(null, null, null, null, null));
    }

    private static void expect(Object expected, Object actual) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError("expected " + expected + " but got " + actual);
        }
    }
}
