package com.project.backend.services.interfaces;

import com.fasterxml.jackson.databind.JsonNode;
import com.project.backend.dtos.AnomalyInferenceRequestDto;
import com.project.backend.dtos.SlaInferenceRequestDto;

public interface AiInferenceService {

    JsonNode getAnomalyMetadata();

    JsonNode predictAnomaly(AnomalyInferenceRequestDto request);

    JsonNode getSlaMetadata();

    JsonNode predictSla(SlaInferenceRequestDto request);
}
