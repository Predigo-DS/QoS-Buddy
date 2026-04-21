package com.project.backend.controllers;

import com.fasterxml.jackson.databind.JsonNode;
import com.project.backend.dtos.AnomalyInferenceRequestDto;
import com.project.backend.dtos.OptimizationResponseDto;
import com.project.backend.dtos.SlaInferenceRequestDto;
import com.project.backend.services.interfaces.AiInferenceService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/ai")
@RequiredArgsConstructor
public class AiInferenceController {

    private final AiInferenceService aiInferenceService;

    @GetMapping("/anomaly/metadata")
    public ResponseEntity<JsonNode> getAnomalyMetadata() {
        return ResponseEntity.ok(aiInferenceService.getAnomalyMetadata());
    }

    @PostMapping("/anomaly/predict")
    public ResponseEntity<JsonNode> predictAnomaly(@Valid @RequestBody AnomalyInferenceRequestDto request) {
        return ResponseEntity.ok(aiInferenceService.predictAnomaly(request));
    }

    @GetMapping("/sla/metadata")
    public ResponseEntity<JsonNode> getSlaMetadata() {
        return ResponseEntity.ok(aiInferenceService.getSlaMetadata());
    }

    @PostMapping("/sla/predict")
    public ResponseEntity<JsonNode> predictSla(@Valid @RequestBody SlaInferenceRequestDto request) {
        return ResponseEntity.ok(aiInferenceService.predictSla(request));
    }

    @PostMapping("/optimize/mock")
    public ResponseEntity<OptimizationResponseDto> optimizeMock() {
        return ResponseEntity.ok(aiInferenceService.runMockOptimization());
    }
}
