(() => {
  const BACKEND_URL = (window.BACKEND_URL || "").replace(/\/$/, "");

  const fileInput = document.getElementById("file");
  const drop = document.getElementById("drop");
  const dropTitle = document.getElementById("drop-title");
  const dropSub = document.getElementById("drop-sub");
  const submit = document.getElementById("submit");
  const form = document.getElementById("form");
  const status = document.getElementById("status");
  const backendUrlEl = document.getElementById("backend-url");

  backendUrlEl.textContent = BACKEND_URL || "(not configured)";

  let currentFile = null;

  function setFile(f) {
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) {
      showStatus("Please select a PDF file.", "error");
      return;
    }
    currentFile = f;
    dropTitle.textContent = f.name;
    dropSub.textContent = `${(f.size / 1024 / 1024).toFixed(2)} MB · click to change`;
    submit.disabled = false;
    hideStatus();
  }

  function showStatus(msg, kind = "loading") {
    status.textContent = msg;
    status.className = `status ${kind}`;
  }

  function hideStatus() {
    status.className = "status hidden";
    status.textContent = "";
  }

  fileInput.addEventListener("change", (e) => setFile(e.target.files[0]));

  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("dragover");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");
    setFile(e.dataTransfer.files[0]);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentFile) return;
    if (!BACKEND_URL) {
      showStatus("Backend URL not configured. Edit config.js.", "error");
      return;
    }

    const fd = new FormData();
    fd.append("file", currentFile);
    fd.append("cols", document.getElementById("cols").value);
    fd.append("rows_per_page", document.getElementById("rows_per_page").value);
    fd.append("scale", document.getElementById("scale").value);
    fd.append("skip_pages", document.getElementById("skip_pages").value);
    fd.append("divider", document.getElementById("divider").value);

    submit.disabled = true;
    showStatus("Generating… this can take 10–60s for large PDFs.", "loading");

    try {
      const res = await fetch(`${BACKEND_URL}/generate`, {
        method: "POST",
        body: fd,
      });

      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const j = await res.json();
          if (j.detail) detail = j.detail;
        } catch {}
        throw new Error(detail);
      }

      const count = res.headers.get("X-Product-Count");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      const stem = currentFile.name.replace(/\.pdf$/i, "");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${stem}_catalogue.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      showStatus(`Done · ${count || "?"} products extracted.`, "ok");
    } catch (err) {
      showStatus(`Failed: ${err.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  });
})();
