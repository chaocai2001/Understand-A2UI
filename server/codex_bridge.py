"""Codex 事件流 → A2UI/活动事件的桥接层。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator

from codex import Codex, Thread, ThreadStartOptions
from codex.protocol import types as t

from .a2ui_prompt import A2UI_DEVELOPER_INSTRUCTIONS

A2UI_KEYS = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")


def classify_line(line: str) -> dict | None:
    """把 agent 输出的一行归类为 A2UI 消息或普通聊天文本。"""
    line = line.strip()
    if not line or line.startswith("```"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return {"kind": "chat", "text": line}
    if isinstance(obj, dict) and any(k in obj for k in A2UI_KEYS):
        return {"kind": "a2ui", "message": obj}
    return {"kind": "chat", "text": line}


def _unwrap_item(item: object) -> object:
    return getattr(item, "root", item)


def describe_item(item: object, phase: str) -> dict | None:
    """把 Codex 的工具类 item 转成左侧活动流条目（仅用于展示，不进 A2UI surface）。"""
    item = _unwrap_item(item)
    if isinstance(item, t.CommandExecutionThreadItem):
        text = f"执行命令: {item.command}"
        if phase == "completed":
            text += f"  (exit={item.exitCode})"
        return {"kind": "activity", "icon": "cmd", "text": text}
    if isinstance(item, t.McpToolCallThreadItem):
        text = f"调用工具: {item.server}.{item.tool}"
        if phase == "completed":
            text += f"  ({item.status})" if item.status else ""
        return {"kind": "activity", "icon": "tool", "text": text}
    if isinstance(item, t.FileChangeThreadItem):
        paths = [getattr(c, "path", "?") for c in (item.changes or [])]
        return {"kind": "activity", "icon": "file", "text": f"文件变更: {', '.join(paths)}"}
    if isinstance(item, t.WebSearchThreadItem):
        query = getattr(item, "query", "")
        return {"kind": "activity", "icon": "web", "text": f"搜索网页: {query}"}
    if isinstance(item, t.ReasoningThreadItem) and phase == "completed":
        summary = item.summary or item.content or ""
        if isinstance(summary, list):
            summary = " ".join(str(s) for s in summary)
        if summary:
            return {"kind": "activity", "icon": "think", "text": f"推理: {str(summary)[:200]}"}
    if isinstance(item, t.PlanThreadItem) and phase == "completed":
        plan = getattr(item, "plan", "") or getattr(item, "text", "")
        if plan:
            return {"kind": "activity", "icon": "plan", "text": f"计划: {str(plan)[:200]}"}
    return None


class CodexBridge:
    """管理 session_id → Codex Thread 的映射，并把每次 turn 流式翻译成 SSE 事件。"""

    def __init__(self, workspace: str) -> None:
        self._codex = Codex()
        self._workspace = workspace
        self._threads: dict[str, Thread] = {}
        self._lock = threading.Lock()

    def _get_thread(self, session_id: str) -> Thread:
        with self._lock:
            thread = self._threads.get(session_id)
            if thread is None:
                thread = self._codex.start_thread(
                    ThreadStartOptions(
                        cwd=self._workspace,
                        developer_instructions=A2UI_DEVELOPER_INSTRUCTIONS,
                        approval_policy="never",
                        sandbox="read-only",
                        ephemeral=True,
                    )
                )
                self._threads[session_id] = thread
            return thread

    def stream_chat(self, session_id: str, message: str) -> Iterator[dict]:
        yield from self._stream_turn(session_id, message)

    def stream_action(self, session_id: str, surface_id: str, action_name: str,
                      context: dict | None, data_model: dict | None) -> Iterator[dict]:
        prompt = (
            f"[UI action] The user triggered action '{action_name}' on surface '{surface_id}'.\n"
            f"Resolved action context: {json.dumps(context or {}, ensure_ascii=False)}\n"
            f"Current surface data model: {json.dumps(data_model or {}, ensure_ascii=False)}\n"
            "Process this action and reply with A2UI JSONL messages updating the UI."
        )
        yield from self._stream_turn(session_id, prompt)

    def _stream_turn(self, session_id: str, prompt: str) -> Iterator[dict]:
        buf = ""
        try:
            thread = self._get_thread(session_id)
            stream = thread.run(prompt)
            for notif in stream:
                payload = getattr(notif, "params", notif)
                if isinstance(payload, t.AgentMessageDeltaNotification):
                    buf += payload.delta
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        event = classify_line(line)
                        if event:
                            yield event
                elif isinstance(payload, t.ItemStartedNotification):
                    event = describe_item(payload.item, "started")
                    if event:
                        yield event
                elif isinstance(payload, t.ItemCompletedNotification):
                    event = describe_item(payload.item, "completed")
                    if event:
                        yield event
                elif isinstance(payload, t.ErrorNotification):
                    yield {"kind": "error", "message": str(payload.error)}
        except Exception as exc:  # codex 进程失败、认证失败等
            yield {"kind": "error", "message": f"{type(exc).__name__}: {exc}"}
            return
        tail = classify_line(buf)
        if tail:
            yield tail
        yield {"kind": "done"}
