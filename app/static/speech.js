"use strict";
(() => {
  const state = {
    file: null, objectURL: null, text: "", title: "", original: "", result: null,
    model: "base", language: "zh", privacy: 1, entryUUID: null, savedUUID: null,
    busy: false, recording: false, recorder: null, stream: null, chunks: [],
    bytes: 0, started: 0, timer: null, poll: null, status: null, sequence: 0, audioHash: null,
  };
  const inPage = () => location.hash.split("?")[0] === "#speech-capture";
  const node = (id) => document.getElementById(id);
  const message = (value) => { if (node("speech-message")) node("speech-message").textContent = value; };
  const elapsed = () => Math.floor((Date.now() - state.started) / 1000);
  const timeText = (seconds) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const dependenciesReady = () => Boolean(state.status?.dependencies?.available);
  const modelReady = () => Boolean(state.status?.models?.find((model) => model.id === state.model)?.ready);

  function controls() {
    if (!inPage() || !node("speech-file")) return;
    node("speech-record").disabled = state.busy || state.recording || Boolean(state.file) || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder;
    node("speech-stop").disabled = !state.recording;
    node("speech-file").disabled = state.busy || state.recording || Boolean(state.file);
    node("speech-transcribe").disabled = state.busy || state.recording || !state.file || !state.file.size || state.file.size > 25 * 1024 * 1024 || !dependenciesReady() || !modelReady() || Boolean(state.entryUUID);
    node("speech-prepare").disabled = state.busy || !dependenciesReady() || state.status?.phase === "preparing" || state.status?.phase === "transcribing" || modelReady();
    node("speech-clear").disabled = state.busy || state.recording || !state.file;
    node("speech-save").disabled = state.busy || state.recording || !state.file || !state.file.size || state.file.size > 25 * 1024 * 1024 || !state.text.trim() || !node("speech-confirm").checked;
    node("speech-save").textContent = state.savedUUID ? "重试保存原始录音" : "确认文字，保存原始录音与记录";
    ["speech-text", "speech-title", "speech-privacy", "speech-model", "speech-language"].forEach((id) => {
      node(id).disabled = state.busy || state.recording || Boolean(state.entryUUID);
    });
  }

  function showFile() {
    if (!inPage() || !node("speech-audio")) return;
    const audio = node("speech-audio");
    audio.hidden = !state.file;
    node("speech-file-info").textContent = state.file ? `${state.file.name} · ${(state.file.size / 1024 / 1024).toFixed(2)} MB` : "尚未选择录音";
    if (state.file) {
      if (!state.objectURL) state.objectURL = URL.createObjectURL(state.file);
      audio.src = state.objectURL;
      node("speech-download").href = state.objectURL;
      node("speech-download").download = state.file.name;
    }
    node("speech-download").hidden = !state.file;
    controls();
  }

  function setFile(file) {
    if (!file) return;
    state.sequence += 1;
    if (state.objectURL) URL.revokeObjectURL(state.objectURL);
    state.file = file;
    state.objectURL = null;
    state.original = "";
    state.result = null;
    state.entryUUID = null;
    state.savedUUID = null;
    state.audioHash = null;
    node("speech-confirm") && (node("speech-confirm").checked = false);
    showFile();
    message(file.size > 25 * 1024 * 1024 ? "录音超过 25 MB，请下载保留后分成较小文件。" : "录音暂留当前浏览器。点击本机转写，或直接手动填写正文后确认保存。可以先下载保留原音频。");
  }

  function stopRecording() {
    clearInterval(state.timer);
    state.timer = null;
    if (state.recorder?.state === "recording") state.recorder.stop();
    state.stream?.getTracks().forEach((track) => track.stop());
    state.stream = null;
    state.recording = false;
    controls();
  }

  async function startRecording() {
    if (state.file || state.recording || state.busy) return;
    state.busy = true;
    controls();
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!inPage()) { state.stream.getTracks().forEach((track) => track.stop()); state.stream = null; return; }
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((value) => MediaRecorder.isTypeSupported(value));
      state.recorder = new MediaRecorder(state.stream, type ? { mimeType: type } : {});
      state.chunks = [];
      state.bytes = 0;
      state.started = Date.now();
      state.recorder.ondataavailable = (event) => {
        if (!event.data.size) return;
        state.chunks.push(event.data);
        state.bytes += event.data.size;
        if (state.bytes >= 25 * 1024 * 1024) stopRecording();
      };
      state.recorder.onstop = () => {
        const mime = state.recorder.mimeType || "audio/webm";
        const extension = mime.includes("mp4") ? "m4a" : "webm";
        const stamp = new Date().toISOString().replace(/[:.]/g, "-");
        const file = new File(state.chunks, `录音-${stamp}.${extension}`, { type: mime });
        state.chunks = [];
        state.recorder = null;
        setFile(file);
      };
      state.recorder.onerror = () => { stopRecording(); message("录音发生错误，请检查麦克风或改用上传录音。"); };
      state.recorder.start(1000);
      state.recording = true;
      state.timer = setInterval(() => {
        message(`正在录音 ${timeText(elapsed())} · 最长 10 分钟。点击停止后核对文字。`);
        if (elapsed() >= 600) stopRecording();
      }, 1000);
      message("正在录音 0:00 · 点击停止后核对文字。");
    } catch (error) {
      state.stream?.getTracks().forEach((track) => track.stop());
      state.stream = null;
      message(error.name === "NotAllowedError" ? "未获得麦克风权限。可在浏览器允许麦克风，或上传已有录音。" : "无法开始录音，请检查麦克风，或上传已有录音。");
    } finally {
      state.busy = false;
      controls();
    }
  }

  async function refreshStatus() {
    try {
      state.status = await api("/api/speech/status");
      if (!inPage() || !node("speech-status")) return;
      const phase = state.status.phase;
      const ready = modelReady();
      const text = !dependenciesReady() ? "尚未安装语音组件 · 可先录音、下载或手工填写文字。" :
        phase === "preparing" ? "正在下载本机模型，完成后才能转写。" :
        phase === "transcribing" ? "正在本机转写录音。" : ready ? "本机语音模型已准备，可以离线转写。" : "语音组件已安装，模型尚未准备。";
      node("speech-status").textContent = state.status.error ? `${text} ${state.status.error}` : text;
      controls();
      clearTimeout(state.poll);
      state.poll = null;
      if (["preparing", "transcribing"].includes(phase)) state.poll = setTimeout(refreshStatus, 3000);
    } catch (error) {
      if (inPage()) message(error.message);
    }
  }

  async function prepareModel() {
    message("首次准备会从模型官方仓库下载到本机数据目录；录音不会参与模型下载。");
    state.busy = true;
    controls();
    try {
      await api("/api/speech/prepare", { method: "POST", body: { model: state.model } });
      await refreshStatus();
    } catch (error) { message(error.message); }
    finally { state.busy = false; controls(); }
  }

  async function transcribe() {
    if (!state.file || state.busy) return;
    const sequence = state.sequence;
    const preserveManualText = Boolean(state.text.trim() && state.text !== state.original);
    state.busy = true;
    controls();
    message("正在本机转写。CPU 处理可能需要几分钟；原始录音保留在此页，尚未归档。");
    const form = new FormData();
    form.append("file", state.file, state.file.name);
    form.append("model", state.model);
    form.append("language", state.language);
    try {
      const result = await api("/api/speech/transcribe", { method: "POST", body: form });
      if (sequence !== state.sequence) return;
      state.result = result;
      state.original = result.text;
      if (!preserveManualText) state.text = result.text;
      if (node("speech-text")) node("speech-text").value = state.text;
      if (node("speech-confirm")) node("speech-confirm").checked = false;
      if (node("speech-original")) node("speech-original").textContent = result.text || "未识别到有效语音";
      message(result.text ? (preserveManualText ? "转写完成，已保留你编辑的正文。新机器结果在“查看机器原始转写”中，可复制需要的内容后确认保存。" : `转写完成 · ${timeText(Math.round(result.duration))}。请对照录音检查并修改，再勾选确认保存。`) : "未识别到有效语音。可以手工填写，或换一段更清晰的录音。");
    } catch (error) { message(error.message); }
    finally { state.busy = false; controls(); await refreshStatus(); }
  }

  async function save() {
    if (!state.file || !state.text.trim() || !node("speech-confirm")?.checked || state.busy) return;
    if (!state.file.size || state.file.size > 25 * 1024 * 1024) { message("请选择有内容且不超过 25 MB 的原始录音。"); return; }
    if (state.text.length > 2_000_000) { message("正文过长，请缩短后再确认保存。"); return; }
    state.busy = true;
    const content = state.text.trim();
    state.entryUUID ||= crypto.randomUUID();
    controls();
    try {
      if (!state.audioHash) {
        const bytes = await state.file.arrayBuffer();
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        state.audioHash = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
      }
      if (!state.savedUUID) {
        const payload = {
          uuid: state.entryUUID, title: state.title.trim() || content.split("\n")[0].slice(0, 100),
          content, item_type: "inbox", origin: "file", privacy_level: Number(state.privacy),
          source: "本机语音录入", notes: "原始录音见附件；机器原始转写保存在扩展字段 speech.original_transcript。",
          metadata_json: { speech: { original_transcript: state.original, engine: state.result?.engine || "manual",
            model: state.result?.model || null, duration: state.result?.duration || null,
            filename: state.file.name, audio_sha256: state.audioHash, reviewed_by_user: true } },
        };
        try {
          const entry = await api("/api/entries", { method: "POST", body: payload });
          state.savedUUID = entry.uuid;
        } catch (error) {
          // A timed-out successful request can be retried with the same UUID.
          if (error.status !== 409) throw error;
          const entry = await api(`/api/entries/${state.entryUUID}`);
          if (entry.content !== content || entry.item_type !== "inbox") throw Error("保存标识发生冲突，请保留录音并检查原记录。");
          state.savedUUID = entry.uuid;
        }
      }
      const existing = await api(`/api/entries/${state.savedUUID}`);
      if (!existing.attachments?.some((item) => item.sha256 === state.audioHash && item.file_size === state.file.size)) {
        const attachment = new FormData();
        attachment.append("file", state.file, state.file.name);
        attachment.append("description", "原始录音，供核对语音转写");
        await api(`/api/entries/${state.savedUUID}/attachments`, { method: "POST", body: attachment });
      }
      const saved = state.savedUUID;
      clear();
      toast("已保存核对后的文字与原始录音到收集箱");
      location.hash = `item/${saved}`;
    } catch (error) {
      if (!state.savedUUID && [400, 422].includes(error.status)) state.entryUUID = null;
      message(state.savedUUID ? `文字已保存，原始录音未保存成功：${error.message}。当前录音仍保留，请点击重试保存原始录音。` : `保存未完成：${error.message}。内容和录音仍保留，请重试。`);
    } finally { state.busy = false; controls(); }
  }

  function clear() {
    if (state.objectURL) URL.revokeObjectURL(state.objectURL);
    state.sequence += 1;
    Object.assign(state, { file: null, objectURL: null, text: "", title: "", original: "", result: null, entryUUID: null, savedUUID: null, audioHash: null });
    if (node("speech-text")) node("speech-text").value = "";
    if (node("speech-title")) node("speech-title").value = "";
    if (node("speech-original")) node("speech-original").textContent = "转写完成后在这里保留机器原文。";
    if (node("speech-confirm")) node("speech-confirm").checked = false;
    if (node("speech-file")) node("speech-file").value = "";
    showFile();
  }

  window.speechCapturePage = async () => {
    $("#main").innerHTML = heading("语音随手记", "录音或上传音频，本机转写，由我修改并确认后保存。", '<a class="ai-link-button" href="#inbox">收集箱 →</a>') +
      `<div class="speech-columns"><section class="panel"><h2>① 留下原始录音</h2><p class="muted">单个不超过 25 MB、最长 10 分钟。只有点击开始录音才会使用麦克风。</p><div class="actions"><button id="speech-record" class="primary">开始录音</button><button id="speech-stop" disabled>停止录音</button></div><label>或选择已有录音<input id="speech-file" type="file" accept="audio/*,.webm,.m4a,.mp4"></label><p id="speech-file-info" class="muted"></p><audio id="speech-audio" controls hidden></audio><div class="actions"><a id="speech-download" hidden>下载保留原音频</a><button id="speech-clear" class="small">清空本页录音与文字</button></div><div id="speech-message" class="speech-message" role="status" aria-live="polite">录音和文字暂留当前浏览器；确认保存前不要关闭此标签页。</div><details><summary>本机语音模型</summary><p id="speech-status" role="status">正在读取状态…</p><label>模型<select id="speech-model"><option value="base">base · 下载约 148 MB</option><option value="small">small · 下载约 486 MB</option></select></label><label>录音语言<select id="speech-language"><option value="zh">中文</option><option value="auto">自动检测</option></select></label><button id="speech-prepare">准备所选本机模型（首次联网下载）</button><p class="muted">本机模型放在数据目录的 models/speech。转写不调用云端。依赖未安装时需先安装语音组件：</p><code class="speech-install">.venv/Scripts/python.exe -m pip install -r requirements-speech.txt</code><p class="muted">安装后重新启动数据库。系统不会自动执行安装命令。</p></details></section><section class="panel"><h2>② 核对文字，再保存</h2><button id="speech-transcribe" class="primary">本机转写此录音</button><label>标题（可选）<input id="speech-title" maxlength="500" value="${esc(state.title)}"></label><label>正文<textarea id="speech-text" rows="14" placeholder="转写后核对姓名、日期、数字与事件，也可直接手动填写。">${esc(state.text)}</textarea></label><details><summary>查看机器原始转写</summary><p id="speech-original" class="speech-original">${esc(state.original || "转写完成后在这里保留机器原文。")}</p></details><label>隐私级别<select id="speech-privacy"><option value="1">普通</option><option value="2">私密</option><option value="3">敏感</option></select></label><label class="speech-confirm"><input id="speech-confirm" type="checkbox">我已对照录音检查，确认保存当前文字和原始录音</label><button id="speech-save" class="primary">确认文字，保存原始录音与记录</button><p class="muted">保存后进入收集箱，可继续每日 AI 整理与人工审核。原音频与机器原始转写保留作依据。</p></section></div>`;
    node("speech-model").value = state.model;
    node("speech-language").value = state.language;
    node("speech-privacy").value = String(state.privacy);
    node("speech-record").onclick = startRecording;
    node("speech-stop").onclick = stopRecording;
    node("speech-file").onchange = (event) => setFile(event.target.files[0]);
    node("speech-clear").onclick = () => { clear(); message("本页录音与文字已清空，已保存的记录不会被删除。"); };
    node("speech-prepare").onclick = prepareModel;
    node("speech-transcribe").onclick = transcribe;
    node("speech-save").onclick = save;
    node("speech-model").onchange = (event) => { state.model = event.target.value; controls(); refreshStatus(); };
    node("speech-language").onchange = (event) => { state.language = event.target.value; };
    node("speech-privacy").onchange = (event) => { state.privacy = Number(event.target.value); };
    node("speech-text").oninput = (event) => { state.text = event.target.value; node("speech-confirm").checked = false; controls(); };
    node("speech-title").oninput = (event) => { state.title = event.target.value; node("speech-confirm").checked = false; controls(); };
    node("speech-confirm").onchange = controls;
    showFile();
    controls();
    await refreshStatus();
  };
  window.speechCaptureStop = () => { stopRecording(); clearTimeout(state.poll); state.poll = null; };
  window.addEventListener("hashchange", () => { if (!inPage()) window.speechCaptureStop(); });
  window.addEventListener("beforeunload", (event) => {
    if (state.recording || state.file) { event.preventDefault(); event.returnValue = ""; }
    stopRecording();
  });
  window.SpeechCaptureUI = { state, stop: window.speechCaptureStop };
})();
