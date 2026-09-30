const PENDING = ["uploaded", "parsing", "analyzing", "chunking", "embedding", "indexing"];
const TIER_LABEL = {
  primary: "دليل أساسي",
  supporting: "مساند",
  related: "ذو صلة",
};
const STATUS_LABEL = {
  uploaded: "تم الرفع",
  parsing: "تحليل",
  analyzing: "تحليل بنيوي",
  chunking: "تقسيم",
  embedding: "Embeddings",
  indexing: "فهرسة",
  completed: "مكتمل",
  failed: "فشل",
};

let pollTimer = null;

const $ = (sel) => document.querySelector(sel);

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `خطأ ${response.status}`);
  }
  return payload;
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-active"));
    tab.classList.add("is-active");
    $(`#${tab.dataset.tab}`).classList.add("is-active");
  });
});

/* ---------- header ---------- */
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    $("#configLine").textContent =
      `LLM: ${cfg.ollama_model} · Embeddings: ${cfg.embedding_model} · TOP_K=${cfg.top_k} · ` +
      `Chunk=${cfg.chunk_size}/${cfg.chunk_overlap} · ${cfg.supported_extensions.join(" ")}`;
  } catch (err) {
    $("#configLine").textContent = err.message;
  }
}

async function loadHealth() {
  const box = $("#healthBadges");
  try {
    const h = await api("/api/health");
    const parts = [
      ["Ollama", h.ollama.reachable && h.ollama.model_available],
      ["Embeddings", h.embeddings.reachable && h.embeddings.model_available],
      [`Qdrant (${h.qdrant.points ?? 0} points)`, h.qdrant.reachable],
    ];
    box.innerHTML = parts
      .map(([name, ok]) => `<span class="badge ${ok ? "ok" : "err"}">${name}</span>`)
      .join("");
  } catch {
    box.innerHTML = '<span class="badge err">الخدمة غير متاحة</span>';
  }
}

/* ---------- upload ---------- */
const dropzone = $("#dropzone");
const fileInput = $("#fileInput");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("drag");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    showFileName();
  }
});
fileInput.addEventListener("change", showFileName);

function showFileName() {
  const file = fileInput.files[0];
  $("#dropLabel").textContent = file
    ? `${file.name} — ${(file.size / 1024).toFixed(1)} KB`
    : "اسحب ملف .md هنا أو اضغط للاختيار";
}

$("#uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const msg = $("#uploadMsg");
  if (!fileInput.files.length) {
    msg.className = "msg err";
    msg.textContent = "اختر ملفًا أولًا.";
    return;
  }

  const body = new FormData(event.target);
  body.set("file", fileInput.files[0]);

  $("#uploadBtn").disabled = true;
  msg.className = "msg";
  msg.textContent = "جارٍ الرفع…";

  try {
    const doc = await api("/api/documents/upload", { method: "POST", body });
    msg.className = "msg ok";
    msg.textContent = `تم استلام «${doc.filename}» وبدأت المعالجة.`;
    event.target.reset();
    showFileName();
    loadDocuments();
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = err.message;
  } finally {
    $("#uploadBtn").disabled = false;
  }
});

/* ---------- documents ---------- */
function statusClass(status) {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return "pending";
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("ar-AE", { dateStyle: "short", timeStyle: "short" });
}

async function loadDocuments() {
  const tbody = $("#docsTable tbody");
  try {
    const data = await api("/api/documents");
    $("#docsEmpty").hidden = data.total > 0;
    tbody.innerHTML = data.items
      .map(
        (doc) => `
        <tr>
          <td class="file">
            ${escapeHtml(doc.filename)}
            <small>${escapeHtml(doc.title || "")}</small>
          </td>
          <td>${escapeHtml(doc.category)}</td>
          <td>${escapeHtml(doc.version)}</td>
          <td>
            <span class="status ${statusClass(doc.status)}">${STATUS_LABEL[doc.status] || doc.status}</span>
            ${doc.error_message ? `<span class="err-text">${escapeHtml(doc.error_message)}</span>` : ""}
          </td>
          <td>${doc.chunk_count}</td>
          <td>${formatDate(doc.uploaded_at)}</td>
          <td>
            <div class="actions">
              <button class="ghost" data-action="chunks" data-id="${doc.id}" ${doc.status !== "completed" ? "disabled" : ""}>عرض</button>
              <button class="ghost" data-action="reindex" data-id="${doc.id}">إعادة فهرسة</button>
              <button class="danger" data-action="delete" data-id="${doc.id}" data-name="${escapeHtml(doc.filename)}">حذف</button>
            </div>
          </td>
        </tr>`
      )
      .join("");

    const busy = data.items.some((doc) => PENDING.includes(doc.status));
    clearTimeout(pollTimer);
    if (busy) pollTimer = setTimeout(loadDocuments, 1500);
    else loadHealth();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="err-text">${escapeHtml(err.message)}</td></tr>`;
  }
}

$("#docsTable").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id, name } = button.dataset;

  if (action === "delete") {
    if (!confirm(`حذف «${name}» نهائيًا مع كل متجهاته؟`)) return;
    button.disabled = true;
    try {
      await api(`/api/documents/${id}`, { method: "DELETE" });
      loadDocuments();
    } catch (err) {
      alert(err.message);
      button.disabled = false;
    }
  } else if (action === "reindex") {
    button.disabled = true;
    try {
      await api(`/api/documents/${id}/reindex`, { method: "POST" });
      loadDocuments();
    } catch (err) {
      alert(err.message);
      button.disabled = false;
    }
  } else if (action === "chunks") {
    try {
      const doc = await api(`/api/documents/${id}`);
      $("#modalTitle").textContent = `${doc.filename} — ${doc.chunks.length} chunk`;
      $("#modalContent").innerHTML = doc.chunks
        .map(
          (chunk) => `
          <div class="chunk">
            <header>#${chunk.index} · ${escapeHtml(chunk.section)} · ${chunk.char_count} حرف</header>
            <pre>${escapeHtml(chunk.content)}</pre>
          </div>`
        )
        .join("");
      $("#chunksModal").hidden = false;
    } catch (err) {
      alert(err.message);
    }
  }
});

$("#modalClose").addEventListener("click", () => ($("#chunksModal").hidden = true));
$("#chunksModal").addEventListener("click", (event) => {
  if (event.target.id === "chunksModal") $("#chunksModal").hidden = true;
});
$("#refreshBtn").addEventListener("click", () => {
  loadDocuments();
  loadHealth();
});

/* ---------- chat ---------- */
function addBubble(text, kind) {
  const div = document.createElement("div");
  div.className = `bubble ${kind}`;
  div.textContent = text;
  $("#messages").appendChild(div);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return div;
}

$("#chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#question");
  const question = input.value.trim();
  if (!question) return;

  addBubble(question, "user");
  input.value = "";
  $("#askBtn").disabled = true;
  const thinking = addBubble("جارٍ البحث في قاعدة المعرفة…", "agent");

  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    thinking.textContent = result.answer;

    const warnings = result.validation?.warnings ?? [];
    if (warnings.length) {
      const box = document.createElement("div");
      box.className = "validation";
      box.innerHTML =
        "<h4>ملاحظات التحقق</h4>" +
        warnings.map((w) => `<div class="warn">${escapeHtml(w)}</div>`).join("");
      thinking.appendChild(box);
    }

    if (result.sources.length) {
      const box = document.createElement("div");
      box.className = "sources";
      box.innerHTML =
        "<h4>المصادر</h4>" +
        result.sources
          .map(
            (source) => `
            <div class="source">
              <b>[${source.citation}] ${escapeHtml(source.filename)}</b>
              <span class="tier tier-${source.tier}">${TIER_LABEL[source.tier] || source.tier}</span>
              <span class="sec">Section: ${escapeHtml(source.section_path || source.section || "-")}${source.version ? ` · v${escapeHtml(source.version)}` : ""} · score ${source.score} · ${escapeHtml(source.origin)}</span>
              <span class="ex">${escapeHtml(source.excerpt)}</span>
            </div>`
          )
          .join("");
      thinking.appendChild(box);
    }

    if (result.plan) {
      const meta = document.createElement("div");
      meta.className = "answer-meta";
      const seconds = (result.timings_ms?.total_ms ?? 0) / 1000;
      meta.textContent =
        `intent: ${result.plan.intent} · ${result.plan.wide_retrieval ? "بحث موسّع" : "بحث مركّز"}` +
        ` · ${result.retrieved_chunks} مقاطع · ${seconds.toFixed(1)}s` +
        ` (استرجاع ${((result.timings_ms?.retrieval_ms ?? 0) / 1000).toFixed(1)}s،` +
        ` توليد ${((result.timings_ms?.generation_ms ?? 0) / 1000).toFixed(1)}s)`;
      thinking.appendChild(meta);
    }
    $("#messages").scrollTop = $("#messages").scrollHeight;
  } catch (err) {
    thinking.className = "bubble error";
    thinking.textContent = err.message;
  } finally {
    $("#askBtn").disabled = false;
  }
});

$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#chatForm").requestSubmit();
  }
});

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

async function loadCategories() {
  try {
    const categories = await api("/api/documents/categories");
    $("#categoryList").innerHTML = categories.map((c) => `<option value="${escapeHtml(c)}">`).join("");
  } catch {
    /* categories are a convenience only */
  }
}

loadConfig();
loadHealth();
loadDocuments();
loadCategories();
setInterval(loadHealth, 30000);
