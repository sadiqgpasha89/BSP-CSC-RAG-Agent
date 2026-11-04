/**
 * Enterprise Telco Agentic Care Platform - Interactive Client Logic
 */

/**
 * Escape untrusted text before it is interpolated into an innerHTML template. Customer utterances flow through the
 * execution plan and the HITL dossier, so without this a crafted chat message becomes stored XSS in the
 * supervisor's browser.
 */
function esc(value) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (c) => map[c]);
}

/**
 * fetch() wrapper for privileged endpoints. Outside development the API requires an operator key (X-API-Key); on a
 * 401 we prompt once and keep the key for the browser session only (never localStorage).
 */
async function apiFetch(url, options = {}) {
  const withKey = () => {
    const key = sessionStorage.getItem("operatorKey");
    return key ? { ...options, headers: { ...(options.headers || {}), "X-API-Key": key } } : options;
  };
  let res = await fetch(url, withKey());
  if (res.status === 401) {
    const key = window.prompt("Operator API key required for this action:");
    if (key) {
      sessionStorage.setItem("operatorKey", key);
      res = await fetch(url, withKey());
    }
  }
  return res;
}

document.addEventListener("DOMContentLoaded", () => {
  // Global State
  let activeSubscriberId = "SUB-882194";
  let turnSentiments = [];
  let currentSessionId = "sess-" + Math.random().toString(36).substring(2, 10);

  // Subscriber Catalog Cache
  const subscriberProfiles = {
    "SUB-882194": {
      name: "Alex Mercer",
      tier: "Enterprise Platinum",
      plan: "5G Ultra Max Unlimited (2026)",
      plan_code: "5G_ULTRA_MAX_2026",
      sector: "Sector-7 (Sunset District)",
      tower: "TOWER-104",
      lastBill: "$124.50",
      unbilled: "$38.50 (Disputed Roaming: $30.00)",
      churnRisk: 0.82,
      equipment: "iPhone 16 Pro Max",
    },
    "SUB-102948": {
      name: "Elena Rostova",
      tier: "Legacy Consumer",
      plan: "Freedom Unlimited (2018 Grandfathered)",
      plan_code: "FREEDOM_UNLIMITED_2018",
      sector: "Sector-1 (Downtown)",
      tower: "TOWER-101",
      lastBill: "$50.00",
      unbilled: "$0.00",
      churnRisk: 0.15,
      equipment: "Google Pixel 8 Pro",
    },
    "SUB-449182": {
      name: "Marcus Vance",
      tier: "Broadband VIP",
      plan: "GigaWave Fiber 1Gbps (2026)",
      plan_code: "FIBER_GIGA_1G_2026",
      sector: "Sector-7 (Sunset District)",
      tower: "TOWER-104",
      lastBill: "$65.00",
      unbilled: "$0.00",
      churnRisk: 0.45,
      equipment: "GigaWave Gateway (GW-AX6000)",
    },
    "SUB-773019": {
      name: "David Chen",
      tier: "Standard Consumer",
      plan: "5G Ultra Max Unlimited (2026)",
      plan_code: "5G_ULTRA_MAX_2026",
      sector: "Sector-2 (Financial District)",
      tower: "TOWER-102",
      lastBill: "$80.00",
      unbilled: "$5.00",
      churnRisk: 0.22,
      equipment: "Samsung Galaxy S24 Ultra",
    }
  };

  // 1. Navigation Tab Switching
  const navTabs = document.querySelectorAll(".nav-tab");
  navTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      navTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      const targetId = tab.getAttribute("data-tab");
      document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));
      const activePane = document.getElementById(targetId);
      if (activePane) activePane.classList.add("active");

      // Auto-trigger tab specific data refresh
      if (targetId === "tab-noc") loadNocData();
      if (targetId === "tab-hybrid") loadHybridTelemetry();
      if (targetId === "tab-mlops") loadMlopsData();
      if (targetId === "tab-metrics") loadMetricsSummary();
      if (targetId === "tab-hitl") loadHitlQueue();
    });
  });

  // 2. Subscriber Profile Hydration
  const subSelect = document.getElementById("subscriber-select");
  const subDetailsBox = document.getElementById("sub-details-box");
  const chatSubIndicator = document.getElementById("chat-sub-indicator");

  function renderSubscriberProfile(subId) {
    activeSubscriberId = subId;
    const sub = subscriberProfiles[subId];
    if (!sub) return;

    chatSubIndicator.textContent = `Active Subscriber: ${sub.name} (${subId}) | Plan: ${sub.plan_code}`;

    subDetailsBox.innerHTML = `
      <div class="sub-detail-row"><span class="sub-detail-label">Name & Tier:</span> <strong>${esc(sub.name)} (${esc(sub.tier)})</strong></div>
      <div class="sub-detail-row"><span class="sub-detail-label">Plan Code:</span> <code>${esc(sub.plan_code)}</code></div>
      <div class="sub-detail-row"><span class="sub-detail-label">Cell Sector:</span> <span>${esc(sub.sector)}</span></div>
      <div class="sub-detail-row"><span class="sub-detail-label">Last Bill / Unbilled:</span> <span>${esc(sub.lastBill)} / ${esc(sub.unbilled)}</span></div>
      <div class="sub-detail-row"><span class="sub-detail-label">Churn Risk ($\delta$):</span> <strong class="${sub.churnRisk > 0.75 ? 'text-danger' : 'text-accent'}">${esc(sub.churnRisk)}</strong></div>
      <div class="sub-detail-row"><span class="sub-detail-label">Active Hardware:</span> <span>${esc(sub.equipment)}</span></div>
    `;
  }

  subSelect.addEventListener("change", (e) => {
    renderSubscriberProfile(e.target.value);
  });
  renderSubscriberProfile("SUB-882194");

  // 3. One-Click Scenario Chips
  const promptChips = document.querySelectorAll(".prompt-chip");
  const chatInput = document.getElementById("chat-input-text");
  promptChips.forEach(chip => {
    chip.addEventListener("click", () => {
      const prompt = chip.getAttribute("data-prompt");
      chatInput.value = prompt;
      handleSendMessage();
    });
  });

  // 4. Chat Sending & Response Processing
  const sendBtn = document.getElementById("send-btn");
  const chatMessages = document.getElementById("chat-messages");
  const voiceToggle = document.getElementById("voice-mode-toggle");

  sendBtn.addEventListener("click", handleSendMessage);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleSendMessage();
  });

  async function handleSendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    chatInput.value = "";
    appendChatMessage("user", text);

    // Show typing state
    const loadingId = appendChatMessage("agent", "AI Reasoning & Orchestration in progress...");
    sendBtn.disabled = true;

    try {
      const payload = {
        subscriber_id: activeSubscriberId,
        message: text,
        session_id: currentSessionId,
        historical_sentiments: turnSentiments,
        is_audio: voiceToggle.checked,
      };

      const res = await fetch("/api/v1/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      removeChatMessage(loadingId);

      if (!res.ok) {
        appendChatMessage("agent", res.status === 404 ? "Subscriber not found." : "The service could not process that request. Please try again.");
      } else if (data.final_response) {
        appendChatMessage("agent", data.final_response);
        updateTraceDAG(data);
        if (data.sentiment_score !== undefined) {
          turnSentiments.push(data.sentiment_score);
        }
      } else {
        appendChatMessage("agent", "An unexpected error occurred during processing.");
      }
    } catch (err) {
      removeChatMessage(loadingId);
      appendChatMessage("agent", `Error connecting to backend: ${err.message}`);
    } finally {
      sendBtn.disabled = false;
    }
  }

  function appendChatMessage(role, content) {
    const msgDiv = document.createElement("div");
    const msgId = "msg-" + Math.random().toString(36).substring(2, 9);
    msgDiv.id = msgId;
    msgDiv.className = `message ${role}-msg`;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.textContent = content;

    msgDiv.appendChild(bubble);
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return msgId;
  }

  function removeChatMessage(msgId) {
    const el = document.getElementById(msgId);
    if (el) el.remove();
  }

  // 5. Update Cognitive Reasoning Trace & Guardrail Panel
  function updateTraceDAG(data) {
    document.getElementById("trace-step-counter").textContent = `${data.steps_executed} Steps Executed`;
    document.getElementById("trace-intent").textContent = `${data.intent_detected} ${data.is_multi_intent ? '(Multi-Intent)' : ''}`;
    document.getElementById("trace-sentiment").textContent = `${data.sentiment_label}`;
    document.getElementById("trace-churn").textContent = `${data.churn_risk} ${data.churn_risk > 0.75 ? '⚠️ High' : '✓ Nominal'}`;
    
    // Cloud Routing Badge
    const routing = data.cloud_routing || {};
    document.getElementById("trace-cloud").textContent = `${routing.provider || 'OpenStack'} (${routing.model_name || 'vLLM'})`;
    
    // Guardrail Verdict
    const guardrailEl = document.getElementById("trace-guardrail");
    if (data.guardrail_passed) {
      guardrailEl.innerHTML = `<span class="badge badge-accent">PASSED (Grounded)</span>`;
    } else {
      guardrailEl.innerHTML = `<span class="badge badge-danger">VIOLATION BLOCKED</span>`;
    }

    // Execution Steps Feed
    const stepsContainer = document.getElementById("trace-steps-container");
    stepsContainer.innerHTML = "";

    if (data.execution_plan && data.execution_plan.length > 0) {
      data.execution_plan.forEach((task, idx) => {
        const stepCard = document.createElement("div");
        stepCard.className = "step-card";
        stepCard.innerHTML = `
          <div class="step-card-header">
            <span>Step ${idx + 1}: ${esc(task.target_agent)}</span>
            <span class="step-tool-badge">${esc(task.status)}</span>
          </div>
          <div class="step-thought">${esc(task.instruction)}</div>
        `;
        stepsContainer.appendChild(stepCard);
      });
    } else {
      stepsContainer.innerHTML = `<div class="empty-trace-placeholder">Resolved autonomously with zero sub-agent escalations.</div>`;
    }

    // Hybrid RAG Citations
    const ragContainer = document.getElementById("trace-rag-container");
    ragContainer.innerHTML = "";

    if (data.retrieved_chunks_count > 0) {
      ragContainer.innerHTML = `
        <div class="rag-citation-card">
          <div class="citation-title">Grounded Context Chunks (${esc(data.retrieved_chunks_count)} retrieved via RRF)</div>
          <p style="font-size:0.75rem; color: var(--text-secondary);">4-Tier Hybrid: Dense Cosine ANN + Sparse BM25 + GraphRAG + Cross-Encoder Neural Reranking</p>
        </div>
      `;
    } else {
      ragContainer.innerHTML = `<div class="empty-trace-placeholder">No RAG contexts queried for this turn.</div>`;
    }

    // Refresh telemetry
    loadHybridTelemetry();
  }

  // 6. NOC Data Loader & Triggers
  async function loadNocData() {
    try {
      const topoRes = await fetch("/api/v1/network/topology");
      const topoData = await topoRes.json();
      const visualizer = document.getElementById("topology-visualizer");
      visualizer.innerHTML = "";

      if (topoData.nodes) {
        topoData.nodes.forEach(n => {
          const card = document.createElement("div");
          const isAlarm = n.data && n.data.status && n.data.status.includes("alarm");
          card.className = `topology-node-card ${isAlarm ? 'alarm' : ''}`;
          card.innerHTML = `
            <div class="node-title">${esc(n.id)}</div>
            <div style="font-size:0.72rem; color:var(--text-muted);">${esc(n.data.name || '')}</div>
            <span class="badge ${isAlarm ? 'badge-danger' : 'badge-accent'}" style="margin-top:6px;">
              ${esc(n.data.status || 'operational')}
            </span>
          `;
          visualizer.appendChild(card);
        });
      }

      // Load alarms
      const alarmRes = await fetch("/api/v1/network/alarms");
      const alarms = await alarmRes.json();
      const alarmsContainer = document.getElementById("active-alarms-list");
      alarmsContainer.innerHTML = "";

      alarms.forEach(a => {
        const item = document.createElement("div");
        item.className = "alarm-alert-box";
        item.innerHTML = `
          <strong>[${esc(a.severity)}] ${esc(a.alarm_type)}</strong> on ${esc(a.target_component)} (${esc(a.sector)})<br>
          <span style="font-size:0.75rem; color:var(--text-secondary);">${esc(a.root_cause)}</span><br>
          <small style="color:var(--accent-amber);">Estimated Restoration: ${esc(a.estimated_restoration_time)} | Impacted: ${esc(a.subscribers_impacted_count)} lines</small>
        `;
        alarmsContainer.appendChild(item);
      });
    } catch (e) {
      console.error("Failed to load NOC data", e);
    }
  }

  // OTDR Fiber Test Trigger
  document.getElementById("trigger-otdr-btn").addEventListener("click", async () => {
    const consoleEl = document.getElementById("diagnostic-output-console");
    consoleEl.innerHTML = `<pre>Sending optical pulse down distribution trunk OLT-OPTICAL-01/PON-04...</pre>`;
    try {
      const res = await apiFetch("/api/v1/network/otdr-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ olt_port: "OLT-OPTICAL-01/PON-04", subscriber_id: activeSubscriberId })
      });
      const data = await res.json();
      consoleEl.innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
    } catch (e) {
      consoleEl.innerHTML = `<pre>OTDR Test failed: ${esc(e.message)}</pre>`;
    }
  });

  // Core HSS Soft Reset Trigger
  document.getElementById("trigger-hss-reset-btn").addEventListener("click", async () => {
    const consoleEl = document.getElementById("diagnostic-output-console");
    consoleEl.innerHTML = `<pre>Dispatching Diameter soft-reset command to Ericsson 5G UDM / HSS Core...</pre>`;
    try {
      const res = await apiFetch("/api/v1/network/core-reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ msisdn: "+1-415-555-0199" })
      });
      const data = await res.json();
      consoleEl.innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
    } catch (e) {
      consoleEl.innerHTML = `<pre>Reset failed: ${esc(e.message)}</pre>`;
    }
  });

  // 7. Hybrid Cloud Telemetry
  async function loadHybridTelemetry() {
    try {
      const res = await fetch("/api/v1/metrics/cost-arbitrage");
      const data = await res.json();

      document.getElementById("hybrid-openstack-pct").textContent = `${data.openstack_percentage ?? 0}%`;
      document.getElementById("hybrid-gcp-pct").textContent = `${data.gcp_total_percentage ?? 0}%`;
      const savingsStr = `$${(data.cumulative_arbitrage_savings_usd || 0).toFixed(2)}`;
      document.getElementById("hybrid-savings-val").textContent = savingsStr;
      document.getElementById("header-savings").textContent = savingsStr;
    } catch (e) {
      console.error("Failed to load hybrid telemetry", e);
    }
  }

  // 8. MLOps Drift & Self-Healing
  async function loadMlopsData() {
    try {
      const res = await fetch("/api/v1/mlops/drift");
      const data = await res.json();

      document.getElementById("drift-psi-val").textContent = `PSI: ${data.psi_score}`;
      document.getElementById("drift-ks-val").textContent = `p-value: ${data.ks_p_value}`;
      document.getElementById("drift-concept-val").textContent = `Δ: ${data.semantic_centroid_drift}`;
      document.getElementById("drift-rag-val").textContent = `Ratio: ${(data.current_rag_similarity_ratio * 100).toFixed(1)}%`;

      // Load model registry
      const modRes = await fetch("/api/v1/mlops/models");
      const models = await modRes.json();
      const tbody = document.getElementById("models-table-body");
      tbody.innerHTML = "";
      models.forEach(m => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${esc(m.version_id)}</strong> (${esc(m.quantization)})</td>
          <td>${esc(m.deployment_target)}</td>
          <td>${esc(m.intent_f1_score)}</td>
          <td>${esc(m.hallucination_rate)}</td>
          <td><span class="badge ${m.status === 'PRODUCTION' ? 'badge-accent' : 'badge-warning'}">${esc(m.status)}</span></td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error("Failed to load MLOps telemetry", e);
    }
  }

  document.getElementById("run-drift-check-btn").addEventListener("click", loadMlopsData);

  document.getElementById("trigger-heal-btn").addEventListener("click", async () => {
    const logEl = document.getElementById("healing-actions-log");
    logEl.innerHTML = "Evaluating detected drift against automated self-healing mitigation policies...";
    try {
      const res = await apiFetch("/api/v1/mlops/self-heal", { method: "POST" });
      const data = await res.json();
      let html = `<strong style="color:var(--accent-cyan)">Self-Healing Interventions Executed:</strong><br>`;
      if (data.actions_taken && data.actions_taken.length > 0) {
        data.actions_taken.forEach(act => {
          html += `* [${esc(act.action_type)}] on <strong>${esc(act.target_component)}</strong>: ${esc(act.justification)}<br>`;
        });
      } else {
        html += `<span style="color:var(--accent-green)">Nominal status: No active drift violations. All model weights and tool rate limits are optimal.</span>`;
      }
      logEl.innerHTML = html;
    } catch (e) {
      logEl.textContent = `Failed: ${e.message}`;
    }
  });

  // 9. Unified 6-Category Metrics Summary
  async function loadMetricsSummary() {
    try {
      const res = await fetch("/api/v1/metrics/summary");
      const d = await res.json();

      // Business
      document.getElementById("metric-fcr").textContent = `${d.business.first_contact_resolution_pct}%`;
      document.getElementById("metric-aht").textContent = `${d.business.average_handling_time_sec}s`;
      document.getElementById("metric-deflection").textContent = `${d.business.agent_deflection_rate_pct}%`;
      document.getElementById("metric-csat").textContent = `${d.business.csat_score} / 5`;
      document.getElementById("metric-cpi").textContent = `$${d.business.cost_per_interaction_usd.toFixed(2)}`;

      // System
      document.getElementById("metric-ttft").textContent = `${d.system.time_to_first_token_ms} ms`;
      document.getElementById("metric-itl").textContent = `${d.system.inter_token_latency_ms} ms`;
      document.getElementById("metric-p95").textContent = `${d.system.p95_latency_ms} ms`;
      document.getElementById("metric-throughput").textContent = `${d.system.token_throughput_per_sec}`;
      document.getElementById("metric-gpu").textContent = `${d.system.gpu_vram_utilization_pct}%`;

      // RAG
      document.getElementById("metric-rag-prec").textContent = `${(d.rag.context_precision * 100).toFixed(1)}%`;
      document.getElementById("metric-rag-rec").textContent = `${(d.rag.context_recall * 100).toFixed(1)}%`;
      document.getElementById("metric-rag-faith").textContent = `${(d.rag.faithfulness_score * 100).toFixed(1)}%`;
      document.getElementById("metric-rag-relev").textContent = `${(d.rag.answer_relevance * 100).toFixed(1)}%`;
      document.getElementById("metric-rag-halluc").textContent = `${d.rag.hallucination_frequency_pct}%`;

      // Agentic
      document.getElementById("metric-agent-eff").textContent = `${(d.agentic.agent_path_efficiency * 100).toFixed(1)}%`;
      document.getElementById("metric-agent-tisr").textContent = `${d.agentic.tool_invocation_success_rate_pct}%`;
      document.getElementById("metric-agent-replan").textContent = `${d.agentic.autonomous_replanning_rate_pct}%`;
      document.getElementById("metric-agent-gcr").textContent = `${d.agentic.goal_completion_rate_pct}%`;

      // ML
      document.getElementById("metric-ml-intent").textContent = `${d.ml_nlp.intent_classification_macro_f1}`;
      document.getElementById("metric-ml-ner").textContent = `${d.ml_nlp.ner_token_level_f1}`;
      document.getElementById("metric-ml-wer").textContent = `${(d.ml_nlp.asr_word_error_rate_wer * 100).toFixed(1)}%`;
      document.getElementById("metric-ml-churn").textContent = `${d.ml_nlp.churn_prediction_roc_auc}`;
    } catch (e) {
      console.error("Failed to load metrics summary", e);
    }
  }

  // 10. HITL Queue Loader
  async function loadHitlQueue() {
    try {
      const res = await apiFetch("/api/v1/hitl/queue");
      const queue = await res.json();
      const container = document.getElementById("hitl-queue-list");
      document.getElementById("hitl-count-badge").textContent = queue.length;
      container.innerHTML = "";

      if (queue.length === 0) {
        container.innerHTML = `
          <div class="empty-trace-placeholder" style="padding:40px;">
            🎉 All customer support sessions resolved autonomously. HITL Escalation Queue is currently empty!
          </div>
        `;
        return;
      }

      queue.forEach(m => {
        const card = document.createElement("div");
        card.className = "manifest-card";
        card.innerHTML = `
          <div class="manifest-header">
            <strong>Escalation ID: ${esc(m.escalation_id)} | Subscriber: ${esc(m.subscriber_id)} (${esc(m.msisdn)})</strong>
            <span class="badge badge-warning">${esc(m.status)}</span>
          </div>
          <div class="manifest-body">
            <strong>Reason:</strong> ${esc(m.escalation_reason)}<br>
            <strong>Summary:</strong> ${esc(m.root_cause_summary)}<br>
            <strong>Identity State:</strong> ${esc(m.verified_identity_state)} | Account CLV: $${esc(m.clv_usd)}
          </div>
          <div class="manifest-actions">
            <button class="btn btn-primary btn-sm resolve-esc-btn" data-id="${esc(m.escalation_id)}">Mark Resolved with Override</button>
          </div>
        `;
        container.appendChild(card);
      });

      document.querySelectorAll(".resolve-esc-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const escId = btn.getAttribute("data-id");
          await apiFetch("/api/v1/hitl/resolve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ escalation_id: escId, resolution_notes: "Representative reviewed and confirmed override." })
          });
          loadHitlQueue();
        });
      });
    } catch (e) {
      console.error("Failed to load HITL queue", e);
    }
  }

  // Initial Load
  loadHybridTelemetry();
  loadMetricsSummary();
});
