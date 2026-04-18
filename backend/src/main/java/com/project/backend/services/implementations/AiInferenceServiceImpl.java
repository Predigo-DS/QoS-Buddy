package com.project.backend.services.implementations;

import com.fasterxml.jackson.databind.JsonNode;
import com.project.backend.dtos.AnomalyInferenceRequestDto;
import com.project.backend.dtos.SlaInferenceRequestDto;
import com.project.backend.services.interfaces.AiInferenceService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

@Service
@RequiredArgsConstructor
public class AiInferenceServiceImpl implements AiInferenceService {

    private final RestClient.Builder restClientBuilder;

    @Value("${app.ai.anomaly-base-url:http://localhost:8003}")
    private String anomalyBaseUrl;

    @Value("${app.ai.sla-base-url:http://localhost:8004}")
    private String slaBaseUrl;

    @Override
    public JsonNode getAnomalyMetadata() {
        return get(anomalyBaseUrl + "/metadata");
    }

    @Override
    public JsonNode predictAnomaly(AnomalyInferenceRequestDto request) {
        return post(anomalyBaseUrl + "/predict", request);
    }

    @Override
    public JsonNode getSlaMetadata() {
        return get(slaBaseUrl + "/metadata");
    }

    @Override
    public JsonNode predictSla(SlaInferenceRequestDto request) {
        return post(slaBaseUrl + "/predict", request);
    }

    private JsonNode get(String url) {
        RestClient client = restClientBuilder.build();
        try {
            return client.get()
                    .uri(url)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientResponseException ex) {
            throw mapDownstreamException(ex);
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "AI inference service unavailable: " + ex.getMessage());
        }
    }

    private JsonNode post(String url, Object payload) {
        RestClient client = restClientBuilder.build();
        try {
            return client.post()
                    .uri(url)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientResponseException ex) {
            throw mapDownstreamException(ex);
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "AI inference service unavailable: " + ex.getMessage());
        }
    }

    private ResponseStatusException mapDownstreamException(RestClientResponseException ex) {
        HttpStatus status = HttpStatus.resolve(ex.getStatusCode().value());
        String body = ex.getResponseBodyAsString();
        String message = (body == null || body.isBlank())
                ? "AI inference request failed"
                : body;

        if (status == null) {
            status = HttpStatus.BAD_GATEWAY;
        }
        return new ResponseStatusException(status, message);
    }
}
