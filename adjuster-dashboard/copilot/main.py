from unittest import result

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent import build_graph, stream_chat
from langchain_core.messages import ToolMessage
import json
import traceback

app = FastAPI(title="Adjuster Copilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev only
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    thread_id: str

@app.get("/")
def hello():
    return {"message": "hello world", "service": "adjuster-copilot", "phase": 2}

@app.post("/chat")
async def chat(req: ChatRequest, authorization: str = Header(...)):
    token = authorization.removeprefix("Bearer ").strip()
    config = {"configurable": {"thread_id": req.thread_id}}

    async def event_stream():
        try:
            async with build_graph(adjuster_token=token) as graph:
                snapshot = await graph.aget_state(config)
                if snapshot.next:
                    pending = snapshot.values["messages"][-1].tool_calls[-1]
                    yield json.dumps({
                        "done": True,
                        "pending": True,
                        "action": {"tool": pending["name"], "args": pending["args"]},
                        "answer": "There's already an action awaiting approval or rejection. Please approve or reject it first.",
                    }) + "\n"
                    return

                async for piece in stream_chat(graph, req.message, config):
                    if piece.get("pending"):
                        piece["answer"] = (
                            f"I'm about to run {piece['action']['tool']} "
                            f"with {piece['action']['args']}. Approve or reject?"
                        )
                    yield json.dumps(piece) + "\n"
        except Exception as e:
            traceback.print_exc()
            yield json.dumps({"done": True, "pending": False, "error": str(e)}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")

@app.post("/approve")
async def approve(req: ChatRequest, authorization: str = Header(...)):
    token = authorization.removeprefix("Bearer ").strip()
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        async with build_graph(adjuster_token=token) as graph:
            snapshot = await graph.aget_state(config)
            if not snapshot.next:
                return {"pending": False, "answer": "No pending action to approve."}
            result = await graph.ainvoke(None, config)
        # return the final answer after approval
        return {"pending": False, "answer": result["messages"][-1].content}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reject")
async def reject(req: ChatRequest, authorization: str = Header(...)):
    token = authorization.removeprefix("Bearer ").strip()
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        async with build_graph(adjuster_token=token) as graph:
            snapshot = await graph.aget_state(config)
            if not snapshot.next:
                return {"pending": False, "answer": "No pending action to reject."}
            # skip the pending action and continue
            pending = snapshot.values["messages"][-1].tool_calls[-1]
            decline = ToolMessage(
                content=f"Declining {pending['name']} with {pending['args']}",
                tool_call_id=pending["id"],
            )
            await graph.aupdate_state(config, {"messages": [decline]}, as_node="action")
            result = await graph.ainvoke(None, config)
        # return the final answer after rejection
        return {"pending": False, "answer": result["messages"][-1].content}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))