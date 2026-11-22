# KPI Definitions and Baseline Plan

Source: `src/metrics/kpi.py`, endpoint `GET /api/v1/admin/kpis`, console tab "Measured KPIs". Every KPI returns `value`, `numerator`, `denominator`, `definition`, `source`, `note`. If the denominator is zero the value is **null** with a note; nothing is defaulted or estimated.

Principle (plan §13): never optimise containment alone. Read containment next to task completion, escalation reasons, CSAT and unsafe-action blocks.

| KPI | Definition | Source events | Caveat |
|---|---|---|---|
| containment_rate | Sessions that ended without human assistance / all sessions | session telemetry | Not a success measure alone |
| first_contact_resolution | Contained sessions whose customer did not contact us again in the window / all sessions | session telemetry | Proxy until contact outcome data is joined |
| task_completion_rate | State-changing operations the source system confirmed (read-back where available) / state-changing operations attempted | TOOL_CALL events | Pending/unknown count as not completed |
| average_handling_time_sec | Mean assistant turn duration | session telemetry | Human baseline comparison needs contact-centre data |
| csat | Mean customer rating (1-5) submitted via `/chat/csat` | conversation records | Null until ratings exist; response bias |
| api_execution_success | Successful authorised enterprise operations / attempted operations (policy denials excluded) | TOOL_CALL events | Denials appear in unsafe_action_blocks instead |
| escalation_rate (+ by reason) | Escalated sessions / sessions, bucketed: identity_or_fraud, policy_limit, customer_request, system_failure, low_confidence, sentiment, other | session telemetry | Buckets are keyword-based on the recorded reason |
| answer_accuracy_proxy | Drafts passing grounding and faithfulness guardrails / validated drafts | GUARDRAIL_VERDICT | **A proxy.** True accuracy needs a labelled evaluation set |
| payment_success | Payments confirmed SUCCESS / payments initiated; pending, failed, unknown reported separately | TOOL_CALL (`payment_status`, `verified`) | Reconciliation lag shows as pending |
| cost_per_interaction_usd | Estimated inference cost / sessions | session telemetry | Excludes infrastructure and human cost |
| latency_p50_ms / latency_p95_ms | Turn latency percentiles | session telemetry | Sandbox latency is not production latency |
| abandonment_rate | Confirmable actions cancelled, expired or unanswered / all confirmable actions | pending actions | |
| repeat_contact_rate | Customers with more than one session / customers | session telemetry | Window is the process lifetime |
| unsafe_action_blocks | Count of calls blocked by policy (cross-customer, least privilege, forbidden) | UNSAFE_ACTION_BLOCKED | A rise may be attack or a bug; investigate both |
| unsupported_answer_rate | Drafts blocked by guardrails / validated drafts | GUARDRAIL_VERDICT | |
| tool_failure_rate | Failed, unavailable or unknown outcomes / attempted | TOOL_CALL | |
| by_language (in the response, outside `kpis`) | Sessions and containment percentage per language | session telemetry | Containment only; quality needs human review per language |

## Baseline measurement plan

1. Run in a staging environment against real (or recorded) traffic for at least two full billing cycles; keep the legacy illustrative metrics tab out of any report.
2. Capture a human-handled sample for AHT, FCR and CSAT comparison.
3. Build a labelled evaluation set (a few hundred questions per language with approved answers and expected tool calls) and measure answer accuracy and tool selection offline.
4. Agree targets with operations after steps 1–3. This repository sets none.
5. Persist KPI events outside the process (they are in an in-memory buffer today).
