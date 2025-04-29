import os
import fnmatch
import requests
import shutil
import platform
import psutil
from bs4 import BeautifulSoup
from datetime import datetime
from utils import setup_logger
from config import DevelopmentConfig
from mcp.server.fastmcp import FastMCP

# Initialize logger (configured to use stderr in utils/logger.py)
logging = setup_logger(__name__)

mcp = FastMCP("Advanced Multi Local Tool Server")

PATH = DevelopmentConfig.FILES_PATH
TODO_FILE = os.path.join(os.getcwd(), "todo.txt")

# ---- SYSTEM & OS TOOLS ----

@mcp.tool()
def get_current_time() -> str:
    """Returns the current system time and date."""
    logging.info("Tool called: get_current_time")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@mcp.tool()
def list_directory(path: str = ".") -> str:
    """Lists files and directories in the given path."""
    logging.info(f"Tool called: list_directory(path={path})")
    try:
        items = os.listdir(path)
        return "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        logging.error(f"Error listing directory {path}: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def read_file(file_path: str) -> str:
    """Reads the content of a file."""
    logging.info(f"Tool called: read_file(file_path={file_path})")
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error reading file {file_path}: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def write_file(file_path: str, content: str) -> str:
    """Writes content to a file (overwrites if exists)."""
    logging.info(f"Tool called: write_file(file_path={file_path})")
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {file_path}"
    except Exception as e:
        logging.error(f"Error writing to file {file_path}: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def delete_path(path: str) -> str:
    """Deletes a file or directory."""
    logging.info(f"Tool called: delete_path(path={path})")
    try:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            return f"Path '{path}' does not exist."
        return f"Successfully deleted {path}"
    except Exception as e:
        logging.error(f"Error deleting path {path}: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def get_system_info() -> str:
    """Returns basic system information (OS, CPU, Memory)."""
    logging.info("Tool called: get_system_info")
    try:
        info = {
            "System": platform.system(),
            "Node": platform.node(),
            "Release": platform.release(),
            "Machine": platform.machine(),
            "CPU Count": os.cpu_count(),
            "Memory usage": f"{psutil.virtual_memory().percent}%",
            "Disk usage": f"{psutil.disk_usage('/').percent}%"
        }
        return "\n".join([f"{k}: {v}" for k, v in info.items()])
    except Exception as e:
        logging.error(f"Error fetching system info: {str(e)}")
        return f"Error: {str(e)}"

# ---- SEARCH TOOLS ----

@mcp.tool()
def search_in_files(pattern: str, directory: str = ".", extension: str = "*") -> str:
    """Searches for a text pattern inside files within a directory."""
    logging.info(f"Tool called: search_in_files(pattern={pattern}, directory={directory})")
    results = []
    try:
        # Limit the search depth or size if needed, here we just do a basic walk
        for root, _, files in os.walk(directory):
            for filename in fnmatch.filter(files, extension):
                file_path = os.path.join(root, filename)
                try:
                    # Skip large files or binary files if necessary
                    if os.path.getsize(file_path) > 1024 * 1024: # 1MB limit
                        continue
                    with open(file_path, 'r', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if pattern.lower() in line.lower():
                                results.append(f"{file_path}:{i}: {line.strip()}")
                                if len(results) >= 20:
                                    break
                except: continue
                if len(results) >= 20: break
            if len(results) >= 20: break
        return "\n".join(results) if results else "No matches found."
    except Exception as e:
        logging.error(f"Search error: {str(e)}")
        return f"Error: {str(e)}"

# ---- TODO TOOLS ----

@mcp.tool()
def add_todo(task: str) -> str:
    """Adds a task to the todo list."""
    logging.info(f"Tool called: add_todo(task={task})")
    try:
        with open(TODO_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {task}\n")
        return f"Added: {task}"
    except Exception as e:
        logging.error(f"Error adding todo: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def list_todos() -> str:
    """Lists all tasks in the todo list."""
    logging.info("Tool called: list_todos")
    if not os.path.exists(TODO_FILE):
        return "No todos found."
    try:
        with open(TODO_FILE, "r", encoding="utf-8") as f:
            todos = f.readlines()
        return "".join([f"{i+1}. {todo}" for i, todo in enumerate(todos)]) if todos else "Todo list is empty."
    except Exception as e:
        logging.error(f"Error listing todos: {str(e)}")
        return f"Error: {str(e)}"

@mcp.tool()
def clear_todos() -> str:
    """Clears the entire todo list."""
    logging.info("Tool called: clear_todos")
    try:
        if os.path.exists(TODO_FILE):
            os.remove(TODO_FILE)
        return "Todo list cleared."
    except Exception as e:
        logging.error(f"Error clearing todos: {str(e)}")
        return f"Error: {str(e)}"

# ---- WEB TOOLS ----

@mcp.tool()
def fetch_web_summary(url: str) -> str:
    """Fetches a website and returns the title and content summary."""
    logging.info(f"Tool called: fetch_web_summary(url={url})")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=10, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=' ')
        clean_text = ' '.join([line.strip() for line in text.splitlines() if line.strip()])
        return f"Title: {soup.title.string if soup.title else 'No Title'}\n\nContent: {clean_text[:2000]}..."
    except Exception as e:
        logging.error(f"Web fetch error for {url}: {str(e)}")
        return f"Error: {str(e)}"

if __name__ == "__main__":
    mcp.run()
