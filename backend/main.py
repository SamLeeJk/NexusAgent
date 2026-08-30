from agent import RECURSION_LIMIT, app_graph, openai_api_key_configured
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

app = FastAPI(title="NexusAgent")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    response: str


def _final_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content and not message.tool_calls:
            content = message.content
            return content if isinstance(content, str) else str(content)
    last = messages[-1]
    return last.content if hasattr(last, "content") else str(last)

#for debugging 
def _debug_messages(messages: list) -> str:
    debug_info = []
    for message in messages:
        if isinstance(message, AIMessage):
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    debug_info.append(f"AIMessage Tool Calls:\n tool: {tool_call['name']},\n args: {tool_call['args']}\n")
            else:
                debug_info.append(f"AIMessage: {message.content}\n")
        elif isinstance(message, HumanMessage):
            debug_info.append(f"HumanMessage: {message.content}\n")
        elif isinstance(message,ToolMessage):
            debug_info.append(f"ToolMessage: {message.name},\nresult: {message.content}\n")
    print("\n".join(debug_info))
    return "\n".join(debug_info)

@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    if not openai_api_key_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY is not set. Add it to backend/.env and restart the server."
            ),
        )
    result = app_graph.invoke(
        {
            "messages": [HumanMessage(content=payload.message)],
            "refund_confirmed": False,
        },
        {"recursion_limit": RECURSION_LIMIT},
    )
    return ChatResponse(response=_debug_messages(result["messages"]))
