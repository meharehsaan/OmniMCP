import os
import fnmatch
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from config import DevelopmentConfig
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Advanced Multi Local Tool Server")

PATH = DevelopmentConfig.FILES_PATH

# ---- SYSTEM TOOLS ----
@mcp.tool()
def get_current_time() -> str:
    """Returns the current system time and date."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def search_in_files(pattern: str, directory: str = ".", extension: str = "*") -> str:
    """Searches for a text pattern inside files within a directory."""
    results = []
    try:
        for root, _, files in os.walk(directory):
            for filename in fnmatch.filter(files, extension):
                file_path = os.path.join(root, filename)
                try:
                    with open(file_path, 'r', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if pattern.lower() in line.lower():
                                results.append(f"{file_path}:{i}: {line.strip()}")
                except: continue
        return "\n".join(results[:20]) if results else "No matches found."
    except Exception as e:
        return f"Error: {str(e)}"


# ---- WEB TOOLS (Mid-Level) ----
@mcp.tool()
def fetch_web_summary(url: str) -> str:
    """Fetches a website and returns the title and main text content (first 1000 chars)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=10, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=' ')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        clean_text = ' '.join(lines)
        return f"Title: {soup.title.string if soup.title else 'No Title'}\n\nContent: {clean_text[:1000]}..."
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"


if __name__ == "__main__":
    mcp.run()
