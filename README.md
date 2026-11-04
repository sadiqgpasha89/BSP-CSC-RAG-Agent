# Enterprise Telco Autonomous Customer Support Platform (Agentic RAG & Hybrid Cloud)

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![Pytest Coverage](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](https://docs.pytest.org/)
[![Architecture](https://img.shields.io/badge/Cloud-OpenStack%20%2B%20GCP-orange.svg)](https://cloud.google.com/)

A production-grade, autonomous Customer Support Representative (CSR) platform for Tier-1 Telecommunications Operators. Built upon the comprehensive specification in [`enterprise_telco_agentic_rag_architecture.md`](./enterprise_telco_agentic_rag_architecture.md), this system implements a hybrid cloud architecture (OpenStack on-premises + Google Cloud Platform), blending classical Machine Learning, 4-tier Hybrid RAG, a Hierarchical Multi-Agent system, deterministic guardrails, and real-time operational telemetry.

---

## 🏛️ High-Level System Architecture

```
                                  [ INCOMING SUBSCRIBER STREAM ]
                                  (VoIP ASR, SMS, Web Chat, App)
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 1: PII / CPNI Scrubber & De-identifier │
                        │  (Regex + Spacy NER Enclave, Salted Tokens)   │
                        └───────────────────────┬────────────────────────┘
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 2: Predictive Inference Router         │
                        │  (Cost/Token & Latency Arbitrage Engine)       │
                        └───────┬──────────────────────────────┬─────────┘
                                │                              │
          Predictable / PII /   │                              │ Complex Multi-Turn /
          Zero Variable Cost    │                              │ High-Churn / Multimodal
                                ▼                              ▼
                 ┌─────────────────────────────┐┌──────────────────────────────┐
                 │    OpenStack (On-Prem)      ││     Google Cloud Platform    │
                 │  vLLM Llama-3.1-70B-Int4   ││  Vertex AI Gemini-1.5-Flash  │
                 │  Local OSS/BSS & Core IMS   ││  GKE Burst, BigQuery Lakes  │
                 └──────────────┬──────────────┘└──────────────┬───────────────┘
                                └──────────────┬───────────────┘
                                               ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 3: Classical ML & NLP Triage Engine   │
                        │  • Multi-Intent Classifier (τ = 0.82)          │
                        │  • Frustration Velocity: d(sentiment)/dt       │
                        │  • Churn Risk Predictor (δ = 0.75)            │
                        │  • Dialect / Code-Switching Normalizer        │
                        └───────────────────────┬────────────────────────┘
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 4: 4-Tier Hybrid RAG Engine            │
                        │  • Dense Cosine Vector Index (HNSW)            │
                        │  • Sparse Lexical BM25 (Acronyms & Hex Codes) │
                        │  • GraphRAG Network Topology Explorer          │
                        │  • Reciprocal Rank Fusion (RRF k=60) + Neural  │
                        │  • Strict Temporal / Grandfathered Isolation   │
                        └───────────────────────┬────────────────────────┘
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 5: Hierarchical Multi-Agent DAG        │
                        │  ┌──────────────────────────────────────────┐  │
                        │  │       SupervisorAgent (Meta-Planner)     │  │
                        │  └──────┬────────────┬───────────┬──────────┘  │
                        │         │            │           │             │
                        │         ▼            ▼           ▼             │
                        │    [Billing]     [Network]  [Provisioning]     │
                        │      Agent         Agent       Agent           │
                        │         │            │           │             │
                        │         └────────────┼───────────┘             │
                        │                      ▼                         │
                        │               [RetentionAgent]                 │
                        └───────────────────────┬────────────────────────┘
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 6: Enterprise OSS / BSS Tool Adapters  │
                        │  • Circuit Breakers (Quarantine & Half-Open)   │
                        │  • BSS Billing, Invoices & Autonomous Credit   │
                        │  • OSS OTDR Line Test, Tower Alarms, HSS Reset │
                        │  • Provisioning eSIM & Anti-SIM-Swap Auth     │
                        └───────────────────────┬────────────────────────┘
                                                │
                                                ▼
                        ┌────────────────────────────────────────────────┐
                        │   STAGE 7: Deterministic Safety Guardrails     │
                        │  • Grounding & NLI Factuality Scrutiny         │
                        │  • Regulatory & Anti-Hallucination Filters    │
                        │  • CPNI Leakage Prevention Enclave             │
                        └───────────────────────┬────────────────────────┘
                                                │
                        ┌───────────────────────┴────────────────────────┐
                        │                                                │
         [PASS: Grounded & Safe]                           [FAIL or High Risk Escalation]
                        │                                                │
                        ▼                                                ▼
        ┌───────────────────────────────┐               ┌─────────────────────────────────┐
        │  STAGE 8: Customer Delivery   │               │   STAGE 8b: HITL Warm Handoff   │
        │  (Restored Tokens & Channel)  │               │   • Tier-2 Human Dossier Queue  │
        │                               │               │   • Pre-filled Diagnostic Recap │
        └───────────────────────────────┘               └─────────────────────────────────┘
```

---

## ⚡ Key Highlights & Capabilities

### 1. Hybrid Cloud Arbitrage (OpenStack On-Prem + GCP)
* **OpenStack On-Premises**: Private cloud cluster hosting vLLM containerized engines serving quantized open weights (`Llama-3.1-70B-Instruct-Int4`) with zero variable token cost. Direct adjacency to sensitive BSS/OSS databases and carrier-grade IMS cores, strictly retaining PII/CPNI within the private telecom boundary.
* **Google Cloud Platform (GCP)**: Cloud burst environment deploying Vertex AI Gemini for complex multi-turn reasoning, multimodal network diagnostic images, and BigQuery serverless analytical lakehouses for real-time telemetry streaming and long-term cohort analytics.

### 2. Decoupled Data Engineering (MLOps & LLMOps Principles)
* **Continuous Ingestion Pipeline** ([`src/data_engineering/ingestion_pipeline.py`](./src/data_engineering/ingestion_pipeline.py)): Incremental chunking, metadata extraction, contractual validity range isolation, and dynamic vector indexing.
* **GraphRAG Topology Builder** ([`src/data_engineering/graph_rag_builder.py`](./src/data_engineering/graph_rag_builder.py)): NetworkX graph mapping cells, towers, backhaul routers, optical line terminals (OLT), and customer premises equipment (CPE) for upstream incident attribution.
* **Streaming Event Bus & Analytical Lakehouse** ([`src/data_engineering/stream_processor.py`](./src/data_engineering/stream_processor.py) & [`src/data_engineering/lakehouse_sink.py`](./src/data_engineering/lakehouse_sink.py)): Simulates high-throughput Kafka/PubSub stream processing and daily-partitioned BigQuery ingestion.

### 3. Classical ML & NLP Triage Engine
* **Multi-Intent Classifier** ([`src/datascience/intent_classifier.py`](./src/datascience/intent_classifier.py)): Supervised multi-label classifier calibrated against confidence threshold $\tau_{intent} = 0.82$. Supports simultaneous secondary intents (e.g., Billing Dispute + Churn Threat).
* **Frustration Velocity Tracker** ([`src/datascience/sentiment_analyzer.py`](./src/datascience/sentiment_analyzer.py)): Computes multi-turn sentiment slope $\frac{d(\text{sentiment})}{dt}$. If frustration velocity $< -0.5/\text{turn}$, escalates proactively before customer rage quit.
* **Predictive Churn Risk Scorer** ([`src/datascience/churn_predictor.py`](./src/datascience/churn_predictor.py)): Evaluates tenure, billing anomalies, open tickets, and recent network outages against threshold $\delta_{churn} > 0.75$. Triggers specialized Retention Agent workflow.
* **Dialect & Code-Switching Normalizer** ([`src/datascience/asr_transcriber.py`](./src/datascience/asr_transcriber.py)): Normalizes noisy ASR transcripts, Hinglish, Spanish-English code-switching, and telco colloquialisms into canonical intent queries.

### 4. 4-Tier Hybrid RAG with Strict Temporal Filtering
* **Tier 1 (Dense)**: Cosine similarity vector retrieval over chunk embeddings.
* **Tier 2 (Sparse)**: Lexical BM25 engine specialized in 3GPP acronyms, hex error codes (`0x4F`), and equipment model numbers (`GW-AX6000`).
* **Tier 3 (GraphRAG)**: Multi-hop graph exploration of network topology and diagnostic SOP dependencies.
* **Tier 4 (Neural Reranker & Fusion)**: Reciprocal Rank Fusion (RRF $k=60$) dynamically balanced with neural cross-encoder reranking.
* **Temporal Metadata Filter** ([`src/rag/temporal_filter.py`](./src/rag/temporal_filter.py)): Prevents modern plan limits from leaking into grandfathered 2018 plans (e.g., Alex Mercer on 2026 50GB hotspot vs. Elena Rostova on 2018 10GB hotspot).

### 5. Hierarchical Multi-Agent System & HITL Warm Handoff
* **SupervisorAgent** ([`src/agents/supervisor.py`](./src/agents/supervisor.py)): Central meta-planner constructing directed acyclic execution graphs (DAGs), managing shared blackboard state, and detecting infinite agent loops ($\le 6$ steps).
* **Specialized Worker Agents**:
  * `BillingAgent`: Inspects CDR items, verifies unbilled roaming, applies automatic credits up to $\$50.00$ (with supervisor approval up to $\$100.00$).
  * `NetworkAgent`: Queries tower health, runs virtual Optical Time-Domain Reflectometer (OTDR) fiber tests, and dispatches Diameter soft resets to Ericsson 5G UDM / HSS cores.
  * `ProvisioningAgent`: Handles eSIM profiles and strictly blocks unauthorized SIM swaps lacking Out-of-Band (OOB) MFA authentication.
  * `RetentionAgent`: Formulates targeted loyalty concessions for high-risk subscribers.
* **Tier-2 Human Handoff (HITL)** ([`src/hitl/`](./src/hitl/)): Generates structured warm handoff dossiers with verified identity states, attempted actions, root cause recaps, and pre-filled action items.

---

## 📊 Comprehensive 6-Category Metrics Engine

The platform measures and calculates metrics across all 6 telecom operational dimensions:

| Category | Metric | Mathematical Formulation / Description |
| :--- | :--- | :--- |
| **1. Business Metrics** | **First Contact Resolution (FCR)** | $\text{FCR} = \frac{\text{Sessions Resolved Without Re-contact}}{\text{Total Sessions}} \times 100$ |
| | **Average Handling Time (AHT)** | $\text{AHT} = \frac{1}{N} \sum_{i=1}^N T_{\text{duration}}(i)$ |
| | **Agent Deflection Rate** | $\text{Deflection} = \frac{\text{Sessions Completed Without Human Escalation}}{\text{Total Sessions}} \times 100$ |
| | **Customer Satisfaction (CSAT)** | $\text{CSAT} = \frac{1}{N} \sum_{i=1}^N \text{Score}_i \quad [1.0 - 5.0]$ |
| | **Net Promoter Score (NPS)** | $\text{NPS} = \% \text{Promoters} - \% \text{Detractors}$ |
| | **Cost Per Interaction (CPI)** | $\text{CPI} = \frac{\text{Total Compute, Token \& Infrastructure Cost}}{\text{Total Sessions}}$ |
| | **Churn Equity Preserved** | $\sum_{\text{retained}} \text{Subscriber CLV} \times P(\text{Churn Avoided})$ |
| **2. System Metrics** | **P95 / P99 Latency** | 95th and 99th percentile end-to-end turn response times |
| | **Time To First Token (TTFT)** | Elapsed time from HTTP request receipt to initial streaming token output |
| | **Inter-Token Latency (ITL)** | Mean delta time between consecutive emitted tokens |
| | **Token Throughput** | Global tokens generated per second across GPU server clusters |
| | **GPU VRAM & SM Utilization** | Hardware utilization of Tensor Core SMs and high-bandwidth memory |
| **3. RAG Metrics** | **Context Precision** | Fraction of retrieved knowledge chunks relevant to the ground truth |
| | **Context Recall** | Extent to which all facts needed to answer query are present in retrieved chunks |
| | **Faithfulness Score** | Fraction of response claims directly grounded in retrieved context: $\frac{\lvert C_{\text{grounded}} \rvert}{\lvert C_{\text{total}} \rvert}$ |
| | **Answer Relevance** | Semantic embedding similarity between user question and final response |
| | **Hallucination Frequency** | Percentage of turns where ungrounded factual assertions are detected |
| **4. Agentic Metrics** | **Agent Path Efficiency ($\eta$)** | $\eta = \frac{K^*}{K}$, where $K^*$ is optimal minimal steps and $K$ is actual execution steps |
| | **Tool Invocation Success (TISR)**| $\text{TISR} = \frac{\text{Successful Tool Calls}}{\text{Total Tool Invocations}} \times 100$ |
| | **Plan Deviation Index** | Normalized edit distance between initial planner DAG and executed task trajectory |
| | **Goal Completion Rate (GCR)** | Percentage of multi-step agent plans terminating in full sub-goal attainment |
| **5. ML / NLP Metrics** | **Intent Classification Macro F1** | Unweighted mean F1 score across all 11 telecommunications intent classes |
| | **Named Entity Recognition F1** | Token-level precision, recall, and F1 for telecommunications entity extraction |
| | **ASR Word Error Rate (WER)** | $\text{WER} = \frac{S + D + I}{N}$ across dialect and noisy acoustic inputs |
| | **Churn Prediction ROC-AUC** | Area under the Receiver Operating Characteristic curve for 30-day churn |
| **6. Cost & Arbitrage** | **OpenStack vs. GCP Savings** | Cumulative financial savings achieved by routing queries to on-prem vLLM |
| | **Cost Per 1K Tokens** | Weighted blended cost across on-prem fixed amortized infrastructure and cloud APIs |

---

## 🚀 Quickstart Guide

### 1. Prerequisites
* Python 3.11 or 3.12
* `uv` or `pip` package manager

### 2. Environment Setup
```bash
# Clone repository and navigate to root
cd BSP-CSC-RAG-Agent

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Test Suite
Execute the comprehensive test suite spanning unit, integration, edge cases, and performance:
```bash
# Run the full test suite with coverage report
pytest tests/ -v --cov=src
```

### 4. Launch the Enterprise Platform
Start the FastAPI server (serving both the REST API and the Command Center Dashboard):
```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

Open your browser and navigate to:
```
http://localhost:8000/
```

---

## 🔐 Security & Deployment Notes

| Setting | Development (`ENVIRONMENT=development`, default) | Anything else (staging / production) |
| :--- | :--- | :--- |
| `SECRET_KEY` | Insecure dev default allowed | **Required**, >= 32 chars; the app refuses to start otherwise |
| `ADMIN_API_KEY` | Optional | **Required**; privileged endpoints (`/network/core-reset`, `/network/otdr-test`, `/mlops/self-heal`, `/hitl/*`, `/chat/subscribers`) need header `X-API-Key` |
| Default customer assurance | `authenticated_mfa` (so the demo console works without a login flow) | `basic_pin`: SIM/eSIM changes and billing credits are denied unless the request carries a valid signed `session_token` |
| CORS | `CORS_ALLOWED_ORIGINS` allow-list (default: localhost) | same; no wildcard |

Generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Session tokens are minted by `SecurityService.generate_session_token(subscriber_id, msisdn, auth_level)` after your identity provider completes step-up MFA.

Runtime datasets live in `data/` (tracked) and are resolved relative to the project root (override with `DATA_DIR`), so the service can be started from any working directory.

A deep-dive on the architecture, sequence flows and the defects fixed in the engineering review is in [`docs/medium-article.md`](./docs/medium-article.md).

---

## 🖥️ Executive Command Center Dashboard

The frontend dashboard provides a dark-mode glassmorphic user interface for NOC engineers, customer support supervisors, and AI operations leads:

1. **Omnichannel CSR Console**:
   * Multi-subscriber selector (Alex Mercer, Elena Rostova, Marcus Vance, David Chen) with live plan and billing card hydration.
   * Real-time chat dialogue with audio transcription toggles.
   * Dynamic Cognitive Reasoning Trace feed detailing DAG steps, multi-intent detection, frustration velocity, and ground truth guardrail verdicts.
2. **Network Operations (NOC) & Topology**:
   * Interactive graph topology visualization of cellular towers, fiber backhauls, and OLT distribution nodes.
   * Live critical alarm monitors (e.g., active fiber cuts on Sector-7 Sunset Blvd).
   * Interactive diagnostics: Virtual OTDR optical pulse test triggers and Diameter HSS/UDM soft reset commands.
3. **Hybrid Cloud & Token Economics**:
   * Real-time cost comparison between on-premises OpenStack vLLM deployment and GCP cloud APIs.
   * Cost per 1K tokens and cumulative financial arbitrage counters.
4. **MLOps & Drift Surveillance**:
   * Population Stability Index (PSI) and Kolmogorov-Smirnov distribution drift monitoring.
   * Canary deployment model registry with shadow routing.
   * Automated one-click self-healing engine rebalancing RAG weights and tool rate limiters.
5. **6-Category Metrics Dashboard**:
   * Live operational telemetry covering Business, System, RAG, Agentic, ML, and Cost metrics.
6. **Tier-2 Human Escalation Queue (HITL)**:
   * Real-time warm handoff dossier feed with customer lifetime value (CLV), root cause recaps, and one-click supervisor resolution notes.

---

## 🔌 API Reference & Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Comprehensive health check and hybrid cloud connectivity status |
| `GET` | `/api/v1/chat/subscribers` | Returns mock enterprise subscriber profiles |
| `POST` | `/api/v1/chat/message` | Ingests subscriber utterance, executes 8-stage agentic pipeline, returns response |
| `GET` | `/api/v1/network/topology` | Returns complete NetworkX cell and backhaul node graph |
| `GET` | `/api/v1/network/alarms` | Returns real-time active network alarms |
| `POST` | `/api/v1/network/otdr-test` | Executes virtual OTDR fiber optical reflection analysis |
| `POST` | `/api/v1/network/core-reset` | Executes Diameter soft-reset on 5G Core UDM/HSS |
| `GET` | `/api/v1/metrics/summary` | Returns unified snapshot of all 6 metric categories |
| `GET` | `/api/v1/metrics/cost-arbitrage` | Returns OpenStack vs. GCP cost arbitrage statistics |
| `GET` | `/api/v1/mlops/drift` | Returns PSI and K-S drift surveillance indicators |
| `GET` | `/api/v1/mlops/models` | Returns model registry and active canary statuses |
| `POST` | `/api/v1/mlops/self-heal` | Triggers automated self-healing mitigation policies |
| `GET` | `/api/v1/hitl/queue` | Returns pending Tier-2 human escalation dossiers |
| `POST` | `/api/v1/hitl/resolve` | Resolves an active escalation dossier |
| `POST` | `/api/v1/rag/search` | Direct test endpoint for the 4-tier Hybrid RAG engine |
| `GET` | `/api/v1/rag/subgraph/{entity_id}` | Traverses the GraphRAG neighbourhood around an entity |

---

## 🛡️ Telco Edge Cases Handled

1. **Outage Storm Geofence Deflection**: Automatically intercepts subscribers connected to towers with active critical alarms (e.g. Sector-7 fiber slice) and deflects routine inquiries with automated broadcast messages to protect CSR queues.
2. **SIM-Swap Zero-Trust Anti-Fraud**: Strictly blocks high-risk SIM replacement and eSIM re-profiling if physical identity or out-of-band MFA authentication is missing.
3. **OSS / BSS Circuit Breakers**: Automatically isolates failing backend microservices (e.g., CDR billing query timeout) to prevent catastrophic cascading thread pool exhaustion.
4. **Agentic Loop Termination**: Monitors execution stack signatures; if the same agent and tool invocation repeats 3 times or total steps exceed 6, terminates loop and triggers warm handoff.
5. **Grandfathered Plan Temporal Isolation**: Automatically pins temporal contracts to subscriber activation dates, preventing invalid modern terms (e.g., 50GB hotspot) from being promised to legacy subscribers.
