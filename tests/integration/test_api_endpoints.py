"""Integration Tests for all FastAPI REST Endpoints."""

def test_api_health(test_client):
    res = test_client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "HEALTHY"
    assert data["openstack_onprem_connected"] is True
    assert data["gcp_connected"] is True


def test_api_chat_message(test_client):
    payload = {
        "subscriber_id": "SUB-882194",
        "message": "I was charged twice for my international roaming data in Frankfurt.",
        "is_audio": False,
    }
    res = test_client.post("/api/v1/chat/message", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["intent_detected"] == "BILLING_DISPUTE"
    assert "credit of $30.00" in data["final_response"]
    assert data["guardrail_passed"] is True
    assert data["cloud_routing"]["provider"] in ["openstack_onprem", "gcp_vertex_ai"]


def test_api_network_topology_and_alarms(test_client):
    topo_res = test_client.get("/api/v1/network/topology")
    assert topo_res.status_code == 200
    assert "nodes" in topo_res.json()

    alarm_res = test_client.get("/api/v1/network/alarms")
    assert alarm_res.status_code == 200
    alarms = alarm_res.json()
    assert len(alarms) >= 1
    assert any(a["target_component"] == "TOWER-104" for a in alarms)


def test_api_otdr_diagnostic(test_client):
    payload = {
        "olt_port": "OLT-OPTICAL-01/PON-04",
        "subscriber_id": "SUB-449182",
    }
    res = test_client.post("/api/v1/network/otdr-test", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["line_status"] == "FIBER_CUT_DETECTED"
    assert data["optical_power_dbm"] <= -30.0


def test_api_mlops_drift_and_self_heal(test_client):
    drift_res = test_client.get("/api/v1/mlops/drift")
    assert drift_res.status_code == 200
    assert "psi_score" in drift_res.json()

    heal_res = test_client.post("/api/v1/mlops/self-heal")
    assert heal_res.status_code == 200
    assert heal_res.json()["status"] == "self_healing_executed"


def test_api_rag_search(test_client):
    payload = {
        "query": "Optical line OTDR testing procedure",
        "top_k": 3,
    }
    res = test_client.post("/api/v1/rag/search", json=payload)
    assert res.status_code == 200
    results = res.json()
    assert len(results) > 0
    assert results[0]["rrf_score"] > 0.0


def test_api_metrics_summary_and_arbitrage(test_client):
    metrics_res = test_client.get("/api/v1/metrics/summary")
    assert metrics_res.status_code == 200
    data = metrics_res.json()
    assert "business" in data
    assert "system" in data
    assert "rag" in data
    assert "agentic" in data
    assert "ml_nlp" in data
    assert "cost_arbitrage" in data

    arb_res = test_client.get("/api/v1/metrics/cost-arbitrage")
    assert arb_res.status_code == 200
    assert "cumulative_arbitrage_savings_usd" in arb_res.json()


def test_api_root_serves_html(test_client):
    res = test_client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "TELCO-AGENTIC-CORE" in res.text
