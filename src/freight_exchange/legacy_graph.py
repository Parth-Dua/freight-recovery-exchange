"""Run the existing LangGraph booking graph in-process for text the cards don't cover (text fallback).

Same graph the old Agentverse LangGraph agent served; buyer and session come from the gateway binding.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

_graph = None


def _compiled():
    global _graph
    if _graph is None:
        import agent as legacy  # src/agent.py

        _graph = legacy._builder.compile(checkpointer=InMemorySaver())
    return _graph


async def run_legacy_graph(sender: str, session: str, text: str) -> str:
    state = await _compiled().ainvoke(
        {"messages": [{"role": "human", "content": text}]},
        config={"configurable": {"thread_id": session},
                "metadata": {"agentverse_sender": sender, "agentverse_session": session}})
    return state["messages"][-1].content
