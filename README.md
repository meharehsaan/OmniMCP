# OmniMCP Gemini Server

A sophisticated local AI assistant that combines the power of **LLM** with the **Model Context Protocol**, all wrapped in a sleek **Chainlit** interface. This server allows Gemini to interact directly with your local system, perform file operations, manage tasks, and browse the web.
OmniMCP (Because it connects to files, web, and system  further tools coming...)

---

## 🚀 Features

- **🧠 Advanced LLM**: Powered by Google Gemini for high quality reasoning and tool calling.
- **🛠️ MCP Integration**: Uses Model Context Protocol to bridge the gap between the LLM and local tools.
- **📂 File System Mastery**: List, read, write, and search files on your local machine.
- **🌐 Web Exploration**: Fetch and summarize content from any URL.
- **🖥️ System Monitoring**: Real time access to system health, OS info, and resource usage.
- **✅ Task Management**: Built in TODO list manager to keep track of your goals.
- **🔐 Secure Access**: Robust authentication layer to protect your server.

---

## 🛠️ Tools & Functionalities

The server exposes several tools that Gemini can call autonomously:

| Category | Tools | Description |
| :--- | :--- | :--- |
| **System** | `get_current_time`, `get_system_info` | Get system time and hardware/OS statistics. |
| **File Ops** | `list_directory`, `read_file`, `write_file`, `delete_path` | Complete file management capabilities. |
| **Search** | `search_in_files` | Grep-like search across files in a directory. |
| **Tasks** | `add_todo`, `list_todos`, `clear_todos` | Manage a persistent `todo.txt` list. |
| **Web** | `fetch_web_summary` | Extract text content and summaries from URLs. |

---

## 📋 Prerequisites

- **Python**: 3.10 or higher.
- **Gemini API Key**: Obtain one from the [Google AI Studio](https://aistudio.google.com/).
- **Internet Connection**: Required for Gemini API and web-based tools.

---

## ⚙️ Setup & Installation

Follow these steps to get your MCP server up and running:

### 1. Clone the Repository
```bash
git clone https://github.com/meharehsaan/OmniMCP.git
cd OmniMCP
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy the `.env.example` to a new file named `.env`:
```bash
cp .env.example .env
```
Edit the `.env` file with your credentials:
```env
# User Authentication
USER=your_preferred_username
PASSWORD=your_secure_password

# Gemini Configuration
GEMINI_API_KEY=your_google_gemini_api_key
GEMINI_MODEL_ID=gemini-2.0-flash-exp

# Chainlit Auth Secret
CHAINLIT_AUTH_SECRET=your_generated_secret_key
```

#### 🔑 Generating `CHAINLIT_AUTH_SECRET`
This secret is used to sign the session cookies. You can generate a secure one by running:
```bash
chainlit create-secret
```
Then copy the output into your `.env` file.

---

## 🏃 Running the Application

Start the Chainlit server with the following command:

```bash
chainlit run app.py -w
```

- The `-w` flag enables auto-reload, which is useful during development.
- Once started, navigate to `http://localhost:8000` (or the port shown in your terminal).
- **Login** using the `USER` and `PASSWORD` you defined in your `.env` file.

---

## 🤝 Contributing

Contributions are welcome! If you have a cool tool idea or found a bug, feel free to open an issue or submit a pull request.

1. Fork the Project.
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`).
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the Branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

---

Built with ❤️ by [Ehsaan Mehar](https://github.com/meharehsaan)
