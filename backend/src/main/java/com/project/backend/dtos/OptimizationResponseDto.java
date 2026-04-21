package com.project.backend.dtos;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class OptimizationResponseDto {

    @JsonProperty("telemetry_summary")
    private Map<String, Object> telemetrySummary;

    @JsonProperty("anomaly_response")
    private Object anomalyResponse;

    @JsonProperty("sla_response")
    private Object slaResponse;

    @JsonProperty("optimization_decision")
    private Object optimizationDecision;

    @JsonProperty("tool_trace")
    private Object toolTrace;

    @JsonProperty("mock_mode")
    private boolean mockMode;
}
