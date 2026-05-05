#!/usr/bin/env python3
"""
listen_telecom.py — Telecom QoE CSV Writer (v2 — fixed)
=========================================================
FIXES vs v1:
1. Data-plane latency merging
   Handles new 'dataplane_latency' events published by traffic_gen_v2.py.
   These contain real ping-measured one-way delay from UE → server through
   TC-impaired links. Used to populate 'dataplane_latency_ms' column and to
   recompute MOS with accurate latency (see fix #2 below).

2. MOS recomputation with measured latency
   The Ryu controller's OpenFlow echo measures CONTROL-PLANE RTT (controller
   ↔ switch, does NOT traverse TC-impaired links). This means the original MOS
   was always computed with near-zero latency regardless of scenario.
   FIX: when a fresh ping-measured latency is available for the segment, MOS is
   recomputed here using the real data-plane latency.
   'mos_source' column: 'ping_measured' | 'ctrl_plane_fallback'
   NOTE: MOS is RETAINED in the dataset as required by the project (it is needed
   for the RAG/reporting layer and executive dashboard). For ML training, use
   PLR/e2e_delay_ms/jitter_ms as primary input features and be aware that
   mos_voice is derived from these — colinear by construction. Use
   feature_importances_ or correlation analysis to decide which to keep.

3. AStream staleness check
   Streaming measured values now expire after STREAMING_MAX_AGE_S (30 s).
   Previously, stale AStream data from a previous scenario could contaminate
   rows of a new scenario for several minutes.

4. Transition skip counter
   The first TRANSITION_SKIP_ROWS rows after a label change are discarded.
   This removes the "boundary noise" where TC impairments haven't yet
   accumulated enough packets for accurate loss measurement.

5. Periodic CSV flush fallback
   A background thread flushes pending port rows every PERIODIC_FLUSH_S (10 s)
   even if no 'loss' event arrives. Prevents stalled rows when TC sampling has
   a gap.

6. New CSV columns
   'dataplane_latency_ms' : ping-measured one-way delay (new)
   'ctrl_plane_rtt_ms'    : OpenFlow echo RTT (renamed from e2e_delay_ms in old
                            code — kept for controller health monitoring)
   'flow_count'           : active OF flows on segment's switch (new)
   'mos_source'           : 'ping_measured' | 'ctrl_plane_fallback' (new)

New CSV schema (network_qoe.csv):
All original columns are preserved + 4 new columns at the end.

TOPOLOGY CHANGES (v2.1):
Topology: s1 (core) + s2-s5 (leaf switches).
- LOSS_PORT_MAP and SEGMENT_NAMES expanded to include leaf uplinks (port 1 of
  each leaf = uplink to s1, shares the same TC rule).
- Segment lookup in _flush_all_unsafe now uses (s_id, p_no) directly.
- Flush trigger fires as soon as all 4 s1 ports are present (no longer waits
  for 8 ports).

SDN ACTION EXECUTOR (v2.2):
Subscribes to Docker Redis 'sdn_actions' channel (localhost:6380).
Executes real OVS/TC commands when the AI agent publishes actions.
Supported action types:
  - reroute_traffic    → ovs-ofctl mod-flows
  - throttle_link      → tc qdisc replace (TBF)
  - apply_qos_profile  → ovs-ofctl add-flow (DSCP-based)
  - monitor_only       → no-op
"""

import redis
import json
import csv
import time
import math
import os
import re
import signal
import sys
import threading
import subprocess
from datetime import datetime
from collections import defaultdict
import urllib.request
import urllib.error
import json as _json_mod

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STREAMING_MAX_AGE_S = 30   # AStream values older than this → fall back to computed
TRANSITION_SKIP_ROWS = int(os.getenv('QOE_TRANSITION_SKIP_ROWS', '3'))   # Rows to discard after a label transition
TRANSITION_SKIP_S    = int(os.getenv('QOE_TRANSITION_SKIP_S', '8'))       # Covers the 5s cooldown + 3s margin
PERIODIC_FLUSH_S     = int(os.getenv('QOE_PERIODIC_FLUSH_S', '2'))        # Fallback flush interval in seconds
DATAPLANE_MAX_AGE_S  = 10  # Ping latency older than this → fall back to ctrl-plane

# QoSentry backend integration
QOSENTRY_BACKEND_URL  = os.getenv('QOSENTRY_BACKEND_URL', 'http://172.28.96.1:8081')
QOSENTRY_INGEST_PATH  = '/api/telemetry/ingest'
QOSENTRY_ENABLED      = os.getenv('QOSENTRY_ENABLED', 'true').lower() == 'true'

# SDN Action executor — Docker Redis exposed on host port 6380
ACTION_REDIS_HOST    = os.getenv('ACTION_REDIS_HOST', 'localhost')
ACTION_REDIS_PORT    = int(os.getenv('ACTION_REDIS_PORT', '6380'))
ACTION_REDIS_CHANNEL = 'sdn_actions'
MININET_SSH          = os.getenv('MININET_SSH', 'mininet@192.168.249.132')

# ---------------------------------------------------------------------------
# Segment / port maps — expanded for leaf switches (s2-s5)
# ---------------------------------------------------------------------------
LOSS_PORT_MAP = {
    (1, 1): (1, 1),
    (1, 2): (1, 2),
    (1, 3): (1, 3),
    (1, 4): (1, 4),
    # Leaf uplinks share the same TC rule as the corresponding s1 port
    (2, 1): (1, 1),
    (3, 1): (1, 2),
    (4, 1): (1, 3),
    (5, 1): (1, 4),
}

SWITCH_TO_LOSS_KEY = {
    2: (1, 1),
    3: (1, 2),
    4: (1, 3),
    5: (1, 4),
}

SEGMENT_NAMES = {
    # Core switch s1
    (1, 1): 'OUTDOOR_RAN',
    (1, 2): 'INDOOR_RAN',
    (1, 3): 'IMS_CDN',
    (1, 4): 'INTERNET',
    # Leaf switches (port 1 = uplink to s1)
    (2, 1): 'OUTDOOR_RAN',
    (3, 1): 'INDOOR_RAN',
    (4, 1): 'IMS_CDN',
    (5, 1): 'INTERNET',
}

# Only these ports represent segment-level links and should be exported to CSV.
SEGMENT_PORT_KEYS = set(SEGMENT_NAMES.keys())

# Reverse map: segment name → (tx_dpid, tx_port)
SEGMENT_TO_PORT = {v: k for k, v in SEGMENT_NAMES.items()}

# Segment → OVS switch + interface (for SDN action executor)
SEGMENT_TO_OVS_PORT = {
    'OUTDOOR_RAN': ('s1', 'eth1'),
    'INDOOR_RAN':  ('s1', 'eth2'),
    'IMS_CDN':     ('s1', 'eth3'),
    'INTERNET':    ('s1', 'eth4'),
}

# ---------------------------------------------------------------------------
# E-model MOS (ITU-T G.107) — copied here so listener can recompute
# independently of monitor_telecom.py.
# ---------------------------------------------------------------------------
def compute_voice_mos(delay_ms: float, plr: float, jitter_ms: float) -> float:
    """
    Re-implementation of ITU-T G.107 E-model for G.711 codec.
    Used to recompute MOS with ping-measured latency when available.
    """
    eff_delay = delay_ms + jitter_ms * 2.0 + 10.0
    Id = 0.024 * eff_delay + (0.11 * (eff_delay - 177.3) if eff_delay > 177.3 else 0.0)
    plr_pct = plr * 100.0
    Ie_eff = (95.0 * plr_pct / (plr_pct + 10.0)) if plr_pct > 0 else 0.0
    R = max(0.0, min(100.0, 93.2 - Id - Ie_eff))
    mos = 1.0 + 0.035 * R + 7e-6 * R * (R - 60.0) * (100.0 - R)
    return round(max(1.0, min(4.4, mos)), 3)


def compute_call_setup_time_ms(latency_ms: float, plr: float) -> float:
    """Same setup-time model used by monitor, recomputed with best latency source."""
    base_setup = 5.0 * latency_ms * 2 + 200.0
    expected_retransmits = plr / (1.0 - plr) if plr < 1.0 else 5.0
    setup_penalty = expected_retransmits * 500.0
    return round(min(base_setup + setup_penalty, 10000.0), 1)


# ===========================================================================
# Listener
# ===========================================================================
class TelecomQoEListener:
    def __init__(self, qoe_filename='network_qoe.csv', raw_filename='network_raw.csv'):
        self.qoe_filename = qoe_filename
        self.raw_filename = raw_filename
        self.current_label = 'NORMAL'
        self.run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # ---- State buffers -----------------------------------------------
        self.link_loss    = {}   # (tx_dpid, tx_port) → float
        self.qoe_buffer   = {}   # (tx_dpid, tx_port) → latest qoe dict
        self.pending_port = {}   # (s_id, p_no) → raw port row dict

        # Streaming (AStream-measured) — now with staleness check
        self.streaming_measured_buffer = {}  # segment_name → dict with 'timestamp'

        # NEW: ping-measured data-plane latency — expires after DATAPLANE_MAX_AGE_S
        # Store both source timestamp and listener receive timestamp.
        self.dataplane_latency_buffer = {}  # segment_name → {latency_ms, jitter_ms, source_timestamp, received_timestamp}

        # NEW: flow count per switch dpid
        self.flow_count_buffer = {}  # dpid → int

        # Counter normalization state (per exported segment port).
        # Linux/OVS counters can reset when links/qdiscs are reconfigured; this
        # keeps exported counters monotonic for downstream feature engineering.
        self.counter_state = {}

        # FIX 4: transition skip counter
        self._transition_skip = 0
        self._transition_quarantine_until = 0.0
        self._qosentry_batch = []

        # ---- Redis & CSV setup -------------------------------------------
        self.redis = redis.Redis(
            host='127.0.0.1', port=6379, db=0, decode_responses=True)
        self._setup_csv()
        signal.signal(signal.SIGINT, self._on_exit)

        # FIX 5: start background periodic flush thread
        self._flush_lock = threading.Lock()
        self._start_periodic_flush()

        # SDN Action Executor: subscribe to Docker Redis sdn_actions channel
        self._start_action_subscriber()

    # -----------------------------------------------------------------------
    # CSV setup
    # -----------------------------------------------------------------------
    def _setup_csv(self):
        self.qoe_file = open(self.qoe_filename, mode='w', newline='')
        self.qoe_fields = [
            # Metadata
            'run_id', 'timestamp', 'datetime', 'segment', 'switch_id', 'port_no',
            # Voice KPIs
            'mos_voice', 'e2e_delay_ms', 'plr', 'jitter_ms', 'cdr_flag',
            'call_setup_time_ms',
            # Streaming KPIs
            'buffering_ratio', 'rebuffering_freq', 'rebuffering_count',
            'total_stall_seconds', 'video_start_time_ms', 'streaming_mos',
            'effective_bitrate_mbps',
            # Data KPIs
            'throughput_mbps', 'dns_latency_ms', 'availability',
            # Raw counters
            'rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets',
            'rx_dropped', 'tx_dropped',
            # NEW columns (v2 fixes)
            'dataplane_latency_ms',   # real ping-measured one-way delay
            'ctrl_plane_rtt_ms',      # OpenFlow echo RTT (controller health only)
            'flow_count',             # active OF flows (key for CAPACITY_EXHAUSTED)
            'mos_source',             # 'ping_measured' | 'ctrl_plane_fallback'
            # Label
            'label',
        ]
        self.qoe_writer = csv.DictWriter(self.qoe_file, fieldnames=self.qoe_fields)
        self.qoe_writer.writeheader()

        self.raw_file = open(self.raw_filename, mode='w', newline='')
        self.raw_fields = [
            'run_id', 'timestamp', 'datetime', 'switch_id', 'port_no',
            'throughput_mbps', 'packet_loss_rate', 'latency_ms', 'jitter_ms',
            'rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets',
            'rx_dropped', 'tx_dropped', 'label'
        ]
        self.raw_writer = csv.DictWriter(self.raw_file, fieldnames=self.raw_fields)
        self.raw_writer.writeheader()

        print(f'[CSV] QoE → {self.qoe_filename} (Run ID: {self.run_id})')
        print(f'[CSV] Raw → {self.raw_filename}')

    def _on_exit(self, sig, frame):
        self._flush_all()
        self.qoe_file.close()
        self.raw_file.close()
        print('\n[OK] CSVs saved. Exiting.')
        sys.exit(0)

    # -----------------------------------------------------------------------
    # FIX 5: Periodic flush background thread
    # -----------------------------------------------------------------------
    def _push_to_qosentry(self, rows: list):
        """Forward QoE rows to QoSentry backend telemetry buffer (best-effort)."""
        if not QOSENTRY_ENABLED or not rows:
            return
        try:
            payload = _json_mod.dumps(rows).encode('utf-8')
            url = QOSENTRY_BACKEND_URL.rstrip('/') + QOSENTRY_INGEST_PATH
            req = urllib.request.Request(
                url,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = resp.getcode()
                if status == 200:
                    print(f' [QoSentry] Pushed {len(rows)} rows → {status}')
        except urllib.error.URLError as e:
            print(f' [QoSentry] Push failed (backend unreachable): {e.reason}')
        except Exception as e:
            print(f' [QoSentry] Push error: {e}')

    def _start_periodic_flush(self):
        def _loop():
            while True:
                time.sleep(PERIODIC_FLUSH_S)
                with self._flush_lock:
                    if self.pending_port:
                        self._flush_all_unsafe()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    # -----------------------------------------------------------------------
    # SDN Action Executor (v2.2)
    # -----------------------------------------------------------------------
    def _start_action_subscriber(self):
        """Subscribe to Docker Redis sdn_actions channel and execute real OVS/TC commands."""

        def _parse_iface(text: str) -> str:
            """Extract ethX from strings like 'backup-path-via-eth2'."""
            m = re.search(r'eth\d+', text or '')
            return m.group(0) if m else 'eth1'

        def _run(cmd: str) -> bool:
            remote_cmd = f"ssh {MININET_SSH} 'sudo {cmd}'"
            print(f' [OVS/TC] {remote_cmd}')
            result = subprocess.run(remote_cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                print(f' [OVS/TC] ERROR: {result.stderr.strip()}')
            return result.returncode == 0

        def _execute(action: dict):
            atype  = action.get('action')
            device = 's1'   # switch-core-01 → s1 in Mininet

            if atype == 'reroute_traffic':
                iface   = _parse_iface(action.get('path', ''))
                port_no = int(iface.replace('eth', '')) if iface else 1
                # Increase output queue priority on the target port
                _run(f'ovs-ofctl mod-flows {device} priority=200,in_port={port_no},actions=output:NORMAL')
                print(f' [ACTION] Rerouted traffic on {device}-{iface}')

            elif atype == 'throttle_link':
                iface = action.get('interface', 'eth1')
                rate  = float(action.get('rate_limit_mbps', 5.0))
                _run(f'tc qdisc replace dev {device}-{iface} root tbf rate {rate}mbit burst 32kbit latency 400ms')
                print(f' [ACTION] Throttled {device}-{iface} to {rate}Mbps')

            elif atype == 'apply_qos_profile':
                profile = action.get('profile', '')
                # Map segment/profile to s1 port number
                segment_port = {'OUTDOOR_RAN': 1, 'INDOOR_RAN': 2, 'IMS_CDN': 3, 'INTERNET': 4}
                # Try to find port from device or default to all ports via NORMAL action
                port_no = 1  # default
                if 'voice' in profile or 'video' in profile:
                    # Apply high-priority rule to all ports
                    _run(f'ovs-ofctl add-flow {device} priority=150,ip,nw_tos=184,actions=NORMAL')
                    _run(f'ovs-ofctl add-flow {device} priority=140,ip,nw_tos=136,actions=NORMAL')
                else:
                    _run(f'ovs-ofctl add-flow {device} priority=120,ip,actions=NORMAL')
                print(f" [ACTION] Applied QoS profile '{profile}' on {device}")

            elif atype == 'monitor_only':
                print(' [ACTION] Monitor only — no OVS change applied')

            else:
                print(f' [ACTION] Unknown action type: {atype!r} — ignored')

        def _loop():
            try:
                action_redis = redis.Redis(
                    host=ACTION_REDIS_HOST,
                    port=ACTION_REDIS_PORT,
                    db=0,
                    decode_responses=True,
                )
                pubsub = action_redis.pubsub()
                pubsub.subscribe(ACTION_REDIS_CHANNEL)
                print(
                    f'[OK] Subscribed to {ACTION_REDIS_CHANNEL} '
                    f'(Docker Redis {ACTION_REDIS_HOST}:{ACTION_REDIS_PORT})'
                )
                for msg in pubsub.listen():
                    if msg['type'] == 'message':
                        try:
                            action = json.loads(msg['data'])
                            print(
                                f"\n[SDN_ACTION] Received: {action.get('action')} "
                                f"on {action.get('device')}"
                            )
                            _execute(action)
                        except Exception as e:
                            print(f' [SDN_ACTION] Error executing action: {e}')
            except Exception as e:
                print(
                    f'[SDN_ACTION] Cannot connect to Docker Redis '
                    f'{ACTION_REDIS_HOST}:{ACTION_REDIS_PORT} — {e}'
                )

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _get_loss(self, s_id, p_no):
        key = (s_id, p_no)
        if key in LOSS_PORT_MAP:
            return self.link_loss.get(LOSS_PORT_MAP[key], 0.0)
        if s_id in SWITCH_TO_LOSS_KEY:
            return self.link_loss.get(SWITCH_TO_LOSS_KEY[s_id], 0.0)
        return 0.0

    def _get_streaming_measured(self, segment: str) -> dict:
        """
        FIX 3: Return AStream measured values only if fresh.
        Values older than STREAMING_MAX_AGE_S are considered stale.
        """
        measured = self.streaming_measured_buffer.get(segment, {})
        if not measured:
            return {}
        age = time.time() - measured.get('timestamp', 0)
        if age > STREAMING_MAX_AGE_S:
            return {}  # stale — fall back to computed
        return measured

    def _get_dataplane_latency(self, segment: str):
        """
        FIX 1: Return ping-measured latency if fresh, else None.
        Returns (latency_ms, jitter_ms, source_tag) or (None, None, 'ctrl_plane_fallback').
        """
        entry = self.dataplane_latency_buffer.get(segment)
        if entry and (time.time() - entry.get('received_timestamp', 0.0)) < DATAPLANE_MAX_AGE_S:
            return entry['latency_ms'], entry['jitter_ms'], 'ping_measured'
        return None, None, 'ctrl_plane_fallback'

    def _get_flow_count(self, s_id: int, p_no: int) -> int:
        """Return flow count for the segment's downstream switch."""
        # Downstream switch dpid = port number (s1-eth1 → s2 = dpid 2, etc.)
        downstream_dpid = p_no + 1 if s_id == 1 else s_id
        return self.flow_count_buffer.get(downstream_dpid, 0)

    def _is_segment_port(self, s_id: int, p_no: int) -> bool:
        """Keep only segment-defining links to avoid duplicate/unknown rows."""
        return (s_id, p_no) in SEGMENT_PORT_KEYS

    def _normalize_counter(self, key, field: str, value: float) -> float:
        """
        Convert raw counters into monotonic counters per port/field.
        If the observed value drops, treat it as a reset and continue from the
        prior logical total by increasing an internal offset.
        """
        st  = self.counter_state.setdefault(key, {})
        rec = st.setdefault(field, {'prev_raw': None, 'offset': 0.0})
        try:
            raw = float(value)
        except (TypeError, ValueError):
            return value
        prev_raw = rec['prev_raw']
        if prev_raw is not None and raw < prev_raw:
            rec['offset'] += prev_raw
        rec['prev_raw'] = raw
        logical = rec['offset'] + raw
        if isinstance(value, int):
            return int(logical)
        return logical

    # -----------------------------------------------------------------------
    # Flush logic
    # -----------------------------------------------------------------------
    def _flush_all(self):
        with self._flush_lock:
            self._flush_all_unsafe()

    def _flush_all_unsafe(self):
        """
        Inner flush (call with lock held or from within lock-protected context).
        FIX 4: rows are silently skipped during transition_skip countdown.
        """
        # During transition boundaries, suppress writes.
        # Decrement row-skip counter on each flush attempt so row/time gates
        # behave as max(row_gate, time_gate) rather than additive.
        if self._transition_skip > 0 or time.time() < self._transition_quarantine_until:
            if self._transition_skip > 0:
                self._transition_skip -= 1
            self.pending_port.clear()
            return

        for (s_id, p_no), port_row in list(self.pending_port.items()):
            port_row['packet_loss_rate'] = self._get_loss(s_id, p_no)

            # Backward-compatible raw row
            self.raw_writer.writerow(port_row)

            qoe_key  = (1, p_no) if s_id == 1 else (s_id, p_no)
            qoe_data = self.qoe_buffer.get(qoe_key, {})

            # FIX 2: direct lookup by (s_id, p_no) — correct for both s1 and leaf switches
            segment = SEGMENT_NAMES.get((s_id, p_no), 'UNKNOWN')

            # ---- Streaming: prefer AStream measured, expire by staleness window ---
            measured = self._get_streaming_measured(segment)

            def stream_val(key, fallback=0.0):
                if key in measured:
                    return measured[key]
                return qoe_data.get(key, fallback)

            # ---- FIX 1+2: Use ping latency for e2e_delay_ms and MOS ------
            dp_lat, dp_jit, mos_src = self._get_dataplane_latency(segment)
            ctrl_rtt = qoe_data.get(
                'ctrl_plane_rtt_ms',
                qoe_data.get('e2e_delay_ms', port_row['latency_ms'])
            )
            plr = port_row['packet_loss_rate']

            if dp_lat is not None:
                # Real data-plane latency is available — use it.
                best_latency = dp_lat
                best_jitter  = dp_jit if dp_jit is not None else port_row['jitter_ms']
            else:
                # Fall back to ctrl-plane RTT only for delay input.
                best_latency = ctrl_rtt
                best_jitter  = port_row['jitter_ms']

            # Always use the E-model formula for MOS to keep behavior consistent.
            mos_recomputed   = compute_voice_mos(best_latency, plr, best_jitter)
            cdr_flag_recomputed = 1 if (
                mos_recomputed < 2.0 or plr > 0.15 or best_latency > 400.0
            ) else 0
            setup_time_recomputed = compute_call_setup_time_ms(best_latency, plr)

            # Flow count for CAPACITY_EXHAUSTED discrimination
            flow_count = qoe_data.get('flow_count', self._get_flow_count(s_id, p_no))

            qoe_row = {
                # Metadata
                'run_id':    self.run_id,
                'timestamp': port_row['timestamp'],
                'datetime':  port_row['datetime'],
                'segment':   segment,
                'switch_id': s_id,
                'port_no':   p_no,
                # Voice KPIs — MOS uses best available latency
                'mos_voice':          mos_recomputed,
                'e2e_delay_ms':       round(best_latency, 3),
                'plr':                port_row['packet_loss_rate'],
                'jitter_ms':          round(best_jitter, 4),
                'cdr_flag':           cdr_flag_recomputed,
                'call_setup_time_ms': setup_time_recomputed,
                # Streaming KPIs (AStream preferred, expires by staleness window)
                'buffering_ratio':       stream_val('buffering_ratio'),
                'rebuffering_freq':      stream_val('rebuffering_freq'),
                'rebuffering_count':     stream_val('rebuffering_count', 0),
                'total_stall_seconds':   stream_val('total_stall_seconds', 0.0),
                'video_start_time_ms':   stream_val('video_start_time_ms'),
                'streaming_mos':         stream_val('streaming_mos'),
                'effective_bitrate_mbps': stream_val('effective_bitrate_mbps'),
                # Data KPIs
                'throughput_mbps': port_row['throughput_mbps'],
                'dns_latency_ms':  qoe_data.get('dns_latency_ms', 0.0),
                'availability':    qoe_data.get('availability', 1.0),
                # Raw counters
                'rx_bytes':   port_row['rx_bytes'],
                'tx_bytes':   port_row['tx_bytes'],
                'rx_packets': port_row['rx_packets'],
                'tx_packets': port_row['tx_packets'],
                'rx_dropped': port_row['rx_dropped'],
                'tx_dropped': port_row['tx_dropped'],
                # NEW v2 columns
                'dataplane_latency_ms': round(dp_lat, 3) if dp_lat is not None else '',
                'ctrl_plane_rtt_ms':    round(ctrl_rtt, 3),
                'flow_count':           flow_count,
                'mos_source':           mos_src,
                # Label
                'label': self.current_label,
            }
            self.qoe_writer.writerow(qoe_row)
            self._qosentry_batch.append(dict(qoe_row))

            # Console summary
            thr = port_row['throughput_mbps']
            loss = port_row['packet_loss_rate']
            mos  = mos_recomputed
            cdr  = cdr_flag_recomputed
            if thr > 0.01 or loss > 0.01 or mos > 0:
                cdr_tag    = 'DROP' if cdr else ' '
                stream_tag = 'STREAM' if measured else '~'
                lat_tag    = (
                    f'DPLat={dp_lat:.1f}ms' if dp_lat
                    else f'CtrlRTT={ctrl_rtt:.1f}ms'
                )
                print(
                    f" [{port_row['datetime']}] {segment:12s} | "
                    f"Thr={thr:.3f}Mbps PLR={loss:.3f} "
                    f"MOS={mos:.2f}({mos_src[:4]}) {cdr_tag} "
                    f"{lat_tag} "
                    f"Buf={stream_val('buffering_ratio'):.3f}{stream_tag} "
                    f"Flows={flow_count} | {self.current_label}"
                )

        self.pending_port.clear()
        self.qoe_file.flush()
        self.raw_file.flush()
        self._push_to_qosentry(self._qosentry_batch)
        self._qosentry_batch = []

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    def run(self):
        print('[OK] Listening on Redis sdn_telemetry...')
        print(f'[OK] AStream staleness threshold: {STREAMING_MAX_AGE_S}s')
        print(f'[OK] Ping latency staleness threshold: {DATAPLANE_MAX_AGE_S}s')
        print(f'[OK] Transition boundary skip: {TRANSITION_SKIP_ROWS} rows')
        print(f'[OK] Transition boundary quarantine: {TRANSITION_SKIP_S}s')

        pubsub = self.redis.pubsub()
        pubsub.subscribe('sdn_telemetry')

        for message in pubsub.listen():
            if message['type'] != 'message':
                continue
            try:
                data = json.loads(message['data'])
            except json.JSONDecodeError:
                continue

            mtype = data.get('type')

            # ---- Label transition ----------------------------------------
            if mtype == 'label':
                is_start = data['status'] == 'start'
                is_stop  = data['status'] == 'stop'

                if is_start:
                    # New scenario starting — update label immediately
                    new_label = data['event']
                    self._flush_all()
                    print(f"\n[LABEL] {self.current_label} → {new_label}")
                    # Arm skip to remove boundary noise rows
                    self._transition_skip = TRANSITION_SKIP_ROWS
                    self._transition_quarantine_until = time.time() + TRANSITION_SKIP_S
                    print(
                        f"[LABEL] Skipping next {TRANSITION_SKIP_ROWS} rows "
                        f"(boundary noise suppression)"
                    )
                    print(
                        f"[LABEL] Suppressing rows for {TRANSITION_SKIP_S}s "
                        f"(time-based boundary quarantine)"
                    )
                    self.current_label = new_label

                elif is_stop:
                    # Scenario ending — keep current label (do NOT revert to NORMAL)
                    # Arm cooldown suppression to block writes during _between_scenarios()
                    # traffic_gen waits 5s between scenarios → we suppress for 8s to be safe
                    self._flush_all()
                    print(f"\n[LABEL] STOP {data['event']} — keeping label={self.current_label}")
                    self._transition_skip = TRANSITION_SKIP_ROWS
                    self._transition_quarantine_until = time.time() + TRANSITION_SKIP_S
                    print(
                        f"[LABEL] Suppressing rows for {TRANSITION_SKIP_S}s "
                        f"(cooldown suppression — no data during _between_scenarios)"
                    )
                    # current_label stays unchanged → next start will update it

            # ---- TC loss update ------------------------------------------
            elif mtype == 'loss':
                loss_key = (data['tx_dpid'], data['tx_port'])
                self.link_loss[loss_key] = data['loss']
                self._flush_all()

            # ---- QoE bundle (from monitor_telecom.py) --------------------
            elif mtype == 'qoe':
                qoe_key = (data['tx_dpid'], data['tx_port'])
                self.qoe_buffer[qoe_key] = data

            # ---- FIX 1: AStream measured streaming metrics ---------------
            elif mtype == 'streaming_measured':
                segment = data.get('segment', 'IMS_CDN')
                # Ensure timestamp is stored for staleness check
                data.setdefault('timestamp', time.time())
                self.streaming_measured_buffer[segment] = data
                print(
                    f"\n[MEASURED] {segment} | "
                    f"start={data.get('video_start_time_ms', 0):.0f}ms "
                    f"buf={data.get('buffering_ratio', 0):.3f} "
                    f"stalls={data.get('rebuffering_count', 0)} "
                    f"MOS={data.get('streaming_mos', 0):.2f} "
                    f"[real AStream valid for {STREAMING_MAX_AGE_S}s]"
                )

            # ---- FIX 1: Data-plane ping latency probes -------------------
            elif mtype == 'dataplane_latency':
                segment = data.get('segment')
                if segment:
                    now_ts = time.time()
                    self.dataplane_latency_buffer[segment] = {
                        'latency_ms':          data.get('latency_ms', 0.0),
                        'jitter_ms':           data.get('jitter_ms', 0.0),
                        'source_timestamp':    data.get('timestamp', now_ts),
                        'received_timestamp':  now_ts,
                    }
                    print(
                        f" [PING] {segment:12s} | "
                        f"OWD={data.get('latency_ms', 0):.1f}ms "
                        f"jitter={data.get('jitter_ms', 0):.2f}ms "
                        f"RTT={data.get('rtt_ms', 0):.1f}ms"
                    )

            # ---- NEW: Flow count from monitor_telecom.py -----------------
            elif mtype == 'flow_count':
                dpid = data.get('dpid')
                if dpid is not None:
                    self.flow_count_buffer[dpid] = data.get('count', 0)

            # ---- Port stats (raw) ----------------------------------------
            elif mtype == 'port':
                required = {
                    's_id', 'p_no', 'tx_packets', 'rx_packets',
                    'tx_bytes', 'rx_bytes', 'throughput', 'timestamp'
                }
                if not required.issubset(data.keys()):
                    continue
                s_id, p_no = data['s_id'], data['p_no']
                if p_no > 100:
                    continue
                # Export only segment-level links (s1-eth1..s1-eth4 + leaf uplinks).
                if not self._is_segment_port(s_id, p_no):
                    continue

                ts      = data['timestamp']
                row_key = (s_id, p_no)
                self.pending_port[(s_id, p_no)] = {
                    'run_id':         self.run_id,
                    'timestamp':      ts,
                    'datetime':       datetime.fromtimestamp(ts).strftime('%H:%M:%S'),
                    'switch_id':      s_id,
                    'port_no':        p_no,
                    'throughput_mbps': round(data['throughput'], 6),
                    'packet_loss_rate': 0.0,
                    # Use ctrl_plane_rtt from port message (renamed from 'latency')
                    'latency_ms':     data.get('ctrl_plane_rtt', data.get('latency', 0.0)),
                    'jitter_ms':      data.get('ctrl_jitter', data.get('jitter', 0.0)),
                    'rx_bytes':   self._normalize_counter(row_key, 'rx_bytes',   data['rx_bytes']),
                    'tx_bytes':   self._normalize_counter(row_key, 'tx_bytes',   data['tx_bytes']),
                    'rx_packets': self._normalize_counter(row_key, 'rx_packets', data['rx_packets']),
                    'tx_packets': self._normalize_counter(row_key, 'tx_packets', data['tx_packets']),
                    'rx_dropped': self._normalize_counter(row_key, 'rx_dropped', data.get('rx_dropped', 0)),
                    'tx_dropped': self._normalize_counter(row_key, 'tx_dropped', data.get('tx_dropped', 0)),
                    'label': self.current_label,
                }
                # FIX 3: Flush as soon as all 4 s1 ports are present.
                # Do NOT wait for all 8 ports (s1 x4 + leaf x4) — this would
                # delay writes if a leaf switch is slow or missing.
                if sum(1 for (sw, _) in self.pending_port if sw == 1) >= 4:
                    self._flush_all()


if __name__ == '__main__':
    qoe_file = sys.argv[1] if len(sys.argv) > 1 else 'network_qoe.csv'
    raw_file = sys.argv[2] if len(sys.argv) > 2 else 'network_raw.csv'
    TelecomQoEListener(qoe_file, raw_file).run()
