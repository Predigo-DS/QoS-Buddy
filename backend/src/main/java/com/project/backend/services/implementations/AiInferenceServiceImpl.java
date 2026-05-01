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
    private final TelemetryBufferService telemetryBufferService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${app.ai.anomaly-base-url:http://localhost:8003}")
    private String anomalyBaseUrl;

    @Value("${app.ai.sla-base-url:http://localhost:8004}")
    private String slaBaseUrl;

    @Value("${app.ai.agent-base-url:http://localhost:8002}")
    private String agentBaseUrl;

    @Value("${app.ai.allow-mock:true}")
    private boolean allowMockFallback;

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
        boolean liveMode = telemetryBufferService.hasEnough(35);
        List<Map<String, Object>> rows = liveMode
                ? telemetryBufferService.getLatest(35)
                : buildMockTelemetryRows();

        // Step 1: anomaly detection — strip string fields, service only accepts numeric values
        AnomalyInferenceRequestDto anomalyReq = AnomalyInferenceRequestDto.builder()
                .rows(buildNumericOnlyRows(rows))
                .stride(1)
                .thresholdName("best")
                .build();
        JsonNode anomalyResult = callWithMockFallback(
                () -> predictAnomaly(anomalyReq),
                buildMockAnomalyResult());

        // Step 2: SLA forecasting — resolve real run_id/segment from metadata
        String runId = "run_20260409_120415";
        String segment = "IMS_CDN";
        try {
            JsonNode slaMeta = getSlaMetadata();
            if (slaMeta.has("run_segment_keys") && slaMeta.get("run_segment_keys").isArray()
                    && slaMeta.get("run_segment_keys").size() > 0) {
                JsonNode first = slaMeta.get("run_segment_keys").get(0);
                if (first.has("run_id"))  runId  = first.get("run_id").asText();
                if (first.has("segment")) segment = first.get("segment").asText();
            }
        } catch (Exception ignored) {}

        SlaInferenceRequestDto slaReq = SlaInferenceRequestDto.builder()
                .runId(runId)
                .segment(segment)
                .rows(rows)
                .useAllWindows(false)
                .stride(1)
                .slaAlertThreshold(0.7)
                .build();
        JsonNode slaResult = callWithMockFallback(
                () -> predictSla(slaReq),
                buildMockSlaResult());

        // Step 3: compute averages over last 30 seconds
        Map<String, Double> avg30s = computeAverages(rows);

        // Step 4: build context string from real metrics so the LLM understands the severity
        double avgPlr    = avg30s.getOrDefault("plr", 0.0);
        double avgDelay  = avg30s.getOrDefault("e2e_delay_ms", 0.0);
        double avgMos    = avg30s.getOrDefault("mos_voice", 4.0);
        boolean anomalyDetected = anomalyResult != null
                && (anomalyResult.path("anomaly_detected").asBoolean(false)
                    || anomalyResult.path("anomaly_windows").asInt(0) > 0);
        boolean slaAlerted = slaResult != null
                && (slaResult.path("sla_alert").asBoolean(false)
                    || slaResult.path("alert_count").asInt(0) > 0);

        String severity = anomalyDetected && slaAlerted ? "CRITICAL"
                        : anomalyDetected || slaAlerted ? "HIGH"
                        : avgPlr > 0.05 || avgDelay > 100 ? "MEDIUM" : "LOW";

        String context = String.format(
                "Live network data — %d rows from Mininet. Severity: %s. " +
                "avg_plr=%.4f avg_e2e_delay=%.1fms avg_mos=%.2f. " +
                "anomaly_detected=%b sla_alert=%b. " +
                "Take concrete remediation action now.",
                rows.size(), severity, avgPlr, avgDelay, avgMos, anomalyDetected, slaAlerted);

        // Step 5: call agent optimization endpoint
        OptimizationRequestDto agentReq = OptimizationRequestDto.builder()
                .anomalyResult(anomalyResult)
                .slaResult(slaResult)
                .avg30s(avg30s)
                .device("switch-core-01")
                .context(context)
                .build();
        JsonNode agentResult = callWithMockFallback(
                () -> post(agentBaseUrl + "/optimization/respond", agentReq),
                buildMockAgentResult());

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
                .mockMode(!liveMode)
                .build();
    }

    private List<Map<String, Object>> buildMockTelemetryRows() {
        long nowEpoch = Instant.now().getEpochSecond();
        List<Map<String, Object>> rows = new ArrayList<>();

        // 35 rows (window_size=35) — gradual degradation pattern, 5-second intervals
        int N = 35;
        for (int i = 0; i < N; i++) {
            double t = (double) i / (N - 1); // 0.0 → 1.0 degradation factor
            Map<String, Object> row = new HashMap<>();
            row.put("run_id", "run_20260409_120415");
            row.put("timestamp", nowEpoch - ((N - 1 - i) * 5L));
            row.put("datetime", Instant.ofEpochSecond(nowEpoch - ((N - 1 - i) * 5L)).toString());
            row.put("segment", "IMS_CDN");
            row.put("switch_id", "sw-0" + (i % 3 + 1));
            row.put("port_no", i % 6 + 1);
            row.put("mos_voice", lerp(3.9, 2.5, t));
            row.put("e2e_delay_ms", lerp(40.0, 160.0, t));
            row.put("plr", lerp(0.005, 0.15, t));
            row.put("jitter_ms", lerp(3.0, 40.0, t));
            row.put("cdr_flag", t > 0.8 ? 1 : 0);
            row.put("call_setup_time_ms", lerp(180.0, 320.0, t));
            row.put("buffering_ratio", lerp(0.01, 0.30, t));
            row.put("rebuffering_freq", lerp(0.0, 2.0, t));
            row.put("rebuffering_count", (int) lerp(0, 7, t));
            row.put("total_stall_seconds", lerp(0.0, 2.5, t));
            row.put("video_start_time_ms", lerp(480.0, 800.0, t));
            row.put("streaming_mos", lerp(4.0, 2.8, t));
            row.put("effective_bitrate_mbps", lerp(12.0, 5.5, t));
            row.put("throughput_mbps", lerp(11.0, 5.0, t));
            row.put("dns_latency_ms", lerp(10.0, 38.0, t));
            row.put("availability", lerp(99.99, 97.5, t));
            row.put("rx_bytes", (long) lerp(1100000, 1600000, t));
            row.put("tx_bytes", (long) lerp(750000, 1000000, t));
            row.put("rx_packets", (long) lerp(8500, 10500, t));
            row.put("tx_packets", (long) lerp(6500, 8500, t));
            row.put("rx_dropped", (int) lerp(2, 55, t));
            row.put("tx_dropped", (int) lerp(1, 35, t));
            row.put("dataplane_latency_ms", lerp(1.5, 18.0, t));
            row.put("ctrl_plane_rtt_ms", lerp(3.0, 22.0, t));
            row.put("flow_count", (int) lerp(100, 180, t));
            row.put("mos_source", "mock");
            row.put("label", t > 0.7 ? 1 : 0);
            rows.add(row);
        }
        return rows;
    }

    private double lerp(double a, double b, double t) {
        return a + (b - a) * t;
    }

    @FunctionalInterface
    private interface ServiceCall {
        JsonNode call();
    }

    private JsonNode callWithMockFallback(ServiceCall call, Map<String, Object> fallback) {
        try {
            return call.call();
        } catch (Exception ex) {
            if (!allowMockFallback) {
                if (ex instanceof ResponseStatusException rse) {
                    throw rse;
                }
                throw new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "AI service unavailable and mock fallback disabled: " + ex.getMessage(),
                        ex
                );
            }
            System.out.println("[MOCK_FALLBACK] Service unavailable, using mock result: " + ex.getMessage());
            return objectMapper.valueToTree(fallback);
        }
    }

    private Map<String, Object> buildMockAnomalyResult() {
        Map<String, Object> result = new HashMap<>();
        result.put("anomaly_detected", true);
        result.put("anomaly_score", 0.78);
        result.put("threshold_name", "best");
        result.put("threshold_value", 0.45);
        result.put("anomaly_windows", 4);
        result.put("total_windows", 5);
        result.put("model_type", "autoencoder");
        result.put("window_size", 7);
        return result;
    }

    private Map<String, Object> buildMockSlaResult() {
        Map<String, Object> result = new HashMap<>();
        result.put("sla_violation_probability", 0.82);
        result.put("sla_alert", true);
        result.put("alert_rate", 0.80);
        result.put("alert_count", 4);
        result.put("run_id", "run_20260409_120415");
        result.put("segment", "IMS_CDN");
        result.put("window_size", 7);
        return result;
    }

    private Map<String, Object> buildMockAgentResult() {
        Map<String, Object> result = new HashMap<>();
        result.put("mock", true);
        result.put("status", "fallback");

        Map<String, Object> decision = new HashMap<>();
        decision.put("decision_summary", "Mock agent decision — LLM provider unavailable or invalid API key");
        decision.put("recommended_actions", List.of());
        decision.put("confidence", 0.0);
        decision.put("risk_level", "medium");
        result.put("decision", decision);

        result.put("tool_trace", List.of());
        result.put("message", "Mock agent result — check GROQ_API_KEY / provider config");
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
