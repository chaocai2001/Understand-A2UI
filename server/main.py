"""FastAPI 入口：/chat 与 /action 两个 SSE 端点 + 静态前端托管。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .codex_bridge import CodexBridge

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

app = FastAPI(title="Codex × A2UI Demo")
bridge = CodexBridge(workspace=str(PROJECT_ROOT))


class ChatRequest(BaseModel):
    sessionId: str
    message: str


class ActionRequest(BaseModel):
    sessionId: str
    surfaceId: str
    actionName: str
    context: dict | None = None
    dataModel: dict | None = None


def sse_response(events: Iterator[dict]) -> StreamingResponse:
    def gen() -> Iterator[str]:
        for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    return sse_response(bridge.stream_chat(req.sessionId, req.message))


@app.post("/action")
def action(req: ActionRequest) -> StreamingResponse:
    return sse_response(
        bridge.stream_action(req.sessionId, req.surfaceId, req.actionName, req.context, req.dataModel)
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
