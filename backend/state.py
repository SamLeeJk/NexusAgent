from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Shared graph state for one /chat request.
    `messages` is the conversation the agent loop needs: the user question,
    the model's tool-call message, the tool result, and the final answer.
    LangGraph appends each node's new messages onto this list (`add_messages`).
    """

    refund_confirmed: bool
