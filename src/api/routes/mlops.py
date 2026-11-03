"""MLOps, Drift Intelligence & Model Registry API Router."""

from typing import Dict, Any, List
from fastapi import APIRouter
from src.mlops.drift_detector import drift_detector, DriftReport
from src.mlops.self_healing import self_healing_engine
from src.mlops.model_registry import model_registry
from src.llmops.evaluator import llmops_evaluator

router = APIRouter(prefix="/mlops", tags=["MLOps & LLMOps Lifecycle"])


@router.get("/drift", response_model=DriftReport)
def run_drift_check():
    """Execute multi-dimensional statistical drift inspection across data, concepts, tools, and RAG."""
    # Test sample simulating active production window
    sample_lengths = [14, 20, 29, 34, 18, 22, 28, 42, 16, 21]
    sample_tools = {"bss_adapter": 18, "oss_adapter": 45, "provisioning_adapter": 8}
    sample_rag = [0.89, 0.84, 0.86, 0.81, 0.88]

    report = drift_detector.detect_drift(
        recent_utterance_lengths=sample_lengths,
        recent_tool_counts=sample_tools,
        recent_rag_similarities=sample_rag,
    )
    return report


@router.post("/self-heal")
def trigger_self_healing():
    """Evaluate detected drift and trigger automated self-healing mitigation policies."""
    report = run_drift_check()
    actions = self_healing_engine.evaluate_and_heal(report)
    return {
        "status": "self_healing_executed",
        "actions_taken": [a.model_dump() for a in actions],
        "history_count": len(self_healing_engine.get_healing_history()),
    }


@router.get("/models")
def get_model_registry():
    """List deployed model artifacts, canary traffic splits, and benchmarks."""
    return model_registry.list_models()


@router.get("/eval-sample")
def run_eval_sample():
    """Run sample offline evaluation against Ragas-style metrics."""
    res = llmops_evaluator.evaluate_turn(
        query="What is the hotspot limit on my 2018 Freedom Unlimited plan?",
        response="Your 2018 Freedom Unlimited plan includes 10GB of high-speed hotspot.",
        retrieved_contexts=["Strict 10GB high-speed mobile hotspot limit per billing cycle on Freedom Unlimited 2018."],
        ground_truth_context="10GB high-speed mobile hotspot limit per billing cycle.",
        optimal_tool_count=1,
        actual_tool_count=1,
    )
    return res
