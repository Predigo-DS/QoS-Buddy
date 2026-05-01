package com.project.backend.controllers;

import com.project.backend.services.implementations.TelemetryBufferService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/telemetry")
@RequiredArgsConstructor
public class TelemetryController {

    private final TelemetryBufferService telemetryBufferService;

    @PostMapping("/ingest")
    public ResponseEntity<Map<String, Object>> ingest(@RequestBody List<Map<String, Object>> rows) {
        telemetryBufferService.ingest(rows);
        return ResponseEntity.ok(Map.of(
                "accepted", rows.size(),
                "buffer_size", telemetryBufferService.size()
        ));
    }

    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() {
        return ResponseEntity.ok(Map.of(
                "buffer_size", telemetryBufferService.size(),
                "live_mode", telemetryBufferService.hasEnough(35)
        ));
    }

    @GetMapping("/latest")
    public ResponseEntity<List<Map<String, Object>>> latest(@RequestParam(defaultValue = "60") int n) {
        return ResponseEntity.ok(telemetryBufferService.getLatest(n));
    }
}
