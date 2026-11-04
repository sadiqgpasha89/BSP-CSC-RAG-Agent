"""Integration tests for API hardening (validation, auth, CORS, error handling)."""

import pytest

from src.core.config import settings


def test_chat_requires_subscriber_and_valid_message(test_client):
    assert test_client.post("/api/v1/chat/message", json={"message": "hi"}).status_code == 422          # no default subscriber
    assert test_client.post("/api/v1/chat/message", json={"subscriber_id": "SUB-882194", "message": ""}).status_code == 422
    too_long = "x" * (settings.max_message_chars + 1)
    assert test_client.post("/api/v1/chat/message", json={"subscriber_id": "SUB-882194", "message": too_long}).status_code == 422


def test_unknown_subscriber_is_404_without_internal_detail(test_client):
    res = test_client.post("/api/v1/chat/message", json={"subscriber_id": "SUB-000000", "message": "hello"})
    assert res.status_code == 404
    assert res.json() == {"detail": "Subscriber not found"}


def test_internal_errors_do_not_leak_details(test_client, monkeypatch):
    from src.agents.supervisor import supervisor_agent

    def explode(*a, **k):
        raise RuntimeError("secret internal path /srv/db password=hunter2")

    monkeypatch.setattr(supervisor_agent, "process_turn", explode)
    res = test_client.post("/api/v1/chat/message", json={"subscriber_id": "SUB-882194", "message": "hello"})
    assert res.status_code == 500
    assert "hunter2" not in res.text and "/srv/db" not in res.text


def test_cors_does_not_reflect_arbitrary_origins(test_client):
    res = test_client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in res.headers}
    ok = test_client.get("/api/v1/health", headers={"Origin": "http://localhost:8000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_health_reports_knowledge_base_state(test_client):
    body = test_client.get("/api/v1/health").json()
    assert body["knowledge_base_indexed"] is True and body["indexed_chunks"] > 0
    assert body["status"] == "HEALTHY"


def test_topology_endpoint_returns_the_whole_graph(test_client):
    data = test_client.get("/api/v1/network/topology").json()
    ids = {n["id"] for n in data["nodes"]}
    assert {"TOWER-101", "TOWER-104", "OLT-OPTICAL-01", "CORE-HSS-UDM"} <= ids


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/v1/network/core-reset", {"msisdn": "+1-415-555-0199"}),
        ("post", "/api/v1/network/otdr-test", {"olt_port": "OLT-OPTICAL-01/PON-04", "subscriber_id": "SUB-449182"}),
        ("post", "/api/v1/mlops/self-heal", None),
        ("get", "/api/v1/hitl/queue", None),
        ("post", "/api/v1/hitl/resolve", {"escalation_id": "ESC-X", "resolution_notes": "n"}),
        ("get", "/api/v1/chat/subscribers", None),
    ],
)
def test_privileged_endpoints_require_operator_key_outside_development(test_client, monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_api_key", "operator-secret-key")

    call = getattr(test_client, method)
    kwargs = {"json": body} if body is not None else {}
    assert call(path, **kwargs).status_code == 401
    assert call(path, headers={"X-API-Key": "wrong"}, **kwargs).status_code == 401
    assert call(path, headers={"X-API-Key": "operator-secret-key"}, **kwargs).status_code != 401


def test_hitl_resolve_flow_over_http(test_client):
    from src.agents.state import AgenticSessionState
    from src.hitl.warm_handoff import warm_handoff_manager

    state = AgenticSessionState(session_id="s", trace_id="t", subscriber_id="SUB-882194", sanitized_utterance="hello")
    esc_id = warm_handoff_manager.create_manifest(state, "api test").escalation_id

    ok = test_client.post("/api/v1/hitl/resolve", json={"escalation_id": esc_id, "resolution_notes": "done"})
    assert ok.status_code == 200
    assert test_client.post("/api/v1/hitl/resolve", json={"escalation_id": esc_id, "resolution_notes": "again"}).status_code == 409
    assert test_client.post("/api/v1/hitl/resolve", json={"escalation_id": "ESC-NOPE", "resolution_notes": "x"}).status_code == 404


def test_network_inputs_are_validated(test_client):
    assert test_client.post("/api/v1/network/core-reset", json={"msisdn": "'; DROP TABLE x;--"}).status_code == 422
    assert test_client.post("/api/v1/network/otdr-test", json={"olt_port": "../../etc/passwd", "subscriber_id": "SUB-1"}).status_code == 422
    assert test_client.post("/api/v1/rag/search", json={"query": "otdr", "top_k": 10_000}).status_code == 422


def test_chat_session_token_elevates_assurance_in_production(test_client, monkeypatch):
    from src.core.security import security_service

    monkeypatch.setattr(settings, "environment", "production")
    payload = {"subscriber_id": "SUB-773019", "message": "I need an eSIM profile QR code for my new phone."}

    denied = test_client.post("/api/v1/chat/message", json=payload).json()
    assert "SM-DP+" not in denied["final_response"]

    token = security_service.generate_session_token("SUB-773019", "+1-415-555-0466", "authenticated_mfa")
    granted = test_client.post("/api/v1/chat/message", json={**payload, "session_token": token}).json()
    assert "SM-DP+" in granted["final_response"]
