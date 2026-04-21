package com.project.backend.services.implementations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.backend.dtos.AnomalyInferenceRequestDto;
import com.project.backend.dtos.OptimizationRequestDto;
import com.project.backend.dtos.OptimizationResponseDto;
import com.project.backend.dtos.SlaInferenceRequestDto;
import com.project.backend.services.interfaces.AiInferenceService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class AiInferenceServiceImpl implements AiInferenceService {

    private final RestClient.Builder restClientBuilder;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${app.ai.anomaly-base-url:http://localhost:8003}")
    private String anomalyBaseUrl;

    @Value("${app.ai.sla-base-url:http://localhost:8004}")
    private String slaBaseUrl;

    @Value("${app.ai.agent-base-url:http://localhost:8002}")
    private String agentBaseUrl;

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

    @Override
    public OptimizationResponseDto runMockOptimization() {
        List<Map<String, Object>> rows = buildMockTelemetryRows();

        // Step 1: anomaly detection — strip string fields, service only accepts numeric values
        AnomalyInferenceRequestDto anomalyReq = AnomalyInferenceRequestDto.builder()
                .rows(buildNumericOnlyRows(rows))
                .stride(1)
                .thresholdName("best")
                .build();
        JsonNode anomalyResult = callWithMockFallback(
                () -> predictAnomaly(anomalyReq),
                buildMockAnomalyResult()
        );

        // Step 2: SLA forecasting
        SlaInferenceRequestDto slaReq = SlaInferenceRequestDto.builder()
                .runId("mock-run-001")
                .segment("segment-A")
                .rows(rows)
                .useAllWindows(false)
                .stride(1)
                .slaAlertThreshold(0.7)
                .build();
        JsonNode slaResult = callWithMockFallback(
                () -> predictSla(slaReq),
                buildMockSlaResult()
        );

        // Step 3: compute averages over last 30 seconds
        Map<String, Double> avg30s = computeAverages(rows);

        // Step 4: call agent optimization endpoint
        OptimizationRequestDto agentReq = OptimizationRequestDto.builder()
                .anomalyResult(anomalyResult)
                .slaResult(slaResult)
                .avg30s(avg30s)
                .device("switch-core-01")
                .context("mock optimization pipeline")
                .build();
        JsonNode agentResult = post(agentBaseUrl + "/optimization/respond", agentReq);

        Map<String, Object> telemetrySummary = new HashMap<>();
        telemetrySummary.put("row_count", rows.size());
        telemetrySummary.put("window_seconds", 30);
        telemetrySummary.put("avg_metrics", avg30s);

        return OptimizationResponseDto.builder()
                .telemetrySummary(telemetrySummary)
                .anomalyResponse(anomalyResult)
                .slaResponse(slaResult)
                .optimizationDecision(agentResult != null ? agentResult.get("decision") : null)
                .toolTrace(agentResult != null ? agentResult.get("tool_trace") : null)
                .mockMode(true)
                .build();
    }

    private List<Map<String, Object>> buildMockTelemetryRows() {
        long nowEpoch = Instant.now().getEpochSecond();
        List<Map<String, Object>> rows = new ArrayList<>();

        // 6 deterministic rows covering the last 30 seconds (5-second intervals)
        double[][] values = {
            // mos_voice, e2e_delay_ms, plr, jitter_ms, call_setup_time_ms,
            // buffering_ratio, rebuffering_freq, rebuffering_count, total_stall_seconds,
            // video_start_time_ms, streaming_mos, effective_bitrate_mbps, throughput_mbps,
            // dns_latency_ms, availability, rx_bytes, tx_bytes, rx_packets, tx_packets,
            // rx_dropped, tx_dropped, dataplane_latency_ms, ctrl_plane_rtt_ms, flow_count
            {3.8, 45.0,  0.01, 5.0,  200.0, 0.02, 0.1, 0.0, 0.0,  500.0,  3.9, 10.0, 9.5,  12.0, 99.9, 1200000, 800000, 9000, 7000, 5,  3,  2.0,  5.0,  120},
            {3.7, 52.0,  0.02, 7.0,  210.0, 0.03, 0.2, 1.0, 0.1,  520.0,  3.8, 9.8,  9.2,  13.0, 99.8, 1250000, 820000, 9100, 7100, 8,  5,  2.5,  5.5,  122},
            {3.5, 68.0,  0.04, 12.0, 225.0, 0.07, 0.5, 2.0, 0.4,  560.0,  3.6, 9.2,  8.8,  16.0, 99.5, 1300000, 850000, 9200, 7200, 15, 10, 4.0,  7.0,  130},
            {3.3, 85.0,  0.06, 18.0, 245.0, 0.12, 0.9, 3.0, 0.8,  600.0,  3.4, 8.5,  8.1,  20.0, 99.1, 1350000, 880000, 9300, 7300, 22, 15, 6.5,  9.5,  138},
            {3.1, 105.0, 0.09, 25.0, 270.0, 0.18, 1.3, 4.0, 1.2,  650.0,  3.2, 7.8,  7.3,  25.0, 98.7, 1400000, 910000, 9400, 7400, 30, 20, 9.0,  12.0, 145},
            {2.9, 130.0, 0.12, 35.0, 300.0, 0.25, 1.8, 6.0, 1.8,  720.0,  3.0, 7.0,  6.5,  32.0, 98.2, 1450000, 940000, 9500, 7500, 40, 28, 12.0, 16.0, 153},
        };

        String[] switchIds = {"sw-01", "sw-01", "sw-02", "sw-02", "sw-03", "sw-03"};

        for (int i = 0; i < values.length; i++) {
            double[] v = values[i];
            Map<String, Object> row = new HashMap<>();
            row.put("run_id", "mock-run-001");
            row.put("timestamp", nowEpoch - (30 - i * 5));
            row.put("datetime", Instant.ofEpochSecond(nowEpoch - (30 - i * 5)).toString());
            row.put("segment", "segment-A");
            row.put("switch_id", switchIds[i]);
            row.put("port_no", i + 1);
            row.put("mos_voice", v[0]);
            row.put("e2e_delay_ms", v[1]);
            row.put("plr", v[2]);
            row.put("jitter_ms", v[3]);
            row.put("cdr_flag", 0);
            row.put("call_setup_time_ms", v[4]);
            row.put("buffering_ratio", v[5]);
            row.put("rebuffering_freq", v[6]);
            row.put("rebuffering_count", (int) v[7]);
            row.put("total_stall_seconds", v[8]);
            row.put("video_start_time_ms", v[9]);
            row.put("streaming_mos", v[10]);
            row.put("effective_bitrate_mbps", v[11]);
            row.put("throughput_mbps", v[12]);
            row.put("dns_latency_ms", v[13]);
            row.put("availability", v[14]);
            row.put("rx_bytes", (long) v[15]);
            row.put("tx_bytes", (long) v[16]);
            row.put("rx_packets", (long) v[17]);
            row.put("tx_packets", (long) v[18]);
            row.put("rx_dropped", (int) v[19]);
            row.put("tx_dropped", (int) v[20]);
            row.put("dataplane_latency_ms", v[21]);
            row.put("ctrl_plane_rtt_ms", v[22]);
            row.put("flow_count", (int) v[23]);
            row.put("mos_source", "mock");
            row.put("label", 0);
            rows.add(row);
        }
        return rows;
    }

    @FunctionalInterface
    private interface ServiceCall {
        JsonNode call();
    }

    private JsonNode callWithMockFallback(ServiceCall call, Map<String, Object> fallback) {
        try {
            return call.call();
        } catch (Exception ex) {
            System.out.println("[MOCK_FALLBACK] Service unavailable, using mock result: " + ex.getMessage());
            return objectMapper.valueToTree(fallback);
        }
    }

    private Map<String, Object> buildMockAnomalyResult() {
        Map<String, Object> result = new HashMap<>();
        result.put("mock", true);
        result.put("status", "fallback");
        result.put("anomaly_detected", true);
        result.put("anomaly_score", 0.78);
        result.put("threshold", "best");
        result.put("label", 1);
        result.put("message", "Mock anomaly result — model artifacts not loaded");
        return result;
    }

    private Map<String, Object> buildMockSlaResult() {
        Map<String, Object> result = new HashMap<>();
        result.put("mock", true);
        result.put("status", "fallback");
        result.put("sla_violation_probability", 0.82);
        result.put("sla_alert", true);
        result.put("risk_level", "high");
        result.put("message", "Mock SLA result — model artifacts not loaded");
        return result;
    }

    private List<Map<String, Object>> buildNumericOnlyRows(List<Map<String, Object>> rows) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> numericRow = new HashMap<>();
            for (Map.Entry<String, Object> entry : row.entrySet()) {
                if (entry.getValue() instanceof Number) {
                    numericRow.put(entry.getKey(), entry.getValue());
                }
            }
            result.add(numericRow);
        }
        return result;
    }

    private Map<String, Double> computeAverages(List<Map<String, Object>> rows) {
        String[] numericFields = {
            "mos_voice", "e2e_delay_ms", "plr", "jitter_ms", "call_setup_time_ms",
            "buffering_ratio", "rebuffering_freq", "rebuffering_count", "total_stall_seconds",
            "video_start_time_ms", "streaming_mos", "effective_bitrate_mbps", "throughput_mbps",
            "dns_latency_ms", "availability", "rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
            "rx_dropped", "tx_dropped", "dataplane_latency_ms", "ctrl_plane_rtt_ms", "flow_count"
        };

        Map<String, Double> sums = new HashMap<>();
        for (String field : numericFields) {
            sums.put(field, 0.0);
        }

        for (Map<String, Object> row : rows) {
            for (String field : numericFields) {
                Object val = row.get(field);
                if (val instanceof Number) {
                    sums.merge(field, ((Number) val).doubleValue(), Double::sum);
                }
            }
        }

        Map<String, Double> avgs = new HashMap<>();
        int n = rows.size();
        for (String field : numericFields) {
            avgs.put(field, n > 0 ? sums.get(field) / n : 0.0);
        }
        return avgs;
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
