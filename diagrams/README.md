# Diagrams

Mermaid sources are in `src/`; rendered SVGs are in `svg/`. Regenerate with `bash diagrams/render.sh` (needs `@mermaid-js/mermaid-cli`). `bash diagrams/render.sh --check` only verifies that every source parses (this is what CI runs). GitHub also renders the `.mmd` files directly.

| # | Diagram | Type | Shows |
|---|---|---|---|
| 01 | [System context](svg/01-system-context.svg) | flowchart | Customers, operators, platform boundary, enterprise systems (mock today) |
| 02 | [Components](svg/02-component-architecture.svg) | flowchart | API layer, core services, orchestration, tool gateway, knowledge, observability |
| 03 | [Agent topology](svg/03-agent-topology.svg) | flowchart | Turn pipeline: language, control flow, intent, routing, guardrails, handover |
| 04 | [Balance and usage](svg/04-seq-balance-usage.svg) | sequence | Journey 1 |
| 05 | [Bill explanation and dispute](svg/05-seq-bill-dispute.svg) | sequence | Journey 2 |
| 06 | [Recharge and payment](svg/06-seq-recharge-payment.svg) | sequence | Journey 3, including declined, pending, unknown and OCS-failure outcomes |
| 07 | [Troubleshooting](svg/07-seq-troubleshooting.svg) | sequence | Journey 4, outage and proactive notification |
| 08 | [Complaint and tracking](svg/08-seq-complaint.svg) | sequence | Journey 5, consent, dedup |
| 09 | [Plan change](svg/09-seq-plan-change.svg) | sequence | Eligibility, warning, read-back verification |
| 10 | [SIM/eSIM with step-up](svg/10-seq-sim-esim-stepup.svg) | sequence | OTP step-up, resumption, human approval |
| 11 | [Human handover](svg/11-seq-human-handover.svg) | sequence | Claim, reply relay, resolve |
| 12 | [Class diagram](svg/12-class-domain.svg) | class | Gateway, policy, pending actions, agents, conversation |
| 13 | [Data model](svg/13-er-data-model.svg) | ER | Entities and relations (sandbox store) |
| 14 | [Deployment](svg/14-deployment.svg) | flowchart | What is in the container and what the operator must provide |
| 15 | [Pending action lifecycle](svg/15-state-pending-action.svg) | state | Confirmation, approval, unknown outcome |
| 16 | [Payment lifecycle](svg/16-state-payment.svg) | state | PENDING, SUCCESS, FAILED, UNKNOWN |
| 17 | [Handover and step-up states](svg/17-state-handover-stepup.svg) | state | |
| 18 | [Threat model DFD](svg/18-threat-model-dfd.svg) | flowchart | Trust boundaries and threats T1-T10 (see `docs/THREAT_MODEL.md`) |

Diagrams describe the implemented behaviour. Items marked "planned" in 14 are not implemented.
