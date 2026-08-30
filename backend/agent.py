import os
from pathlib import Path

from data.confirm import CONFIRM_WORDS
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from state import AgentState
from tools import (
    check_refund_policy,
    create_refund_request,
    get_order_status,
    search_refund_policy,
)

_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")

TOOLS = [
    get_order_status,
    check_refund_policy,
    create_refund_request,
    search_refund_policy,
]
RECURSION_LIMIT = 10
SYSTEM_PROMPT = (
    "You are a customer-support agent. "
    "When the user asks about an order, call get_order_status with the order ID. "
    "Call check_refund_policy with the order ID to check if the order is eligible for a refund. "
    "Then answer in clear natural language using the tool result. "
    "If the order is not found, say so."
)
CONFIRMATION_PROMPT = (
    "Determine whether the user's latest message explicitly authorizes "
    "creating or proceeding with a refund request. "
    "Questions about refund eligibility or refund policy are NOT confirmation. "
    "Only explicit requests to proceed, create, submit, or confirm the refund count as confirmation."
)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        extra="ignore",
    )
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"


settings = Settings()

class ConfirmationState(BaseModel):
    confirmed: bool

def openai_api_key_configured() -> bool:
    return bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY"))


def build_graph():
    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key or None,
    ).bind_tools(TOOLS)
    confirmation_llm=ChatOpenAI( #creae a separate LLM instance for confirmation detection
        model=settings.openai_model,
        api_key=settings.openai_api_key or None,
    ).with_structured_output(ConfirmationState)
    def call_model(state: AgentState) -> dict:
        response = llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [response]}

    def detect_confirmation(state: AgentState) -> dict:
        # Check if the user has confirmed the refund request
        message = state["messages"][-1]#only check the last message for confirmation
        if not isinstance(message, HumanMessage):
            return {"refund_confirmed": False}
        result = confirmation_llm.invoke(
            [
                SystemMessage(content=CONFIRMATION_PROMPT),
                message
            ]
        )
        confirmed = result.confirmed
        print(f"[Confirmation] {message.content} for message: {result.confirmed}")
        return {"refund_confirmed": confirmed}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("confirmation", detect_confirmation)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_edge(START, "confirmation")
    graph.add_edge("confirmation", "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


app_graph = build_graph()
