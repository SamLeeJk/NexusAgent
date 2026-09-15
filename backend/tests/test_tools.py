import pytest
from data.orders import ORDERS
from data.refunds import REFUND_REQUEST
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from state import AgentState
from tools import check_refund_policy, create_refund_request


@pytest.mark.parametrize(
    "order_id,found,eligible",
    [
        (" O1002 ", True, True),
        ("O1001", True, False),
        ("missing", False, False),
    ],
)
def test_eligibility_uses_stored_order(order_id, found, eligible):
    result = check_refund_policy.invoke({"order_id": order_id})
    assert (result["found"], result["eligible"]) == (found, eligible)
    assert result["order_id"] == order_id.strip()


def test_model_cannot_override_order_status():
    result = check_refund_policy.invoke({"order_id": "O1001", "status": "processing"})
    assert result["eligible"] is False
    assert "status" not in check_refund_policy.args


def test_unknown_status_is_not_eligible(monkeypatch):
    monkeypatch.setitem(ORDERS, "O1003", {"order_id": "O1003", "status": "cancelled"})
    assert check_refund_policy.invoke({"order_id": "O1003"})["eligible"] is False


@pytest.mark.parametrize(
    "order_id,confirmed",
    [
        ("O1002", False),
        ("O1001", True),
        ("missing", True),
    ],
)
def test_refund_rejections_do_not_write(order_id, confirmed):
    result = create_refund_request.invoke(
        {"order_id": order_id, "refund_confirmed": confirmed}
    )
    assert result["success"] is False
    assert REFUND_REQUEST == {}


def test_duplicate_refund_is_rejected():
    args = {"order_id": " O1002 ", "refund_confirmed": True}
    assert create_refund_request.invoke(args)["success"] is True
    assert create_refund_request.invoke(args)["success"] is False
    assert list(REFUND_REQUEST) == ["O1002"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_tool_node_injects_confirmation_instead_of_trusting_model(confirmed):
    graph = StateGraph(AgentState)
    graph.add_node("tools", ToolNode([create_refund_request]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "refund-test",
                "name": "create_refund_request",
                "args": {"order_id": "O1002", "refund_confirmed": not confirmed},
            }
        ],
    )
    graph.compile().invoke({"messages": [message], "refund_confirmed": confirmed})
    assert bool(REFUND_REQUEST) is confirmed
