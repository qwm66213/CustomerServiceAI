const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const themeToggle = document.getElementById("themeToggle");

const sessionId = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36);

const sourceLabels = {
  keyword: "FAQ匹配",
  pinyin: "拼音匹配",
  edit_distance: "形近匹配",
  vector: "语义匹配",
  vector_low: "语义匹配(低置信)",
  ai: "AI回答",
  chat: "闲聊",
  fallback: "转人工",
  transfer: "转人工",
  system: "系统",
};

// ============ 暗色模式 ============
(function initTheme() {
  if (localStorage.getItem("theme") === "dark") {
    document.body.classList.add("dark");
    themeToggle.textContent = "☀️";
  }
})();
themeToggle.addEventListener("click", () => {
  document.body.classList.toggle("dark");
  const dark = document.body.classList.contains("dark");
  localStorage.setItem("theme", dark ? "dark" : "light");
  themeToggle.textContent = dark ? "☀️" : "🌙";
});

// ============ 回车发送 ============
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
sendBtn.addEventListener("click", sendMessage);

// ============ 动态快捷按钮 ============
async function loadSuggestions() {
  try {
    const res = await fetch(`/suggestions?session_id=${sessionId}`);
    const data = await res.json();
    const container = document.getElementById("quickActions");
    container.innerHTML = "";
    data.suggestions.forEach((q) => {
      const btn = document.createElement("button");
      btn.className = "quick-btn";
      btn.textContent = q;
      btn.addEventListener("click", () => {
        inputEl.value = q;
        sendMessage();
      });
      container.appendChild(btn);
    });
  } catch {}
}
loadSuggestions();

// ============ 消息渲染 ============
function appendMessage(text, role, source) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;

  if (source && role === "bot") {
    const tag = document.createElement("div");
    tag.className = "source-tag";
    tag.textContent = sourceLabels[source] || source;
    div.appendChild(tag);

    // 反馈按钮
    const fb = document.createElement("div");
    fb.className = "feedback-row";
    fb.innerHTML = `<button class="fb-btn fb-helpful">👍 有帮助</button><button class="fb-btn fb-unhelpful">👎 没帮助</button>`;
    fb.querySelectorAll(".fb-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rating = btn.classList.contains("fb-helpful") ? "helpful" : "unhelpful";
        try {
          await fetch("/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_id: sessionId,
              reply_text: text.slice(0, 200),
              rating,
              source,
            }),
          });
        } catch {}
        fb.innerHTML = `<span class="fb-done">${rating === "helpful" ? "✅ 感谢反馈" : "📝 已记录"}</span>`;
      });
    });
    div.appendChild(fb);
  }

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "msg bot";
  div.id = "typing";
  div.innerHTML = '<span class="typing"></span><span class="typing"></span><span class="typing"></span>';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById("typing");
  if (el) el.remove();
}

// ============ 发送消息 ============
async function sendMessage() {
  const message = inputEl.value.trim();
  if (!message) return;

  appendMessage(message, "user");
  inputEl.value = "";
  sendBtn.disabled = true;
  showTyping();

  try {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem("token");
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const res = await fetch("/chat", {
      method: "POST",
      headers,
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    const data = await res.json();
    hideTyping();
    appendMessage(data.reply, "bot", data.source);
    loadSuggestions();
  } catch {
    hideTyping();
    appendMessage("网络错误，请稍后重试。", "bot");
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}
