const elements = {
    loginView: document.querySelector("#login-view"),
    appView: document.querySelector("#app-view"),
    loginForm: document.querySelector("#login-form"),
    loginError: document.querySelector("#login-error"),
    userLabel: document.querySelector("#user-label"),
    userAvatar: document.querySelector("#user-avatar"),
    accountTrigger: document.querySelector("#account-trigger"),
    accountMenu: document.querySelector("#account-menu"),
    accountMenuLabel: document.querySelector("#account-menu-label"),
    statusLine: document.querySelector("#status-line"),
    statusIndicator: document.querySelector("#status-indicator"),
    conversationTitle: document.querySelector("#conversation-title"),
    messages: document.querySelector("#messages"),
    chatForm: document.querySelector("#chat-form"),
    messageInput: document.querySelector("#message-input"),
    sendButton: document.querySelector("#send-button"),
    logout: document.querySelector("#logout"),
    newChat: document.querySelector("#new-chat"),
    sidebar: document.querySelector("#sidebar"),
    sidebarCollapse: document.querySelector("#sidebar-collapse"),
    drawerOpen: document.querySelector("#drawer-open"),
    drawerClose: document.querySelector("#drawer-close"),
    drawerBackdrop: document.querySelector("#drawer-backdrop"),
    quickTheme: document.querySelector("#quick-theme"),
    settingsDialog: document.querySelector("#settings-dialog"),
    openSettings: document.querySelector("#open-settings"),
    settingsClose: document.querySelector("#settings-close"),
    passwordForm: document.querySelector("#password-form"),
    passwordMessage: document.querySelector("#password-message"),
    toastRegion: document.querySelector("#toast-region"),
    workspaceSwitcher: document.querySelector("#workspace-switcher"),
    workspaceMenu: document.querySelector("#workspace-menu"),
    threadFilter: document.querySelector("#thread-filter"),
    threadList: document.querySelector("#thread-list"),
    contextPanel: document.querySelector("#context-panel"),
    contextToggle: document.querySelector("#context-toggle"),
    contextClose: document.querySelector("#context-close"),
    activeTools: document.querySelector("#active-tools"),
    activeToolCount: document.querySelector("#active-tool-count"),
    availableTools: document.querySelector("#available-tools"),
    modelStateBadge: document.querySelector("#model-state-badge"),
    modelStateDetail: document.querySelector("#model-state-detail"),
    reasoningAccordion: document.querySelector("#reasoning-accordion"),
    reasoningState: document.querySelector("#reasoning-state"),
    reasoningDetail: document.querySelector("#reasoning-detail"),
    toolRoundCount: document.querySelector("#tool-round-count"),
    contextMessageCount: document.querySelector("#context-message-count"),
};

let socket = null;
let isBusy = false;
let thinkingElement = null;
let currentTheme = localStorage.getItem("omnimcp-theme") || "dark";
let transcript = [];
let toolRoundCount = 0;
let threadFilterActive = false;
let viewingArchivedThread = false;
let activeArchivedThreadId = null;
const toolSteps = new Map();
const contextToolItems = new Map();
const supportsModalDialog = typeof HTMLDialogElement !== "undefined";
const THREAD_STORAGE_KEY = "omnimcp-thread-snapshots";

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function inlineMarkdown(value) {
    let html = escapeHtml(value);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    html = html.replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
    return html;
}

function renderMarkdown(markdown) {
    const output = [];
    const segments = String(markdown || "").split(/```/);

    segments.forEach((segment, index) => {
        if (index % 2 === 1) {
            const languageMatch = segment.match(/^([\w+-]+)\n/);
            const language = languageMatch ? languageMatch[1] : "code";
            const code = segment.replace(/^([\w+-]+)\n/, "").replace(/\n$/, "");
            output.push(`
        <div class="code-container">
          <div class="code-header">
            <span>${escapeHtml(language)}</span>
            <button class="copy-button" type="button">Copy</button>
          </div>
          <pre><code>${escapeHtml(code)}</code></pre>
        </div>
      `);
            return;
        }

        const lines = segment.replace(/\r\n/g, "\n").split("\n");
        let paragraph = [];
        let list = [];
        let ordered = false;

        const flushParagraph = () => {
            if (!paragraph.length) return;
            output.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
            paragraph = [];
        };

        const flushList = () => {
            if (!list.length) return;
            const tag = ordered ? "ol" : "ul";
            output.push(`<${tag}>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</${tag}>`);
            list = [];
            ordered = false;
        };

        lines.forEach((line) => {
            const trimmed = line.trim();
            if (!trimmed) {
                flushParagraph();
                flushList();
                return;
            }

            const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
            if (heading) {
                flushParagraph();
                flushList();
                const level = Math.min(heading[1].length + 1, 4);
                output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
                return;
            }

            const bullet = trimmed.match(/^[-*]\s+(.+)$/);
            const number = trimmed.match(/^\d+\.\s+(.+)$/);
            if (bullet || number) {
                flushParagraph();
                const nextOrdered = Boolean(number);
                if (list.length && ordered !== nextOrdered) flushList();
                ordered = nextOrdered;
                list.push((bullet || number)[1]);
                return;
            }

            flushList();
            paragraph.push(trimmed);
        });

        flushParagraph();
        flushList();
    });

    return output.join("");
}

function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    elements.toastRegion.append(toast);
    setTimeout(() => toast.remove(), 3600);
}

function setModelState(state, detail) {
    const active = state !== "Ready";
    elements.modelStateBadge.textContent = state;
    elements.modelStateBadge.className = `state-badge ${active ? "active" : "ready"}`;
    elements.modelStateDetail.textContent = detail;
    elements.reasoningState.textContent = state.toLowerCase();
    elements.reasoningDetail.textContent = detail;
}

function setStatus(text, type = "warning") {
    elements.statusLine.textContent = text;
    elements.statusIndicator.className = `status-dot ${type}`;
    if (type === "success") {
        setModelState("Ready", "Awaiting instruction");
    } else if (type === "error") {
        setModelState("Error", text);
    } else {
        setModelState("Active", text);
    }
}

function setBusy(value) {
    isBusy = value;
    elements.messageInput.disabled = value;
    elements.sendButton.disabled = value;
    elements.reasoningAccordion.open = value;
}

function showLogin() {
    elements.loginView.hidden = false;
    elements.appView.hidden = true;
    setBusy(false);
}

function showApp(username) {
    elements.loginView.hidden = true;
    elements.appView.hidden = false;
    elements.userLabel.textContent = username || "Local user";
    elements.accountMenuLabel.textContent = username || "Local user";
    elements.userAvatar.textContent = (username || "U").trim().charAt(0).toUpperCase();
    elements.accountMenu.querySelector(".avatar").textContent =
        (username || "U").trim().charAt(0).toUpperCase();
}

function scrollToBottom(behavior = "smooth") {
    requestAnimationFrame(() => {
        elements.messages.scrollTo({top: elements.messages.scrollHeight, behavior});
    });
}

function updateConversationTitle(content) {
    if (transcript.some((item) => item.role === "user")) return;
    const clean = content.replace(/\s+/g, " ").trim();
    elements.conversationTitle.textContent = clean.length > 48 ? `${clean.slice(0, 48)}…` : clean;
    renderThreadList();
}

function ensureEmptyState() {
    if (elements.messages.children.length) return;
    const empty = document.createElement("section");
    empty.className = "empty-state";
    empty.innerHTML = `
    <div class="empty-state-header">
      <img class="empty-mark" src="/public/logo_mark.svg" alt="" />
      <div>
        <span class="overline">Agent workspace online</span>
        <h2>What are we solving?</h2>
      </div>
    </div>
    <p>Delegate research, inspect your controlled workspace, manage local files, or orchestrate the connected MCP tools.</p>
    <div class="suggestions">
      <button class="suggestion" type="button" data-prompt="List the files in my workspace and summarize what you find.">
        <strong>Audit the workspace</strong><span>Map files, structure, and likely execution paths</span>
      </button>
      <button class="suggestion" type="button" data-prompt="Show the current system time and system resource information.">
        <strong>Inspect system health</strong><span>Review time, memory, disk, and platform state</span>
      </button>
      <button class="suggestion" type="button" data-prompt="Show all items in my todo list.">
        <strong>Review active work</strong><span>Open and organize the local task list</span>
      </button>
      <button class="suggestion" type="button" data-prompt="Search my workspace for TODO and summarize the matches.">
        <strong>Locate technical debt</strong><span>Search TODO markers and summarize the findings</span>
      </button>
    </div>
  `;
    empty.querySelectorAll("[data-prompt]").forEach((button) => {
        button.addEventListener("click", () => {
            if (isBusy) return;
            elements.messageInput.value = button.dataset.prompt;
            elements.chatForm.requestSubmit();
        });
    });
    elements.messages.append(empty);
}

function removeEmptyState() {
    elements.messages.querySelector(".empty-state")?.remove();
}

function appendMessage(role, content, record = true) {
    removeEmptyState();
    const article = document.createElement("article");
    article.className = `message ${role}`;
    const inner = document.createElement("div");
    inner.className = "message-inner";
    const body = document.createElement("div");
    body.className = "message-body";
    body.innerHTML = role === "assistant"
        ? renderMarkdown(content)
        : escapeHtml(content).replaceAll("\n", "<br>");
    inner.append(body);
    article.append(inner);
    elements.messages.append(article);

    if (record) {
        transcript.push({
            role,
            content: String(content),
            timestamp: new Date().toISOString(),
        });
        updateContextMetrics();
    }
    scrollToBottom();
    return article;
}

function showThinking() {
    hideThinking();
    removeEmptyState();
    thinkingElement = document.createElement("div");
    thinkingElement.className = "thinking";
    thinkingElement.setAttribute("role", "status");
    thinkingElement.innerHTML = `
    <details class="thinking-accordion" open>
      <summary>
        <span class="thinking-label"><span class="reasoning-dot"></span> Extended thinking</span>
        <span>Working</span>
      </summary>
      <div class="thinking-detail">Synthesizing context and deciding whether local tools are required.</div>
    </details>
  `;
    elements.messages.append(thinkingElement);
    setModelState("Active", "Reasoning over the current request");
    scrollToBottom();
}

function hideThinking() {
    thinkingElement?.remove();
    thinkingElement = null;
}

function createToolStep(event) {
    hideThinking();
    removeEmptyState();
    const details = document.createElement("details");
    details.className = "tool-step";
    details.open = true;
    details.innerHTML = `
    <summary>
      <span class="tool-meta">
        <span class="tool-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.7 6.3 3-3a4.2 4.2 0 0 1-5.4 5.4l-6.6 6.6a2.1 2.1 0 0 0 3 3l6.6-6.6a4.2 4.2 0 0 1 5.4-5.4l-3 3"/></svg>
        </span>
        <span class="tool-name">${escapeHtml(event.name)}</span>
      </span>
      <span class="tool-state">Running</span>
    </summary>
    <pre><code>Input:\n${escapeHtml(JSON.stringify(event.input || {}, null, 2))}</code></pre>
  `;
    elements.messages.append(details);
    toolSteps.set(event.id, details);
    toolRoundCount += 1;
    elements.toolRoundCount.textContent = String(toolRoundCount);
    addContextTool(event);
    transcript.push({
        role: "tool",
        tool_name: event.name,
        content: `Input:\n${JSON.stringify(event.input || {}, null, 2)}`,
        timestamp: new Date().toISOString(),
    });
    updateContextMetrics();
    scrollToBottom();
}

function finishToolStep(event) {
    const details = toolSteps.get(event.id);
    if (!details) return;
    details.classList.add("done");
    details.open = false;
    details.querySelector(".tool-state").textContent = "Done";
    details.querySelector("code").textContent =
        `Input:\n${JSON.stringify(event.input || {}, null, 2)}\n\nOutput:\n${event.output || ""}`;
    const transcriptEntry = [...transcript].reverse().find(
        (item) => item.role === "tool" && item.tool_name === event.name && !item.content.includes("\n\nOutput:\n"),
    );
    if (transcriptEntry) transcriptEntry.content += `\n\nOutput:\n${event.output || ""}`;
    finishContextTool(event);
    updateContextMetrics();
    showThinking();
}

function resetConversation() {
    archiveCurrentThread();
    transcript = [];
    toolRoundCount = 0;
    viewingArchivedThread = false;
    activeArchivedThreadId = null;
    toolSteps.clear();
    contextToolItems.clear();
    hideThinking();
    elements.messages.innerHTML = "";
    elements.conversationTitle.textContent = "New conversation";
    elements.toolRoundCount.textContent = "0";
    elements.messageInput.placeholder = "Ask OmniMCP to reason, inspect, or act...";
    setBusy(false);
    renderActiveToolsEmpty();
    updateContextMetrics();
    renderThreadList();
    ensureEmptyState();
}

function updateContextMetrics() {
    const messageCount = transcript.filter((item) => item.role !== "tool").length;
    elements.contextMessageCount.textContent = `${messageCount} message${messageCount === 1 ? "" : "s"}`;
}

function renderAvailableTools(tools) {
    const list = Array.isArray(tools) ? tools : [];
    elements.availableTools.innerHTML = "";
    if (!list.length) {
        elements.availableTools.innerHTML = '<span class="capability-chip">No tools discovered</span>';
        return;
    }
    list.forEach((tool) => {
        const chip = document.createElement("span");
        chip.className = "capability-chip";
        chip.textContent = tool.name;
        chip.title = tool.description || tool.name;
        elements.availableTools.append(chip);
    });
}

function renderActiveToolsEmpty() {
    elements.activeTools.innerHTML = `
    <div class="context-empty">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18"/></svg>
      <span>No tools running</span>
    </div>
  `;
    elements.activeToolCount.textContent = "0";
}

function addContextTool(event) {
    if (!contextToolItems.size) elements.activeTools.innerHTML = "";
    const item = document.createElement("div");
    item.className = "active-tool";
    item.innerHTML = `
    <span class="active-tool-icon"></span>
    <span class="active-tool-name">${escapeHtml(event.name)}</span>
    <span class="active-tool-state">Running</span>
  `;
    elements.activeTools.prepend(item);
    contextToolItems.set(event.id, item);
    elements.activeToolCount.textContent = String(
        [...contextToolItems.values()].filter((tool) => !tool.classList.contains("done")).length,
    );
    setModelState("Tool call", `Running ${event.name}`);
}

function finishContextTool(event) {
    const item = contextToolItems.get(event.id);
    if (!item) return;
    item.classList.add("done");
    item.querySelector(".active-tool-state").textContent = "Done";
    elements.activeToolCount.textContent = String(
        [...contextToolItems.values()].filter((tool) => !tool.classList.contains("done")).length,
    );
    setModelState("Active", "Processing tool results");
}

function loadThreadSnapshots() {
    try {
        const value = JSON.parse(localStorage.getItem(THREAD_STORAGE_KEY) || "[]");
        return Array.isArray(value) ? value : [];
    } catch {
        return [];
    }
}

function saveThreadSnapshots(snapshots) {
    localStorage.setItem(THREAD_STORAGE_KEY, JSON.stringify(snapshots.slice(0, 30)));
}

function archiveCurrentThread() {
    if (!transcript.some((item) => item.role === "user")) return;
    const snapshots = loadThreadSnapshots();
    const title = elements.conversationTitle.textContent || "Conversation";
    const fingerprint = transcript.find((item) => item.role === "user")?.content || title;
    const snapshot = {
        id: `${Date.now()}-${fingerprint.slice(0, 16)}`,
        title,
        createdAt: new Date().toISOString(),
        messages: transcript,
    };
    const deduped = snapshots.filter((item) => item.messages?.[0]?.content !== fingerprint);
    saveThreadSnapshots([snapshot, ...deduped]);
}

function threadGroupFor(dateValue) {
    const date = new Date(dateValue);
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startYesterday = new Date(startToday);
    startYesterday.setDate(startYesterday.getDate() - 1);
    if (date >= startToday) return "today";
    if (date >= startYesterday) return "yesterday";
    return "week";
}

function renderThreadList() {
    const groups = {
        today: elements.threadList.querySelector('[data-thread-group="today"]'),
        yesterday: elements.threadList.querySelector('[data-thread-group="yesterday"]'),
        week: elements.threadList.querySelector('[data-thread-group="week"]'),
    };
    Object.values(groups).forEach((group) => {
        group.querySelector(".thread-items").innerHTML = "";
        group.hidden = true;
    });

    const current = document.createElement("div");
    current.className = `thread-row${viewingArchivedThread ? "" : " active"}`;
    current.dataset.currentThread = "";
    current.innerHTML = `
    <button class="thread-item" type="button" data-thread-open="current">
      <span class="thread-title">${escapeHtml(
        viewingArchivedThread ? "New conversation" : elements.conversationTitle.textContent || "New conversation",
    )}</span>
    </button>
    <button class="thread-menu" type="button" data-thread-menu aria-label="Conversation actions"
      aria-expanded="false">...</button>
    <div class="thread-actions-menu" hidden>
      <button type="button" data-thread-action="rename">Rename</button>
      <button type="button" data-thread-action="delete" class="danger">Delete</button>
    </div>
  `;
    groups.today.hidden = false;
    groups.today.querySelector(".thread-items").append(current);

    loadThreadSnapshots().forEach((thread) => {
        const groupKey = threadGroupFor(thread.createdAt);
        if (threadFilterActive && groupKey !== "today") return;
        const group = groups[groupKey];
        group.hidden = false;
        const row = document.createElement("div");
        row.className = `thread-row${activeArchivedThreadId === thread.id ? " active" : ""}`;
        row.dataset.threadId = thread.id;
        row.innerHTML = `
      <button class="thread-item" type="button" data-thread-open="${escapeHtml(thread.id)}">
        <span class="thread-title">${escapeHtml(thread.title || "Conversation")}</span>
      </button>
      <button class="thread-menu" type="button" data-thread-menu aria-label="Conversation actions"
        aria-expanded="false">...</button>
      <div class="thread-actions-menu" hidden>
        <button type="button" data-thread-action="rename">Rename</button>
        <button type="button" data-thread-action="delete" class="danger">Delete</button>
      </div>
    `;
        group.querySelector(".thread-items").append(row);
    });
}

function closeThreadMenus(except = null) {
    elements.threadList.querySelectorAll(".thread-actions-menu").forEach((menu) => {
        if (menu === except) return;
        menu.hidden = true;
        menu.closest(".thread-row")?.querySelector("[data-thread-menu]")
            ?.setAttribute("aria-expanded", "false");
    });
}

function renameThread(row) {
    const threadId = row.dataset.threadId;
    const currentTitle = row.querySelector(".thread-title")?.textContent?.trim() || "Conversation";
    const nextTitle = window.prompt("Rename conversation", currentTitle)?.trim();
    if (!nextTitle || nextTitle === currentTitle) return;

    if (threadId) {
        const snapshots = loadThreadSnapshots();
        const thread = snapshots.find((item) => item.id === threadId);
        if (!thread) return;
        thread.title = nextTitle.slice(0, 120);
        saveThreadSnapshots(snapshots);
        if (activeArchivedThreadId === threadId) elements.conversationTitle.textContent = thread.title;
    } else {
        elements.conversationTitle.textContent = nextTitle.slice(0, 120);
    }
    renderThreadList();
    showToast("Conversation renamed.");
}

async function deleteThread(row) {
    const threadId = row.dataset.threadId;
    const title = row.querySelector(".thread-title")?.textContent?.trim() || "this conversation";
    if (!window.confirm(`Delete "${title}"? This cannot be undone.`)) return;

    if (threadId) {
        saveThreadSnapshots(loadThreadSnapshots().filter((item) => item.id !== threadId));
        if (activeArchivedThreadId === threadId) {
            try {
                const response = await fetch("/api/chat/reset", {method: "POST"});
                if (!response.ok) throw new Error();
            } catch {
                showToast("Conversation was removed locally, but the server could not reset.", "error");
            }
            transcript = [];
            viewingArchivedThread = false;
            activeArchivedThreadId = null;
            elements.messages.innerHTML = "";
            elements.conversationTitle.textContent = "New conversation";
            elements.messageInput.disabled = false;
            elements.sendButton.disabled = false;
            elements.messageInput.placeholder = "Ask OmniMCP to reason, inspect, or act...";
            ensureEmptyState();
            updateContextMetrics();
            setStatus(socket?.readyState === WebSocket.OPEN ? "Ready" : "Disconnected",
                socket?.readyState === WebSocket.OPEN ? "success" : "error");
        }
    } else {
        if (isBusy) {
            showToast("Wait for the current response before deleting this conversation.");
            return;
        }
        try {
            const response = await fetch("/api/chat/reset", {method: "POST"});
            if (!response.ok) throw new Error();
            transcript = [];
            elements.messages.innerHTML = "";
            elements.conversationTitle.textContent = "New conversation";
            toolRoundCount = 0;
            toolSteps.clear();
            contextToolItems.clear();
            renderActiveToolsEmpty();
            updateContextMetrics();
            ensureEmptyState();
            setStatus("Ready", "success");
        } catch {
            showToast("Unable to delete the current conversation.", "error");
            return;
        }
    }
    renderThreadList();
    showToast("Conversation deleted.");
}

function openThreadSnapshot(threadId) {
    const thread = loadThreadSnapshots().find((item) => item.id === threadId);
    if (!thread) return;
    transcript = Array.isArray(thread.messages) ? thread.messages : [];
    viewingArchivedThread = true;
    activeArchivedThreadId = threadId;
    hideThinking();
    toolSteps.clear();
    elements.messages.innerHTML = "";
    elements.conversationTitle.textContent = thread.title || "Conversation";
    transcript.forEach((message) => {
        if (message.role === "tool") {
            const details = document.createElement("details");
            details.className = "tool-step done";
            details.innerHTML = `
        <summary><span class="tool-meta"><span class="tool-name">${escapeHtml(message.tool_name || "tool")}</span></span><span class="tool-state">Archived</span></summary>
        <pre><code>${escapeHtml(message.content)}</code></pre>
      `;
            elements.messages.append(details);
        } else {
            appendMessage(message.role, message.content, false);
        }
    });
    updateContextMetrics();
    renderThreadList();
    setDrawer(false);
    elements.messageInput.disabled = true;
    elements.sendButton.disabled = true;
    elements.messageInput.placeholder = "Archived thread - start a new thread to continue";
    elements.statusLine.textContent = "Archived thread";
    elements.statusIndicator.className = "status-dot success";
    setModelState("Archive", "Read-only local thread snapshot");
    showToast("Loaded a read-only local thread snapshot. Start a new thread to continue.");
}

function connectSocket() {
    if (socket) socket.close();
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${window.location.host}/ws/chat`);

    socket.addEventListener("open", () => setStatus("Starting local tools", "warning"));
    socket.addEventListener("message", (message) => {
        let event;
        try {
            event = JSON.parse(message.data);
        } catch {
            showToast("The server sent an invalid message.", "error");
            return;
        }

        if (event.type === "status") {
            setStatus(event.content || "Working", "warning");
            if (isBusy) showThinking();
        } else if (event.type === "ready") {
            setStatus("Ready", "success");
            setBusy(false);
            renderAvailableTools(event.tools);
            ensureEmptyState();
        } else if (event.type === "approval_required") {
            hideThinking();
            const details = JSON.stringify(event.input || {}, null, 2);
            const approved = window.confirm(
                `OmniMCP wants to run "${event.name}".\n\n${details}\n\nAllow this action?`,
            );
            if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: "tool_approval",
                    id: event.id,
                    approved,
                }));
            }
            if (approved) showThinking();
            else showToast("Tool execution denied.");
        } else if (event.type === "tool_start") {
            createToolStep(event);
        } else if (event.type === "tool_end") {
            finishToolStep(event);
        } else if (event.type === "assistant") {
            hideThinking();
            appendMessage("assistant", event.content || "");
        } else if (event.type === "done") {
            hideThinking();
            setStatus("Ready", "success");
            setBusy(false);
            elements.messageInput.focus();
        } else if (event.type === "error") {
            hideThinking();
            appendMessage("system", event.content || "The request failed.");
            setStatus("Error", "error");
            setBusy(false);
        }
    });

    socket.addEventListener("close", (event) => {
        hideThinking();
        setStatus("Disconnected", "error");
        setBusy(true);
        if (event.code === 1008) {
            resetConversation();
            showLogin();
        }
    });

    socket.addEventListener("error", () => {
        hideThinking();
        setStatus("Connection error", "error");
        setBusy(true);
    });
}

async function loadSession() {
    const response = await fetch("/api/session", {cache: "no-store"});
    if (!response.ok) throw new Error(`Session request failed: ${response.status}`);
    const session = await response.json();
    if (!session.authenticated) {
        showLogin();
        return;
    }
    showApp(session.username);
    ensureEmptyState();
    connectSocket();
}

function applyTheme(theme) {
    currentTheme = ["light", "dark", "system"].includes(theme) ? theme : "system";
    document.documentElement.dataset.theme = currentTheme;
    localStorage.setItem("omnimcp-theme", currentTheme);
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
        button.classList.toggle("active", button.dataset.themeChoice === currentTheme);
        button.setAttribute("aria-pressed", String(button.dataset.themeChoice === currentTheme));
    });
}

function resolvedTheme() {
    if (currentTheme !== "system") return currentTheme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function toggleQuickTheme() {
    applyTheme(resolvedTheme() === "dark" ? "light" : "dark");
}

function setDrawer(open) {
    elements.sidebar.classList.toggle("drawer-open", open);
    elements.drawerBackdrop.hidden = !open;
    document.body.style.overflow = open ? "hidden" : "";
}

function setContextOpen(open) {
    const compact = window.matchMedia("(max-width: 1180px)").matches;
    if (compact) {
        elements.appView.classList.toggle("context-open", open);
        elements.appView.classList.remove("context-collapsed");
    } else {
        elements.appView.classList.toggle("context-collapsed", !open);
        elements.appView.classList.remove("context-open");
    }
    elements.contextToggle.setAttribute("aria-expanded", String(open));
}

function openSettingsDialog() {
    if (!elements.settingsDialog) return;
    if (supportsModalDialog && typeof elements.settingsDialog.showModal === "function") {
        elements.settingsDialog.showModal();
        return;
    }
    elements.settingsDialog.setAttribute("open", "open");
}

function closeSettingsDialog() {
    if (!elements.settingsDialog) return;
    if (supportsModalDialog && typeof elements.settingsDialog.close === "function") {
        elements.settingsDialog.close();
        return;
    }
    elements.settingsDialog.removeAttribute("open");
}

async function exportConversation() {
    if (!transcript.length) {
        showToast("There is no conversation to export.");
        return;
    }
    try {
        const response = await fetch("/api/conversation/export", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                title: elements.conversationTitle.textContent,
                messages: transcript,
            }),
        });
        if (!response.ok) throw new Error(`Export failed: ${response.status}`);

        const blob = await response.blob();
        const disposition = response.headers.get("content-disposition") || "";
        const match = disposition.match(/filename="([^"]+)"/);
        const filename = match?.[1] || "omnimcp-conversation.zip";
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast("Conversation exported.");
    } catch (error) {
        console.error(error);
        showToast("Unable to export the conversation.", "error");
    }
}

elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.loginError.textContent = "";
    const form = new FormData(elements.loginForm);
    const button = elements.loginForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
        const response = await fetch("/api/login", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                username: String(form.get("username") || "").trim(),
                password: form.get("password"),
            }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            elements.loginError.textContent = response.status === 429
                ? "Too many attempts. Try again in a few minutes."
                : payload.detail || "Invalid username or password.";
            return;
        }
        const payload = await response.json();
        elements.loginForm.reset();
        showApp(payload.username);
        resetConversation();
        connectSocket();
    } catch {
        elements.loginError.textContent = "Unable to reach the OmniMCP server.";
    } finally {
        button.disabled = false;
    }
});

elements.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (viewingArchivedThread) {
        showToast("Start a new thread before sending another message.");
        return;
    }
    const content = elements.messageInput.value.trim();
    if (!content || isBusy || !socket || socket.readyState !== WebSocket.OPEN) return;
    updateConversationTitle(content);
    appendMessage("user", content);
    elements.messageInput.value = "";
    elements.messageInput.style.height = "auto";
    setBusy(true);
    setStatus("Thinking", "warning");
    showThinking();
    socket.send(JSON.stringify({type: "message", content}));
});

elements.messageInput.addEventListener("input", () => {
    elements.messageInput.style.height = "auto";
    elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 190)}px`;
});

elements.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        elements.chatForm.requestSubmit();
    }
});

elements.messages.addEventListener("click", async (event) => {
    const button = event.target.closest(".copy-button");
    if (!button) return;
    const code = button.closest(".code-container")?.querySelector("code")?.textContent || "";
    try {
        await navigator.clipboard.writeText(code);
        button.textContent = "Copied";
        setTimeout(() => {
            button.textContent = "Copy";
        }, 1500);
    } catch {
        showToast("Clipboard access was denied.", "error");
    }
});

elements.newChat.addEventListener("click", async () => {
    if (isBusy) {
        showToast("Wait for the current response to finish.");
        return;
    }
    try {
        const response = await fetch("/api/chat/reset", {method: "POST"});
        if (!response.ok) throw new Error();
        resetConversation();
        setStatus("Ready", "success");
        setDrawer(false);
        elements.messageInput.focus();
    } catch {
        showToast("Unable to start a new conversation.", "error");
    }
});

elements.logout.addEventListener("click", async () => {
    try {
        await fetch("/api/logout", {method: "POST"});
    } finally {
        socket?.close();
        socket = null;
        resetConversation();
        setDrawer(false);
        showLogin();
    }
});

elements.sidebarCollapse.addEventListener("click", () => {
    const collapsed = elements.appView.classList.toggle("sidebar-collapsed");
    localStorage.setItem("omnimcp-sidebar-collapsed", String(collapsed));
    elements.accountMenu.hidden = true;
    elements.accountTrigger.setAttribute("aria-expanded", "false");
});

elements.accountTrigger.addEventListener("click", () => {
    const open = elements.accountMenu.hidden;
    elements.accountMenu.hidden = !open;
    elements.accountTrigger.setAttribute("aria-expanded", String(open));
});

elements.drawerOpen.addEventListener("click", () => setDrawer(true));
elements.drawerClose.addEventListener("click", () => setDrawer(false));
elements.drawerBackdrop.addEventListener("click", () => setDrawer(false));
elements.quickTheme.addEventListener("click", toggleQuickTheme);
elements.contextToggle.addEventListener("click", () => {
    const compact = window.matchMedia("(max-width: 1180px)").matches;
    const open = compact
        ? !elements.appView.classList.contains("context-open")
        : elements.appView.classList.contains("context-collapsed");
    setContextOpen(open);
});
elements.contextClose.addEventListener("click", () => setContextOpen(false));

elements.workspaceSwitcher.addEventListener("click", () => {
    const open = elements.workspaceMenu.hidden;
    elements.workspaceMenu.hidden = !open;
    elements.workspaceSwitcher.setAttribute("aria-expanded", String(open));
});

document.addEventListener("click", (event) => {
    if (
        !elements.workspaceMenu.hidden
        && !elements.workspaceMenu.contains(event.target)
        && !elements.workspaceSwitcher.contains(event.target)
    ) {
        elements.workspaceMenu.hidden = true;
        elements.workspaceSwitcher.setAttribute("aria-expanded", "false");
    }
    if (
        !elements.accountMenu.hidden
        && !elements.accountMenu.contains(event.target)
        && !elements.accountTrigger.contains(event.target)
    ) {
        elements.accountMenu.hidden = true;
        elements.accountTrigger.setAttribute("aria-expanded", "false");
    }
    if (!event.target.closest(".thread-row")) closeThreadMenus();
});

elements.threadFilter.addEventListener("click", () => {
    threadFilterActive = !threadFilterActive;
    elements.threadFilter.classList.toggle("active", threadFilterActive);
    elements.threadFilter.title = threadFilterActive ? "Showing today's threads" : "Filter threads";
    renderThreadList();
});

elements.threadList.addEventListener("click", (event) => {
    const row = event.target.closest(".thread-row");
    if (!row) return;
    const menuButton = event.target.closest("[data-thread-menu]");
    if (menuButton) {
        const menu = row.querySelector(".thread-actions-menu");
        const open = menu.hidden;
        closeThreadMenus(menu);
        row.classList.remove("menu-up");
        if (open) {
            const listBottom = elements.threadList.getBoundingClientRect().bottom;
            const rowBottom = row.getBoundingClientRect().bottom;
            row.classList.toggle("menu-up", listBottom - rowBottom < 90);
        }
        menu.hidden = !open;
        menuButton.setAttribute("aria-expanded", String(open));
        return;
    }
    const actionButton = event.target.closest("[data-thread-action]");
    if (actionButton) {
        closeThreadMenus();
        if (actionButton.dataset.threadAction === "rename") renameThread(row);
        else deleteThread(row);
        return;
    }
    const openButton = event.target.closest("[data-thread-open]");
    if (!openButton) return;
    if (row.dataset.threadId) {
        openThreadSnapshot(row.dataset.threadId);
    } else if (viewingArchivedThread) {
        elements.newChat.click();
    }
});

document.querySelectorAll("[data-composer-action]").forEach((button) => {
    button.addEventListener("click", () => {
        const action = button.dataset.composerAction;
        if (action === "attach") {
            showToast("File attachment UI is ready for a future upload tool.");
        } else if (action === "model") {
            showToast("Gemini is the active model for this workspace.");
        } else {
            setContextOpen(true);
        }
    });
});

document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
});

document.querySelectorAll("#export-chat, #topbar-export, #settings-export").forEach((button) => {
    button.addEventListener("click", exportConversation);
});

elements.openSettings.addEventListener("click", () => {
    setDrawer(false);
    openSettingsDialog();
});
elements.settingsClose.addEventListener("click", () => closeSettingsDialog());
elements.settingsDialog.addEventListener("click", (event) => {
    if (event.target === elements.settingsDialog) closeSettingsDialog();
});

elements.passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.passwordMessage.className = "form-message";
    elements.passwordMessage.textContent = "";
    const form = new FormData(elements.passwordForm);
    const currentPassword = String(form.get("current_password") || "");
    const newPassword = String(form.get("new_password") || "");
    const confirmation = String(form.get("confirm_password") || "");
    if (newPassword !== confirmation) {
        elements.passwordMessage.className = "form-message error";
        elements.passwordMessage.textContent = "New passwords do not match.";
        return;
    }

    const button = elements.passwordForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
        const response = await fetch("/api/settings/password", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                current_password: currentPassword,
                new_password: newPassword,
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            elements.passwordMessage.className = "form-message error";
            elements.passwordMessage.textContent = payload.detail || "Unable to change password.";
            return;
        }
        elements.passwordForm.reset();
        elements.passwordMessage.textContent = "Password updated successfully.";
        showToast("Password updated.");
    } catch {
        elements.passwordMessage.className = "form-message error";
        elements.passwordMessage.textContent = "Unable to reach the server.";
    } finally {
        button.disabled = false;
    }
});

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.passwordToggle);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        button.textContent = show ? "Hide" : "Show";
        button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
});

applyTheme(currentTheme);
elements.appView.classList.toggle(
    "sidebar-collapsed",
    localStorage.getItem("omnimcp-sidebar-collapsed") === "true",
);
renderActiveToolsEmpty();
updateContextMetrics();
renderThreadList();
setContextOpen(!window.matchMedia("(max-width: 1180px)").matches);
loadSession().catch((error) => {
    console.error(error);
    showLogin();
});
