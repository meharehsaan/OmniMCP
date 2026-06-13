import io
import os
import sys
import json
import time
import hmac
import asyncio
import hashlib
import secrets
import zipfile
import uvicorn
from typing import Any
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from collections import defaultdict, deque

from google import genai
from mcp import ClientSession
from google.genai import types
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from mcp.client.stdio import StdioServerParameters, stdio_client
from fastapi.responses import FileResponse, StreamingResponse
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status

from config import DevConfig
from utils import setup_logger

logging = setup_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
COOKIE_NAME = "omnimcp_session"
AUTH_FILE = BASE_DIR / ".omnimcp_auth.json"
SESSION_COOKIE_AGE = DevConfig.SESSION_TTL_SECONDS
LOGIN_WINDOW_SECONDS = 5 * 60
MAX_LOGIN_ATTEMPTS = 10
MAX_EXPORT_CHARS = 10_000_000
MAX_HTTP_REQUEST_BYTES = 12_000_000
TOOL_APPROVAL_TIMEOUT_SECONDS = 90
APPROVAL_REQUIRED_TOOLS = frozenset({"write_file", "delete_path", "clear_todos"})
SYSTEM_INSTRUCTION = ("You are a helpful local AI assistant with access to tools. "
                      "Use tools only when needed, explain destructive actions before taking them, "
                      "and format responses using Markdown.")


class LoginRequest(BaseModel):
	username: str = Field(min_length=1, max_length=128)
	password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(BaseModel):
	current_password: str = Field(min_length=1, max_length=1024)
	new_password: str = Field(min_length=10, max_length=1024)


class ExportMessage(BaseModel):
	role: str = Field(min_length=1, max_length=32)
	content: str = Field(max_length=100_000)
	timestamp: str | None = Field(default=None, max_length=64)
	tool_name: str | None = Field(default=None, max_length=128)


class ConversationExportRequest(BaseModel):
	title: str = Field(default="OmniMCP Conversation", max_length=256)
	messages: list[ExportMessage] = Field(max_length=1000)


def _password_hash(password: str, salt: bytes) -> str:
	return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000, ).hex()


def _load_stored_password() -> tuple[bytes, str] | None:
	try:
		AUTH_FILE.chmod(0o600)
		payload = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
		return bytes.fromhex(payload["salt"]), str(payload["password_hash"])
	except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
		return None


def verify_password(password: str) -> bool:
	stored = _load_stored_password()
	if stored is None:
		return hmac.compare_digest(password, DevConfig.PASSWORD)
	salt, expected_hash = stored
	return hmac.compare_digest(_password_hash(password, salt), expected_hash)


def save_password(password: str) -> None:
	salt = secrets.token_bytes(32)
	payload = {"salt": salt.hex(), "password_hash": _password_hash(password, salt), "updated_at": datetime.now(timezone.utc).isoformat(), }
	temporary_file = AUTH_FILE.with_suffix(".tmp")
	descriptor = os.open(temporary_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, )
	with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2)
		handle.flush()
		os.fsync(handle.fileno())
	temporary_file.replace(AUTH_FILE)
	AUTH_FILE.chmod(0o600)


def mcp_to_genai_tool(mcp_tool: Any) -> types.FunctionDeclaration:
	"""Convert an MCP tool definition to a Gemini function declaration."""
	return types.FunctionDeclaration(name=mcp_tool.name, description=mcp_tool.description, parameters=mcp_tool.inputSchema, )


def tool_input_for_client(arguments: dict[str, Any]) -> dict[str, Any]:
	"""Limit sensitive or oversized tool arguments sent to the browser."""
	visible = dict(arguments)
	content = visible.get("content")
	if isinstance(content, str) and len(content) > 2000:
		visible["content"] = (f"[{len(content.encode('utf-8'))} bytes; preview omitted from activity log]")
	return visible


def sign_session_id(session_id: str) -> str:
	signature = hmac.new(DevConfig.AUTH_SECRET.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256, ).hexdigest()
	return f"{session_id}.{signature}"


def verify_session_cookie(cookie_value: str | None) -> str | None:
	if not cookie_value or "." not in cookie_value:
		return None

	session_id, signature = cookie_value.rsplit(".", 1)
	expected = hmac.new(DevConfig.AUTH_SECRET.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256, ).hexdigest()
	return session_id if hmac.compare_digest(signature, expected) else None


def request_is_same_origin(request: Request) -> bool:
	origin = request.headers.get("origin")
	if not origin:
		return True
	parsed = urlparse(origin)
	return parsed.netloc == request.headers.get("host") and parsed.scheme in {"http", "https"}


def websocket_is_same_origin(websocket: WebSocket) -> bool:
	origin = websocket.headers.get("origin")
	if not origin:
		return True
	parsed = urlparse(origin)
	return parsed.netloc == websocket.headers.get("host") and parsed.scheme in {"http", "https"}


def require_same_origin(request: Request) -> None:
	if not request_is_same_origin(request):
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-origin request rejected")


@dataclass
class OmniSession:
	session_id: str
	gemini_history: list[types.Content] = field(default_factory=list)
	client_genai: Any | None = None
	mcp_session: ClientSession | None = None
	tool_config: types.Tool | None = None
	shutdown_event: asyncio.Event | None = None
	runner_task: asyncio.Task[None] | None = None
	initialized: bool = False
	closed: bool = False
	last_activity: float = field(default_factory=time.monotonic)
	init_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
	chat_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

	def touch(self) -> None:
		self.last_activity = time.monotonic()

	def tools_payload(self) -> list[dict[str, Any]]:
		if not self.tool_config or not self.tool_config.function_declarations:
			return []
		return [{"name": declaration.name, "description": declaration.description} for declaration in self.tool_config.function_declarations]

	def trim_history(self) -> None:
		overflow = len(self.gemini_history) - DevConfig.MAX_HISTORY_MESSAGES
		if overflow > 0:
			del self.gemini_history[:overflow]
			while self.gemini_history and self.gemini_history[0].role != "user":
				del self.gemini_history[0]

	async def initialize(self) -> list[dict[str, Any]]:
		async with self.init_lock:
			if self.closed:
				raise RuntimeError("Session has been closed.")
			if self.initialized:
				self.touch()
				return self.tools_payload()

			self.client_genai = genai.Client(api_key=DevConfig.GEMINI_API_KEY, http_options={"api_version": "v1alpha"}, )
			server_params = StdioServerParameters(command=sys.executable, args=[str(BASE_DIR / "mcp_tools.py")], cwd=str(BASE_DIR), )
			self.shutdown_event = asyncio.Event()
			init_event = asyncio.Event()
			session_state: dict[str, Any] = {}

			async def mcp_runner() -> None:
				try:
					async with stdio_client(server_params) as (read, write):
						async with ClientSession(read, write) as session:
							await session.initialize()
							session_state["session"] = session
							init_event.set()
							await self.shutdown_event.wait()
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					session_state["error"] = exc
					logging.exception("MCP server failed for session %s", self.session_id)
					init_event.set()

			self.runner_task = asyncio.create_task(mcp_runner(), name=f"omnimcp-mcp-{self.session_id[:8]}", )

			try:
				await asyncio.wait_for(init_event.wait(), timeout=20)
				self.mcp_session = session_state.get("session")
				if not self.mcp_session:
					error = session_state.get("error")
					raise RuntimeError("Failed to initialize the local tool server.") from error

				result = await self.mcp_session.list_tools()
				declarations = [mcp_to_genai_tool(tool) for tool in result.tools]
				self.tool_config = types.Tool(function_declarations=declarations)
				self.initialized = True
				self.touch()
				return self.tools_payload()
			except Exception:
				await self._stop_runner()
				raise

	async def ask(self, content: str, websocket: WebSocket) -> None:
		await self.initialize()
		async with self.chat_lock:
			self.touch()
			self.gemini_history.append(types.Content(role="user", parts=[types.Part.from_text(text=content)]))
			self.trim_history()

			for _ in range(DevConfig.MAX_TOOL_ROUNDS):
				response = await self.client_genai.aio.models.generate_content(model=DevConfig.GEMINI_MODEL_ID, contents=self.gemini_history, config=types.GenerateContentConfig(tools=[self.tool_config], system_instruction=SYSTEM_INSTRUCTION, ), )
				if not response.candidates or not response.candidates[0].content:
					raise RuntimeError("Gemini returned an empty response.")

				model_content = response.candidates[0].content
				self.gemini_history.append(model_content)
				self.trim_history()
				function_calls = [part.function_call for part in (model_content.parts or []) if part.function_call]

				if not function_calls:
					await websocket.send_json({"type": "assistant", "content": response.text or "No response generated."})
					self.touch()
					return

				async def run_tool(function_call: Any) -> types.Part:
					args = function_call.args
					args_dict = args if isinstance(args, dict) else dict(args) if args else {}
					visible_args = tool_input_for_client(args_dict)
					tool_id = secrets.token_urlsafe(8)
					if function_call.name in APPROVAL_REQUIRED_TOOLS:
						await websocket.send_json({"type": "approval_required", "id": tool_id, "name": function_call.name, "input": visible_args, })
						try:
							approval = await asyncio.wait_for(websocket.receive_json(), timeout=TOOL_APPROVAL_TIMEOUT_SECONDS, )
							approved = (approval.get("type") == "tool_approval" and approval.get("id") == tool_id and approval.get("approved") is True)
						except asyncio.TimeoutError:
							approved = False

						if not approved:
							output = "Tool execution denied by the user."
							await websocket.send_json({"type": "tool_start", "id": tool_id, "name": function_call.name, "input": visible_args, })
							await websocket.send_json({"type": "tool_end", "id": tool_id, "name": function_call.name, "input": visible_args, "output": output, })
							return types.Part.from_function_response(name=function_call.name, response={"result": output}, )

					await websocket.send_json({"type": "tool_start", "id": tool_id, "name": function_call.name, "input": visible_args, })
					try:
						result = await self.mcp_session.call_tool(function_call.name, args_dict)
						output = str(result.content)
					except Exception as exc:
						logging.exception("Tool %s failed", function_call.name)
						output = f"Tool failed: {type(exc).__name__}"

					if len(output) > DevConfig.MAX_TOOL_OUTPUT_CHARS:
						output = (output[: DevConfig.MAX_TOOL_OUTPUT_CHARS] + "\n\n[Output truncated by OmniMCP]")
					await websocket.send_json({"type": "tool_end", "id": tool_id, "name": function_call.name, "input": visible_args, "output": output, })
					return types.Part.from_function_response(name=function_call.name, response={"result": output}, )

				# Serialize tool calls so approval responses cannot race on one socket.
				tool_responses = [await run_tool(function_call) for function_call in function_calls]
				self.gemini_history.append(types.Content(role="tool", parts=tool_responses))
				self.trim_history()

			raise RuntimeError("Gemini exceeded the maximum number of tool-call rounds.")

	async def reset(self) -> None:
		async with self.chat_lock:
			self.gemini_history.clear()
			self.touch()

	async def _stop_runner(self) -> None:
		if self.shutdown_event and not self.shutdown_event.is_set():
			self.shutdown_event.set()
		if self.runner_task and not self.runner_task.done():
			try:
				await asyncio.wait_for(self.runner_task, timeout=5)
			except asyncio.TimeoutError:
				self.runner_task.cancel()
				await asyncio.gather(self.runner_task, return_exceptions=True)
		self.runner_task = None
		self.mcp_session = None
		self.initialized = False

	async def close(self) -> None:
		async with self.init_lock:
			if self.closed:
				return
			self.closed = True
			await self._stop_runner()
			self.gemini_history.clear()
			self.client_genai = None
			self.tool_config = None


sessions: dict[str, OmniSession] = {}
sessions_lock = asyncio.Lock()
login_attempts: dict[str, deque[float]] = defaultdict(deque)


async def get_session(session_id: str) -> OmniSession | None:
	async with sessions_lock:
		session = sessions.get(session_id)
		if session:
			session.touch()
		return session


async def create_session() -> OmniSession:
	sessions_to_close: list[OmniSession] = []
	async with sessions_lock:
		while len(sessions) >= DevConfig.MAX_SESSIONS:
			oldest_id = min(sessions, key=lambda key: sessions[key].last_activity)
			sessions_to_close.append(sessions.pop(oldest_id))

		session_id = secrets.token_urlsafe(32)
		session = OmniSession(session_id=session_id)
		sessions[session_id] = session

	await asyncio.gather(*(old_session.close() for old_session in sessions_to_close), return_exceptions=True, )
	return session


async def remove_session(session_id: str) -> None:
	async with sessions_lock:
		session = sessions.pop(session_id, None)
	if session:
		await session.close()


async def remove_other_sessions(keep_session_id: str) -> None:
	async with sessions_lock:
		old_sessions = [sessions.pop(session_id) for session_id in list(sessions) if session_id != keep_session_id]
	await asyncio.gather(*(session.close() for session in old_sessions), return_exceptions=True, )


async def cleanup_expired_sessions() -> None:
	while True:
		await asyncio.sleep(60)
		cutoff = time.monotonic() - DevConfig.SESSION_TTL_SECONDS
		async with sessions_lock:
			expired = [sessions.pop(session_id) for session_id, session in list(sessions.items()) if session.last_activity < cutoff and not session.chat_lock.locked() and not session.init_lock.locked()]
		await asyncio.gather(*(session.close() for session in expired), return_exceptions=True)


def login_allowed(client_key: str) -> bool:
	now = time.monotonic()
	attempts = login_attempts[client_key]
	while attempts and attempts[0] < now - LOGIN_WINDOW_SECONDS:
		attempts.popleft()
	if len(attempts) >= MAX_LOGIN_ATTEMPTS:
		return False
	attempts.append(now)
	return True


@asynccontextmanager
async def lifespan(_: FastAPI):
	cleanup_task = asyncio.create_task(cleanup_expired_sessions(), name="omnimcp-session-cleanup")
	try:
		yield
	finally:
		cleanup_task.cancel()
		await asyncio.gather(cleanup_task, return_exceptions=True)
		async with sessions_lock:
			active_sessions = list(sessions.values())
			sessions.clear()
		await asyncio.gather(*(session.close() for session in active_sessions), return_exceptions=True)


app = FastAPI(title="OmniMCP", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, )
app.mount("/public", StaticFiles(directory=PUBLIC_DIR), name="public")


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
	content_length = request.headers.get("content-length")
	if content_length:
		try:
			if int(content_length) > MAX_HTTP_REQUEST_BYTES:
				return Response(content='{"detail":"Request body is too large"}', status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, media_type="application/json", )
		except ValueError:
			return Response(content='{"detail":"Invalid Content-Length header"}', status_code=status.HTTP_400_BAD_REQUEST, media_type="application/json", )
	response = await call_next(request)
	response.headers["X-Content-Type-Options"] = "nosniff"
	response.headers["X-Frame-Options"] = "DENY"
	response.headers["Referrer-Policy"] = "no-referrer"
	response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
	response.headers["Content-Security-Policy"] = ("default-src 'self'; "
	                                               "script-src 'self'; "
	                                               "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
	                                               "font-src 'self' https://fonts.gstatic.com; "
	                                               "img-src 'self' data:; "
	                                               "connect-src 'self' ws: wss:; "
	                                               "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
	if request.url.path.startswith("/api/"):
		response.headers["Cache-Control"] = "no-store"
	if DevConfig.COOKIE_SECURE:
		response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
	return response


@app.get("/")
async def index() -> FileResponse:
	return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/session")
async def session_status(request: Request) -> dict[str, Any]:
	session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	session = await get_session(session_id) if session_id else None
	if not session:
		return {"authenticated": False}
	return {"authenticated": True, "username": DevConfig.USER, "initialized": session.initialized, "tools": session.tools_payload(), }


@app.post("/api/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
	require_same_origin(request)
	client_key = request.client.host if request.client else "unknown"
	if not login_allowed(client_key):
		raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts. Try again later.", )

	username_matches = hmac.compare_digest(payload.username.strip(), DevConfig.USER)
	password_matches = verify_password(payload.password)
	if not username_matches or not password_matches:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password", )

	login_attempts.pop(client_key, None)
	previous_session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	if previous_session_id:
		await remove_session(previous_session_id)
	session = await create_session()
	response.set_cookie(COOKIE_NAME, sign_session_id(session.session_id), httponly=True, samesite="strict", secure=DevConfig.COOKIE_SECURE, max_age=SESSION_COOKIE_AGE, path="/", )
	return {"authenticated": True, "username": DevConfig.USER}


@app.post("/api/settings/password")
async def change_password(payload: PasswordChangeRequest, request: Request, ) -> dict[str, bool]:
	require_same_origin(request)
	session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	session = await get_session(session_id) if session_id else None
	if not session:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required", )
	if not verify_password(payload.current_password):
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect", )
	if len(payload.new_password) < 10:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must contain at least 10 characters", )
	if hmac.compare_digest(payload.current_password, payload.new_password):
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be different", )

	save_password(payload.new_password)
	await remove_other_sessions(session.session_id)
	return {"ok": True}


@app.post("/api/conversation/export")
async def export_conversation(payload: ConversationExportRequest, request: Request, ) -> StreamingResponse:
	require_same_origin(request)
	session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	session = await get_session(session_id) if session_id else None
	if not session:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required", )
	if len(payload.messages) > 1000:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Conversation is too large to export", )
	export_size = len(payload.title) + sum(len(message.content) + len(message.role) + len(message.tool_name or "") for message in payload.messages)
	if export_size > MAX_EXPORT_CHARS:
		raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Conversation export exceeds the size limit", )

	exported_at = datetime.now(timezone.utc)
	markdown_lines = [f"# {payload.title.strip() or 'OmniMCP Conversation'}", "", f"Exported: {exported_at.isoformat()}", "", ]
	for message in payload.messages:
		label = message.tool_name or message.role.title()
		markdown_lines.extend([f"## {label}", "", message.content, ""])

	json_payload = {"title": payload.title, "exported_at": exported_at.isoformat(), "messages": [message.model_dump() for message in payload.messages], }
	archive = io.BytesIO()
	with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
		bundle.writestr("conversation.md", "\n".join(markdown_lines))
		bundle.writestr("conversation.json", json.dumps(json_payload, indent=2, ensure_ascii=False), )
	archive.seek(0)

	filename = f"omnimcp-conversation-{exported_at.strftime('%Y%m%d-%H%M%S')}.zip"
	return StreamingResponse(archive, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'}, )


@app.post("/api/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
	require_same_origin(request)
	session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	if session_id:
		await remove_session(session_id)
	response.delete_cookie(COOKIE_NAME, path="/", secure=DevConfig.COOKIE_SECURE, httponly=True, samesite="strict", )
	return {"ok": True}


@app.post("/api/chat/reset")
async def reset_chat(request: Request) -> dict[str, bool]:
	require_same_origin(request)
	session_id = verify_session_cookie(request.cookies.get(COOKIE_NAME))
	session = await get_session(session_id) if session_id else None
	if not session:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required", )
	await session.reset()
	return {"ok": True}


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
	if not websocket_is_same_origin(websocket):
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return

	session_id = verify_session_cookie(websocket.cookies.get(COOKIE_NAME))
	session = await get_session(session_id) if session_id else None
	if not session:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return

	await websocket.accept()
	try:
		await websocket.send_json({"type": "status", "content": "Starting local tools and Gemini..."})
		tools = await session.initialize()
		await websocket.send_json({"type": "ready", "content": "OmniMCP is ready.", "tools": tools, })

		while True:
			payload = await websocket.receive_json()
			if payload.get("type") != "message":
				continue

			content = str(payload.get("content", "")).strip()
			if not content:
				continue
			if len(content) > 20_000:
				await websocket.send_json({"type": "error", "content": "Message is too long."})
				continue

			await websocket.send_json({"type": "status", "content": "Thinking..."})
			try:
				await session.ask(content, websocket)
				await websocket.send_json({"type": "done"})
			except WebSocketDisconnect:
				raise
			except Exception:
				logging.exception("Chat request failed for session %s", session.session_id)
				await websocket.send_json({"type": "error", "content": "The request failed. Check the server log for details.", })
	except WebSocketDisconnect:
		return
	except Exception:
		logging.exception("WebSocket failed for session %s", session.session_id)
		try:
			await websocket.send_json({"type": "error", "content": "The connection failed. Check the server log for details.", })
		except Exception:
			pass


if __name__ == "__main__":
	uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
