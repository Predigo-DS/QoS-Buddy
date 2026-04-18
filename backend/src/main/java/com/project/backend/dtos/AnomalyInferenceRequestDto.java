package com.project.backend.dtos;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotEmpty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AnomalyInferenceRequestDto {

    @NotEmpty
    private List<Map<String, Object>> rows;

    @Min(1)
    private Integer stride;

    @JsonProperty("threshold_name")
    private String thresholdName;
}
