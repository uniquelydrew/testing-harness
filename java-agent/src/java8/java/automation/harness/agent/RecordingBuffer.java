package automation.harness.agent;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

final class RecordingBuffer {
    private static final int LIMIT = 256;
    private final ArrayDeque<Map<String, Object>> events = new ArrayDeque<Map<String, Object>>(LIMIT);
    private boolean active;

    synchronized void start() { events.clear(); active = true; notifyAll(); }
    synchronized List<Map<String, Object>> stop() { active = false; notifyAll(); return drain(); }
    synchronized boolean active() { return active; }
    synchronized void offer(Map<String, Object> event) {
        if (!active) return;
        String type = String.valueOf(event.get("type"));
        if (type.equals("mouse_moved") || type.equals("hover") || type.equals("layout") || type.equals("css") || type.equals("skin") || type.equals("pressed")) return;
        if (events.size() == LIMIT) events.removeFirst();
        events.addLast(event); notifyAll();
    }
    synchronized List<Map<String, Object>> drain() {
        List<Map<String, Object>> result = new ArrayList<Map<String, Object>>(events); events.clear(); return result;
    }
    synchronized List<Map<String, Object>> awaitAndDrain(long timeoutMillis) {
        long bounded = Math.max(0L, Math.min(timeoutMillis, 5000L));
        long deadline = System.nanoTime() + bounded * 1000000L;
        while (active && events.isEmpty() && bounded > 0L) {
            try { wait(bounded); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); break; }
            bounded = Math.max(0L, (deadline - System.nanoTime()) / 1000000L);
        }
        return drain();
    }
}
