from pathlib import Path
from typing import Annotated

from data.orders import ORDERS
from data.refunds import REFUND_REQUEST
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState


@tool
def get_order_status(order_id: str) -> dict:
    """Look up the status of a customer order by its order ID (for example O1001)."""
    order = ORDERS.get(order_id.strip())
    if order is None:
        return {
            "found": False,
            "order_id": order_id,
            "error": f"Order {order_id} was not found.",
        }
    return {"found": True, **order}


#  检查退款政策
@tool
def check_refund_policy(status: str, order_id: str) -> dict:
    """Check whether an order is eligible for a refund based on its current status."""
    if status == "processing":
        return {
            "found": True,
            "eligible": True,
            "reason": f"Order with status '{status}' is eligible for a refund.",
        }
    if status == "shipped":
        return {
            "found": True,
            "eligible": False,
            "reason": f"Order with status '{status}' is not eligible for a refund.",
        }
    return {
        "found": True,
        "eligible": False,
        "order_id": order_id,
        "reason": f"Order {order_id} has unsupported status: {status}.",
    }


@tool
def create_refund_request(
    order_id: str, refund_confirmed: Annotated[bool, InjectedState("refund_confirmed")]
) -> dict:
    """Create a refund request for an order."""
    # Check if the order exists
    normalized_order_id = (
        order_id.strip()
    )  # Normalize the order ID to ensure consistent lookup
    order = ORDERS.get(normalized_order_id)
    is_requested = REFUND_REQUEST.get(normalized_order_id)
    print("REFUND_REQUEST before =", REFUND_REQUEST)
    print("object id =", id(REFUND_REQUEST))
    print("is_requested =", is_requested)
    if not refund_confirmed:
        return {
            "success": False,
            "order_id": normalized_order_id,
            "error": f"Refund request for order {normalized_order_id} was not confirmed by the user.",
        }
    if order is None:  # order does not exist
        return {
            "success": False,
            "order_id": normalized_order_id,
            "error": f"Order {normalized_order_id} was not found.",
        }
    if is_requested:  # Check if a refund request has already been made for this order
        return {
            "success": False,
            "order_id": normalized_order_id,
            "error": f"Refund request for order {normalized_order_id} has already been made.",
        }
    if order["status"] == "processing":  # Check if the order is eligible for a refund
        # add the refund request to the REFUND_REQUEST dictionary
        REFUND_REQUEST[normalized_order_id] = {
            "order_id": normalized_order_id,
            "status": "requested",
        }
        print("REFUND_REQUEST after =", REFUND_REQUEST)
        print("object id =", id(REFUND_REQUEST))
        return {
            "success": True,
            "order_id": normalized_order_id,
            "refund_status": "requested",
        }
    return {
        "success": False,
        "order_id": normalized_order_id,
        "error": f"Order {normalized_order_id} is not eligible for a refund.",
    }

POLICY_PATH=Path(__file__).parent / "data" / "knowledge"/ "refund_policy.md"
@tool
def search_refund_policy(query:str)->dict:
    """Search for refund policy information based on a query."""
    with open(POLICY_PATH, "r", encoding="utf-8") as file:
        policy_text = file.read()
    return{
        "query": query,
        "policy_text": policy_text,
    }