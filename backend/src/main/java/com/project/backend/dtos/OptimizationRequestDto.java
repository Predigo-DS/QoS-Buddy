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
public class OptimizationRequestDto {

    @JsonProperty("anomaly_result")
    private Object anomalyResult;

    @JsonProperty("sla_result")
    private Object slaResult;

    @JsonProperty("avg_30s")
    private Map<String, Double> avg30s;

    private String device;
    private String context;
}
