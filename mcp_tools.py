import os
import ssl
import socket
import shutil
import psutil
import urllib3
import fnmatch
import platform
import ipaddress
from typing import Any
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from urllib.parse import urljoin, urlparse

from config import DevConfig
from utils import setup_logger

logging = setup_logger(__name__)
mcp = FastMCP("OmniMCP Local Tool Server")

WORKSPACE = DevConfig.FILES_PATH
TODO_FILE = WORKSPACE / "todo.txt"
MAX_FILE_SIZE = DevConfig.MAX_FILE_SIZE_BYTES
MAX_WEB_RESPONSE_SIZE = 2 * 1024 * 1024
MAX_SEARCH_RESULTS = 20
MAX_REDIRECTS = 5

WORKSPACE.mkdir(parents=True, exist_ok=True)


def resolve_workspace_path(path: str, *, allow_root: bool = True) -> Path:
	"""Resolve a user path and ensure it stays inside the configured workspace."""
	raw_path = Path(path).expanduser()
	candidate = raw_path if raw_path.is_absolute() else WORKSPACE / raw_path
	resolved = candidate.resolve()

	if resolved != WORKSPACE and WORKSPACE not in resolved.parents:
		raise ValueError(f"Path must stay inside the workspace: {WORKSPACE}")
	if not allow_root and resolved == WORKSPACE:
		raise ValueError("This operation is not allowed on the workspace root.")
	return resolved


def resolve_workspace_entry(path: str, *, allow_root: bool = True) -> Path:
	"""Resolve a path's parent while preserving the final entry, including symlinks."""
	raw_path = Path(path).expanduser()
	candidate = raw_path if raw_path.is_absolute() else WORKSPACE / raw_path
	parent = candidate.parent.resolve()
	if parent != WORKSPACE and WORKSPACE not in parent.parents:
		raise ValueError(f"Path must stay inside the workspace: {WORKSPACE}")

	entry = parent / candidate.name
	if not allow_root and entry == WORKSPACE:
		raise ValueError("This operation is not allowed on the workspace root.")
	return entry


def display_path(path: Path) -> str:
	if path == WORKSPACE:
		return "."
	return str(path.relative_to(WORKSPACE))


def validate_public_url(url: str) -> tuple[str, list[str]]:
	parsed = urlparse(url)
	if parsed.scheme not in {"http", "https"}:
		raise ValueError("Only HTTP and HTTPS URLs are supported.")
	if not parsed.hostname:
		raise ValueError("The URL must include a hostname.")
	if parsed.username or parsed.password:
		raise ValueError("URLs containing credentials are not allowed.")

	try:
		addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM, )))
	except socket.gaierror as exc:
		raise ValueError(f"Unable to resolve hostname: {parsed.hostname}") from exc

	for address in addresses:
		ip = ipaddress.ip_address(address)
		if not ip.is_global:
			raise ValueError("Local, private, reserved, and loopback addresses are not allowed.")
	return url, addresses


def request_public_url(url: str) -> tuple[Any, Any]:
	"""Open a URL through a validated and pinned public IP address."""
	validated_url, addresses = validate_public_url(url)
	parsed = urlparse(validated_url)
	port = parsed.port or (443 if parsed.scheme == "https" else 80)
	default_port = 443 if parsed.scheme == "https" else 80
	host_header = parsed.hostname if port == default_port else f"{parsed.hostname}:{port}"
	request_target = parsed.path or "/"
	if parsed.query:
		request_target = f"{request_target}?{parsed.query}"

	common_options = {"port": port, "timeout": urllib3.Timeout(connect=5, read=15), "retries": False, "maxsize": 1, "block": True, }
	last_error: Exception | None = None
	for address in addresses:
		if parsed.scheme == "https":
			pool = urllib3.HTTPSConnectionPool(address, cert_reqs=ssl.CERT_REQUIRED, assert_hostname=parsed.hostname, server_hostname=parsed.hostname, **common_options, )
		else:
			pool = urllib3.HTTPConnectionPool(address, **common_options)

		try:
			response = pool.request("GET", request_target, headers={"Host": host_header, "User-Agent": "OmniMCP/1.0", "Accept": "text/html,text/plain;q=0.9", }, preload_content=False, decode_content=True, redirect=False, )
			return response, pool
		except (urllib3.exceptions.HTTPError, OSError) as exc:
			last_error = exc
			pool.close()

	raise urllib3.exceptions.HTTPError("Unable to connect to any validated public address.") from last_error


@mcp.tool()
def get_current_time() -> str:
	"""Returns the current system time and date."""
	logging.info("Tool called: get_current_time")
	return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


@mcp.tool()
def list_directory(path: str = ".") -> str:
	"""Lists files and directories inside the configured workspace."""
	logging.info("Tool called: list_directory(path=%s)", path)
	try:
		directory = resolve_workspace_path(path)
		if not directory.is_dir():
			return f"Error: '{path}' is not a directory."

		items = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
		if not items:
			return "Directory is empty."
		return "\n".join(f"{'[dir]' if item.is_dir() else '[file]'} {display_path(item)}" for item in items)
	except (OSError, ValueError) as exc:
		logging.warning("Unable to list directory %s: %s", path, exc)
		return f"Error: {exc}"


@mcp.tool()
def read_file(file_path: str) -> str:
	"""Reads a UTF-8 text file inside the configured workspace."""
	logging.info("Tool called: read_file(file_path=%s)", file_path)
	try:
		path = resolve_workspace_path(file_path)
		if not path.is_file():
			return f"Error: '{file_path}' is not a file."
		if path.stat().st_size > MAX_FILE_SIZE:
			return f"Error: File exceeds the {MAX_FILE_SIZE}-byte size limit."
		return path.read_text(encoding="utf-8", errors="replace")
	except (OSError, ValueError) as exc:
		logging.warning("Unable to read file %s: %s", file_path, exc)
		return f"Error: {exc}"


@mcp.tool()
def write_file(file_path: str, content: str) -> str:
	"""Writes a UTF-8 text file inside the workspace, creating parent folders."""
	logging.info("Tool called: write_file(file_path=%s)", file_path)
	try:
		encoded = content.encode("utf-8")
		if len(encoded) > MAX_FILE_SIZE:
			return f"Error: Content exceeds the {MAX_FILE_SIZE}-byte size limit."

		path = resolve_workspace_path(file_path, allow_root=False)
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_bytes(encoded)
		return f"Successfully wrote to {display_path(path)}"
	except (OSError, ValueError) as exc:
		logging.warning("Unable to write file %s: %s", file_path, exc)
		return f"Error: {exc}"


@mcp.tool()
def delete_path(path: str) -> str:
	"""Deletes a file or directory inside the workspace, but never the workspace root."""
	logging.info("Tool called: delete_path(path=%s)", path)
	try:
		target = resolve_workspace_entry(path, allow_root=False)
		if target.is_symlink() or target.is_file():
			target.unlink()
		elif target.is_dir():
			shutil.rmtree(target)
		else:
			return f"Path '{path}' does not exist."
		return f"Successfully deleted {display_path(target)}"
	except (OSError, ValueError) as exc:
		logging.warning("Unable to delete path %s: %s", path, exc)
		return f"Error: {exc}"


@mcp.tool()
def get_system_info() -> str:
	"""Returns basic system, CPU, memory, and disk information."""
	logging.info("Tool called: get_system_info")
	try:
		info = {"System": platform.system(), "Node": platform.node(), "Release": platform.release(), "Machine": platform.machine(), "CPU Count": os.cpu_count(), "Memory Usage": f"{psutil.virtual_memory().percent}%",
		        "Workspace Disk Usage": f"{psutil.disk_usage(str(WORKSPACE)).percent}%", }
		return "\n".join(f"{key}: {value}" for key, value in info.items())
	except (OSError, RuntimeError) as exc:
		logging.warning("Unable to fetch system info: %s", exc)
		return f"Error: {exc}"


@mcp.tool()
def search_in_files(pattern: str, directory: str = ".", extension: str = "*") -> str:
	"""Searches for text inside workspace files, returning at most 20 matches."""
	logging.info("Tool called: search_in_files(pattern=%s, directory=%s, extension=%s)", pattern, directory, extension, )
	if not pattern:
		return "Error: Search pattern cannot be empty."

	try:
		root = resolve_workspace_path(directory)
		if not root.is_dir():
			return f"Error: '{directory}' is not a directory."

		results: list[str] = []
		for current_root, directories, files in os.walk(root, followlinks=False):
			directories[:] = [name for name in directories if not (Path(current_root) / name).is_symlink()]
			for filename in fnmatch.filter(files, extension):
				file_path = Path(current_root) / filename
				try:
					if file_path.is_symlink() or file_path.stat().st_size > MAX_FILE_SIZE:
						continue
					with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
						for line_number, line in enumerate(handle, 1):
							if pattern.casefold() in line.casefold():
								results.append(f"{display_path(file_path)}:{line_number}: {line.strip()}")
								if len(results) >= MAX_SEARCH_RESULTS:
									return "\n".join(results)
				except OSError as exc:
					logging.debug("Skipping unreadable file %s: %s", file_path, exc)
		return "\n".join(results) if results else "No matches found."
	except (OSError, ValueError) as exc:
		logging.warning("Search failed: %s", exc)
		return f"Error: {exc}"


@mcp.tool()
def add_todo(task: str) -> str:
	"""Adds a task to the workspace todo list."""
	logging.info("Tool called: add_todo")
	task = task.strip()
	if not task:
		return "Error: Task cannot be empty."
	if "\n" in task or "\r" in task:
		return "Error: Task must be a single line."

	try:
		with TODO_FILE.open("a", encoding="utf-8") as handle:
			handle.write(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {task}\n")
		return f"Added: {task}"
	except OSError as exc:
		logging.warning("Unable to add todo: %s", exc)
		return f"Error: {exc}"


@mcp.tool()
def list_todos() -> str:
	"""Lists all tasks in the workspace todo list."""
	logging.info("Tool called: list_todos")
	if not TODO_FILE.exists():
		return "No todos found."
	try:
		if TODO_FILE.stat().st_size > MAX_FILE_SIZE:
			return "Error: Todo file exceeds the configured file-size limit."
		todos = TODO_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
		return "\n".join(f"{index}. {todo}" for index, todo in enumerate(todos, 1)) or "Todo list is empty."
	except OSError as exc:
		logging.warning("Unable to list todos: %s", exc)
		return f"Error: {exc}"


@mcp.tool()
def clear_todos() -> str:
	"""Clears the workspace todo list."""
	logging.info("Tool called: clear_todos")
	try:
		TODO_FILE.unlink(missing_ok=True)
		return "Todo list cleared."
	except OSError as exc:
		logging.warning("Unable to clear todos: %s", exc)
		return f"Error: {exc}"


@mcp.tool()
def fetch_web_summary(url: str) -> str:
	"""Fetches a public website and returns its title and readable text."""
	logging.info("Tool called: fetch_web_summary(url=%s)", url)
	if not url.startswith(("http://", "https://")):
		url = f"https://{url}"

	response = None
	pool = None
	try:
		current_url = url
		for _ in range(MAX_REDIRECTS + 1):
			response, pool = request_public_url(current_url)
			if response.status in {301, 302, 303, 307, 308}:
				location = response.headers.get("location")
				response.release_conn()
				pool.close()
				response = None
				pool = None
				if not location:
					raise ValueError("Redirect response did not include a destination.")
				current_url = urljoin(current_url, location)
				continue
			break
		else:
			raise ValueError("Too many redirects.")

		if response is None:
			raise ValueError("Website returned no response.")
		if response.status >= 400:
			raise ValueError(f"Website returned HTTP {response.status}.")

		content_type = response.headers.get("content-type", "").lower()
		if "text/html" not in content_type and "text/plain" not in content_type:
			raise ValueError("Only HTML and plain-text responses are supported.")

		chunks: list[bytes] = []
		total_size = 0
		while True:
			chunk = response.read(16 * 1024)
			if not chunk:
				break
			total_size += len(chunk)
			if total_size > MAX_WEB_RESPONSE_SIZE:
				raise ValueError("Website response is too large.")
			chunks.append(chunk)

		encoding = "utf-8"
		for parameter in content_type.split(";")[1:]:
			key, separator, value = parameter.strip().partition("=")
			if separator and key.casefold() == "charset" and value.strip():
				encoding = value.strip().strip("\"'")
				break
		body = b"".join(chunks).decode(encoding, errors="replace")
		soup = BeautifulSoup(body, "html.parser")
		for element in soup(["script", "style", "noscript"]):
			element.decompose()

		title = soup.title.get_text(" ", strip=True) if soup.title else "No Title"
		clean_text = " ".join(soup.get_text(" ", strip=True).split())
		summary = clean_text[:4000]
		suffix = "..." if len(clean_text) > len(summary) else ""
		return f"Title: {title}\nURL: {current_url}\n\nContent: {summary}{suffix}"
	except (urllib3.exceptions.HTTPError, OSError, UnicodeError, ValueError) as exc:
		logging.warning("Web fetch failed for %s: %s", url, exc)
		return f"Error: {exc}"
	finally:
		if response is not None:
			response.release_conn()
		if pool is not None:
			pool.close()


if __name__ == "__main__":
	mcp.run(transport="stdio")
