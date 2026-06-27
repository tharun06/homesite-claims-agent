from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agent import build_graph
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
    try:
        async with build_graph(adjuster_token=token) as graph:
            result = await graph.ainvoke(
                {"messages": [("user", req.message)]},
                config=config,
            )
        for m in result["messages"]:
            print(f"[{type(m).__name__}] {str(m.content)[:200]}")
        return {"answer": result["messages"][-1].content, "tools": []}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
