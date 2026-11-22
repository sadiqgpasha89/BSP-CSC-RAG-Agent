"""KnowledgeAgent: grounded answers from the knowledge base, with source citations. No account access."""

from src.agents.base import AgentOutcome, BaseAgent
from src.agents.state import AgenticSessionState
from src.core.config import settings


class KnowledgeAgent(BaseAgent):
    NAME = "KnowledgeAgent"

    def run(self, state: AgenticSessionState, instruction: str) -> AgentOutcome:
        out = self.outcome()
        chunks = state.retrieved_chunks
        state.record_step(agent_name=self.NAME, action="RAG_KNOWLEDGE_CONSULTATION", thought=f"Consulted knowledge base ({len(chunks)} passages retrieved).")
        if not chunks or chunks[0].get("rerank_score", 0) < settings.rag_relevance_threshold:
            out.notices.append("I couldn't find verified information to answer that, so I won't guess.")
            return self.finish(state, out)
        top = chunks[0]
        out.messages.append(f"Based on our official documentation regarding '{top['title']}':\n{top['content']}")
        out.sources = [{"doc_id": c.get("doc_id") or c.get("source_id"), "title": c.get("title")} for c in chunks[:3]]
        state.blackboard["sources"] = out.sources
        return self.finish(state, out)


knowledge_agent = KnowledgeAgent()
