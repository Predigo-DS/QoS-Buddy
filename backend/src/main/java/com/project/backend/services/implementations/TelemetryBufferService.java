package com.project.backend.services.implementations;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class TelemetryBufferService {

    private static final int MAX_ROWS = 1000;

    private final CopyOnWriteArrayList<Map<String, Object>> buffer = new CopyOnWriteArrayList<>();

    public void ingest(List<Map<String, Object>> rows) {
        buffer.addAll(rows);
        int excess = buffer.size() - MAX_ROWS;
        if (excess > 0) {
            buffer.subList(0, excess).clear();
        }
    }

    public List<Map<String, Object>> getLatest(int n) {
        List<Map<String, Object>> snapshot = new ArrayList<>(buffer);
        int from = Math.max(0, snapshot.size() - n);
        return snapshot.subList(from, snapshot.size());
    }

    public int size() {
        return buffer.size();
    }

    public boolean hasEnough(int minRows) {
        return buffer.size() >= minRows;
    }
}
