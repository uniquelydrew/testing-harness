package automation.harness.agent;

import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/** Races Swing/AWT and JavaFX in-process capture without guessing frameworks. */
final class AgentCapture {
    private AgentCapture() { }

    static Map<String, Object> captureNextClick(long timeoutMillis) throws Exception {
        CompletableFuture<Map<String, Object>> swing = SwingRecorder.beginCapture();
        CompletableFuture<Map<String, Object>> javafx = JavaFxRecorder.beginCapture();
        try {
            return CompletableFuture.anyOf(swing, javafx)
                .thenApply(value -> (Map<String, Object>) value)
                .get(timeoutMillis, TimeUnit.MILLISECONDS);
        } finally {
            SwingRecorder.endCapture(swing);
            JavaFxRecorder.endCapture(javafx);
        }
    }

    static Map<String, Object> hitTest(double x, double y) {
        try { return SwingRecorder.hitTest(x, y); }
        catch (RuntimeException ignored) { return JavaFxRecorder.hitTest(x, y); }
    }
}
