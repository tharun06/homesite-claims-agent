import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state import ClaimState

from nodes.policy_gate import policy_gate
from nodes.video_processor import video_processor
from nodes.photo_processor import photo_processor
from nodes.authenticity_checker import authenticity_checker
from nodes.damage_assessor import damage_assessor
from nodes.retrieval_engine import retrieval_engine
from nodes.rules_evaluator import rules_evaluator
from nodes.decision import decision_node

claim_adjuster     = rules_evaluator
response_formatter = decision_node


# ── END NODES ──────────────────────────────────────────────────────────────────

def end_pass(state: ClaimState) -> dict:
    print("\n✅ Claim APPROVED")
    return {}

def end_fail(state: ClaimState) -> dict:
    print("\n❌ Claim DENIED")
    return {}

def end_fraud(state: ClaimState) -> dict:
    print("\n🚨 Claim FLAGGED for SIU")
    return {}

def end_human(state: ClaimState) -> dict:
    print("\n👤 Routing to HUMAN ADJUSTER")
    return {}

def request_more_info(state: ClaimState) -> dict:
    current = state.get("info_requests", 0)
    print(f"\n📋 Requesting more info (attempt {current + 1})")
    return {"info_requests": current + 1}


# ── ROUTING ────────────────────────────────────────────────────────────────────

def route_after_gate(state: ClaimState) -> str:
    """Skip everything if policy is invalid."""
    if state.get("decision") == "fail":
        return "end_fail"
    if state.get("media_type") == "photo":
        return "photo_processor"
    return "video_processor"


def route_after_fraud(state: ClaimState) -> str:
    """Skip LLM calls if fraud is HIGH."""
    if state.get("risk_level") == "HIGH":
        return "end_fraud"
    return "damage_assessor"


def route_decision(state: ClaimState) -> str:
    decision = state.get("decision", "need_more_info")
    info_requests = state.get("info_requests", 0)

    if decision == "pass":
        return "end_pass"
    elif decision == "fail":
        return "end_fail"
    elif decision == "flag_fraud":
        return "end_fraud"
    elif decision == "need_more_info":
        return "request_more_info" if info_requests < 2 else "end_human"
    return "end_human"


# ── GRAPH ──────────────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(ClaimState)

    # nodes
    graph.add_node("policy_gate",          policy_gate)
    graph.add_node("video_processor",      video_processor)
    graph.add_node("photo_processor",      photo_processor)
    graph.add_node("authenticity_checker", authenticity_checker)
    graph.add_node("damage_assessor",      damage_assessor)
    graph.add_node("retrieval_engine",     retrieval_engine)
    graph.add_node("claim_adjuster",     claim_adjuster)
    graph.add_node("response_formatter", response_formatter)
    graph.add_node("end_pass",             end_pass)
    graph.add_node("end_fail",             end_fail)
    graph.add_node("end_fraud",            end_fraud)
    graph.add_node("end_human",            end_human)
    graph.add_node("request_more_info",    request_more_info)

    graph.set_entry_point("policy_gate")

    # policy gate → branch on media type (or fail immediately)
    graph.add_conditional_edges(
        "policy_gate",
        route_after_gate,
        {
            "end_fail":        "end_fail",
            "video_processor": "video_processor",
            "photo_processor": "photo_processor",
        }
    )

    # both media paths converge at authenticity_checker
    graph.add_edge("video_processor", "authenticity_checker")
    graph.add_edge("photo_processor", "authenticity_checker")

    # fraud gate → skip LLM if HIGH
    graph.add_conditional_edges(
        "authenticity_checker",
        route_after_fraud,
        {"end_fraud": "end_fraud", "damage_assessor": "damage_assessor"}
    )

    # LLM pipeline
    graph.add_edge("damage_assessor",  "retrieval_engine")
    graph.add_edge("retrieval_engine", "claim_adjuster")
    graph.add_edge("claim_adjuster",   "response_formatter")

    # final routing
    graph.add_conditional_edges(
        "response_formatter",
        route_decision,
        {
            "end_pass":          "end_pass",
            "end_fail":          "end_fail",
            "end_fraud":         "end_fraud",
            "end_human":         "end_human",
            "request_more_info": "request_more_info",
        }
    )

    # loop back — only re-runs RAG + rules, not video/photo processing
    graph.add_edge("request_more_info", "retrieval_engine")  # re-runs RAG → adjuster → formatter

    graph.add_edge("end_pass",  END)
    graph.add_edge("end_fail",  END)
    graph.add_edge("end_fraud", END)
    graph.add_edge("end_human", END)

    conn = sqlite3.connect("claims_state.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["request_more_info"]
    )


claims_app = build_graph()
