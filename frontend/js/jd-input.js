/* Reusable existing, pasted, or uploaded job-description input. */
(function () {
  const ALLOWED = [".pdf", ".docx", ".txt", ".md"];
  const MAX = 5 * 1048576;
  let nextName = 0;

  function mount(host, options = {}) {
    const name = "jd-mode-" + (++nextName);
    host.innerHTML = `<div class="jd-input">
      <h3>${PX.esc(options.title || "Job description")}</h3>
      <div class="seg" role="radiogroup" aria-label="Job description source">
        <label><input type="radio" name="${name}" value="existing" checked><span>Choose existing</span></label>
        <label><input type="radio" name="${name}" value="text"><span>Paste text</span></label>
        <label><input type="radio" name="${name}" value="file"><span>Upload file</span></label>
      </div>
      <label class="field jd-existing"><span>Existing job description</span><select aria-label="Existing job description"></select></label>
      <label class="field jd-text" hidden><span>Paste job description</span><textarea maxlength="50000" placeholder="Paste the job description here..."></textarea></label>
      <div class="jd-file" hidden>
        <div class="drop" tabindex="0" role="button" aria-label="Choose a job description file">
          <div class="ico">Upload</div><b>Drop a job description file here</b>
          <p>or <u>browse files</u> · PDF, DOCX, TXT or MD · up to 5 MB</p>
          <input type="file" accept=".pdf,.docx,.txt,.md" hidden>
        </div>
        <div class="file-prev" hidden><span class="ico">File</span><div><b></b><small class="muted"></small></div><button type="button" class="btn ghost sm">Remove</button></div>
      </div>
      <small class="jd-hint muted" role="status"></small>
    </div>`;

    const query = selector => host.querySelector(selector);
    const select = query("select"), text = query("textarea"), fileBox = query(".jd-file");
    const drop = query(".drop"), input = query('input[type="file"]'), preview = query(".file-prev");
    const existingBox = query(".jd-existing"), textBox = query(".jd-text");
    const hint = query(".jd-hint");
    let file = null;

    API.jobDescriptions().then(items => {
      select.innerHTML = items.length
        ? items.map(item => `<option value="${item.id}">${item.company_name ? PX.esc(item.company_name) + " · " : ""}${PX.esc(item.role_title)}</option>`).join("")
        : '<option value="">No existing job descriptions</option>';
      if (!items.length) {
        query('input[value="existing"]').disabled = true;
        query('input[value="text"]').checked = true;
        hint.textContent = "No existing job descriptions; paste one or upload a file.";
        toggle();
      }
    }).catch(error => { hint.textContent = error.message; });

    function method() {
      return host.querySelector(`input[name="${name}"]:checked`).value;
    }

    function toggle() {
      const value = method();
      existingBox.hidden = value !== "existing";
      textBox.hidden = value !== "text";
      fileBox.hidden = value !== "file";
    }

    host.querySelectorAll(`input[name="${name}"]`).forEach(radio => radio.addEventListener("change", toggle));

    function setFile(value) {
      if (!value) {
        file = null;
        input.value = "";
        preview.hidden = true;
        drop.hidden = false;
        return;
      }
      const ext = "." + value.name.split(".").pop().toLowerCase();
      if (!ALLOWED.includes(ext)) return PX.toast("Unsupported file type. Use PDF, DOCX, TXT or MD.", "error");
      if (!value.size) return PX.toast("That file is empty.", "error");
      if (value.size > MAX) return PX.toast("That file is over the 5 MB limit.", "error");
      file = value;
      preview.querySelector("b").textContent = value.name;
      preview.querySelector("small").textContent = PX.fmtBytes(value.size);
      preview.hidden = false;
      drop.hidden = true;
    }

    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", () => setFile(input.files[0]));
    ["dragover", "dragenter"].forEach(type => drop.addEventListener(type, event => {
      event.preventDefault();
      drop.classList.add("over");
    }));
    ["dragleave", "drop"].forEach(type => drop.addEventListener(type, event => {
      event.preventDefault();
      drop.classList.remove("over");
    }));
    drop.addEventListener("drop", event => setFile(event.dataTransfer.files[0]));
    preview.querySelector("button").addEventListener("click", () => setFile(null));

    return {
      isFilled() {
        if (method() === "existing") return Boolean(select.value);
        if (method() === "text") return Boolean(text.value.trim());
        return Boolean(file);
      },
      async resolve() {
        if (method() === "existing") {
          if (!select.value) throw new Error("Choose an existing job description.");
          return { id: Number(select.value) };
        }
        if (method() === "text") {
          if (!text.value.trim()) throw new Error("Paste the job description first.");
          return API.createJd({ text: text.value });
        }
        if (!file) throw new Error("Choose a job description file first.");
        return API.createJd({ file });
      }
    };
  }

  window.JDInput = { mount };
})();
