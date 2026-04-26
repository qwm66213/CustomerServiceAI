const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");

// 生成简单 session_id，刷新页面后新会话
const sessionId = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36);

// 快捷按钮
document.querySelectorAll(".quick-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    inputEl.value = btn.dataset.q;
    sendMessage();
  });
});

// 回车发送
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

function appendMessage(text, role, source) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  if (source && role === "bot") {
    const tag = document.createElement("div");
    tag.className = "source-tag";
    const labels = {
      keyword: "FAQ匹配",
      vector: "语义匹配",
      ai: "AI回答",
      fallback: "转人工",
      system: "系统",
    };
    tag.textContent = labels[source] || source;
    div.appendChild(tag);
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

async function sendMessage() {
  const message = inputEl.value.trim();
  if (!message) return;

  appendMessage(message, "user");
  inputEl.value = "";
  sendBtn.disabled = true;
  showTyping();

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    const data = await res.json();
    hideTyping();
    appendMessage(data.reply, "bot", data.source);
  } catch {
    hideTyping();
    appendMessage("网络错误，请稍后重试。", "bot");
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}
