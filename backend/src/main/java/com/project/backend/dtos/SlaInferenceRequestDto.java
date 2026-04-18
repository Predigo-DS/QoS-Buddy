package com.project.backend.dtos;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
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
public class SlaInferenceRequestDto {

    @NotBlank
    @JsonProperty("run_id")
    private String runId;

    @NotBlank
    private String segment;

    @NotEmpty
    private List<Map<String, Object>> rows;

    @JsonProperty("use_all_windows")
    private Boolean useAllWindows;

    @Min(1)
    private Integer stride;

    @JsonProperty("sla_alert_threshold")
    @DecimalMin("0.0")
    @DecimalMax("1.0")
    private Double slaAlertThreshold;
}
