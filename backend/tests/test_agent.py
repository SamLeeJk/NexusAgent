import importlib
from types import SimpleNamespace
from unittest.mock import patch

import agent
import pytest
from data.refunds import REFUND_REQUEST
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_import_does_not_construct_model_clients():
    with patch(
        "langchain_openai.ChatOpenAI",
        side_effect=AssertionError("Eager client construction"),
    ):
        importlib.reload(agent)
    # Restore the imported client symbol after the isolated import assertion.
    importlib.reload(agent)


@pytest.mark.parametrize("confirmed", [False, True])
def test_real_graph_confirmation_tool_and_final_answer(monkeypatch, confirmed):
    class Model:
        def invoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Request processed.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "refund",
                        "name": "create_refund_request",
                        "args": {"order_id": "O1002"},
                    }
                ],
            )

        def bind_tools(self, tools):
            return self

        def with_structured_output(self, schema):
            return SimpleNamespace(invoke=lambda messages: schema(confirmed=confirmed))

    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: Model())
    graph = agent.build_graph()
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Please process order O1002")],
            "refund_confirmed": False,
        },
        {"recursion_limit": agent.RECURSION_LIMIT},
    )
    assert result["messages"][-1].content == "Request processed."
    assert any(isinstance(message, ToolMessage) for message in result["messages"])
    assert bool(REFUND_REQUEST) is confirmed
