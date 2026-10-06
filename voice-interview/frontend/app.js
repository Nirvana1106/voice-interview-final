"use strict";

const $ = (id) => document.getElementById(id);
const ui = {
  loginView: $("loginView"), workspaceView: $("workspaceView"), loginForm: $("loginForm"),
  loginButton: $("loginButton"), loginMessage: $("loginMessage"), userLabel: $("userLabel"),
  logoutButton: $("logoutButton"), createForm: $("createForm"), createMessage: $("createMessage"),
  startButton: $("startButton"), sampleSelect: $("sampleSelect"), fillSampleButton: $("fillSampleButton"),
  interviewPanel: $("interviewPanel"), interviewTitle: $("interviewTitle"),
  interviewState: $("interviewState"), progressLabel: $("progressLabel"),
  progressDots: $("progressDots"), questionArea: $("questionArea"), questionText: $("questionText"),
  completeArea: $("completeArea"), answerInput: $("answerInput"),
  replayButton: $("replayButton"), stopSpeechButton: $("stopSpeechButton"),
  speechStatus: $("speechStatus"), recordButton: $("recordButton"),
  stopRecordButton: $("stopRecordButton"), recordStatus: $("recordStatus"),
  confirmButton: $("confirmButton"), retryButton: $("retryButton"),
  interviewMessage: $("interviewMessage"), backToSetup: $("backToSetup"),
  viewHistoryButton: $("viewHistoryButton"), historyList: $("historyList"),
  historyDetail: $("historyDetail"), historyMessage: $("historyMessage"),
  refreshHistoryButton: $("refreshHistoryButton"), metricsSummary: $("metricsSummary"),
  metricsMessage: $("metricsMessage"), refreshMetricsButton: $("refreshMetricsButton"),
  toast: $("toast"),
};

const state = {
  user: null, samples: [], interviewId: null, turn: 1, complete: false,
  question: "", streaming: false, savingAnswer: false, recognition: null,
  streamController: null,
  recognitionToken: 0, showSetup: true, timing: null, viewVersion: 0, creating: false,
};
const voice = { session: 0, queue: [], buffer: "", busy: false, timing: null, enabled: false };
let toastTimer;

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method, credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") showLogin();
    const detail = typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试。";
    throw new Error(detail);
  }
  return data;
}

function setMessage(element, message, ok = false) {
  element.textContent = message || "";
  element.classList.toggle("ok", ok);
}

function setBusy(button, busy, busyText = "请稍候…") {
  if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyText : button.dataset.originalText;
}

function toast(message) {
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 4500);
}

function showLogin() {
  state.viewVersion += 1;
  cancelGeneration();
  stopSpeech();
  stopRecognition(true);
  state.user = null;
  state.interviewId = null;
  state.question = "";
  state.complete = false;
  state.showSetup = true;
  state.timing = null;
  ui.questionText.textContent = "";
  ui.answerInput.value = "";
  for (const id of ["role", "jobDescription", "experienceSummary", "password"]) $(id).value = "";
  ui.historyDetail.replaceChildren();
  ui.historyDetail.hidden = true;
  ui.historyList.replaceChildren();
  ui.metricsSummary.replaceChildren();
  ui.interviewMessage.textContent = "";
  ui.historyMessage.textContent = "";
  ui.metricsMessage.textContent = "";
  ui.workspaceView.hidden = true;
  ui.loginView.hidden = false;
  ui.userLabel.hidden = true;
  ui.logoutButton.hidden = true;
}

function showWorkspace(user) {
  state.user = user;
  ui.userLabel.textContent = user.username;
  ui.userLabel.hidden = false;
  ui.logoutButton.hidden = false;
  ui.loginView.hidden = true;
  ui.workspaceView.hidden = false;
}

function openTab(name) {
  state.viewVersion += 1;
  if (name !== "practice") {
    cancelGeneration();
    stopSpeech();
    stopRecognition(true);
  }
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("active", button.dataset.tab === name);
  }
  for (const panel of ["practice", "history", "performance"]) {
    $(panel + "Panel").hidden = panel !== name;
  }
  if (name === "history") void refreshHistory();
  if (name === "performance") void refreshMetrics();
}

function showPracticeContent() {
  ui.createForm.hidden = !state.showSetup;
  ui.interviewPanel.hidden = state.showSetup || !state.interviewId;
}

async function loadSamples() {
  try {
    const response = await fetch("/samples.json", { cache: "no-store" });
    if (!response.ok) return;
    state.samples = await response.json();
    for (const sample of state.samples) {
      const option = document.createElement("option");
      option.value = sample.id;
      option.textContent = sample.label;
      ui.sampleSelect.append(option);
    }
  } catch { /* Samples are optional; manual entry stays available. */ }
}

function fillSample() {
  const sample = state.samples.find((item) => item.id === ui.sampleSelect.value);
  if (!sample) { toast("请先选择一组虚构资料。"); return; }
  $("role").value = sample.role;
  $("jobDescription").value = sample.jobDescription;
  $("experienceSummary").value = sample.experienceSummary;
  setMessage(ui.createMessage, "已填入虚构测试资料；可以按需要修改。", true);
}

function stopSpeech() {
  voice.enabled = false;
  voice.session += 1;
  voice.queue = [];
  voice.buffer = "";
  voice.busy = false;
  voice.timing = null;
  if ("speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch { /* Text remains available. */ }
  }
  ui.speechStatus.textContent = "播报已停止";
}

function beginSpeech(timing) {
  stopSpeech();
  voice.enabled = true;
  voice.timing = timing;
  if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    ui.speechStatus.textContent = "当前浏览器不支持语音播报，问题文字仍可阅读。";
    if (timing) void sendMetric(timing, "浏览器不支持语音播报");
  } else {
    ui.speechStatus.textContent = "收到问题后开始播报…";
  }
}

function pumpSpeech() {
  if (!voice.enabled || voice.busy || voice.queue.length === 0 || !("speechSynthesis" in window)) return;
  const part = voice.queue.shift();
  const session = voice.session;
  let utterance;
  try {
    utterance = new SpeechSynthesisUtterance(part);
  } catch {
    voice.queue = [];
    ui.speechStatus.textContent = "播报不可用，问题文字仍可阅读。";
    if (voice.timing) void sendMetric(voice.timing, "语音播报不可用");
    return;
  }
  utterance.lang = "zh-CN";
  utterance.rate = 1;
  voice.busy = true;
  utterance.onstart = () => {
    if (session !== voice.session) return;
    ui.speechStatus.textContent = "正在播报问题";
    const timing = voice.timing;
    if (timing && timing.speechStartMs === null) {
      timing.speechStartMs = performance.now() - timing.clickAt;
      void sendMetric(timing);
    }
  };
  utterance.onend = () => {
    if (session !== voice.session) return;
    voice.busy = false;
    if (voice.queue.length) pumpSpeech();
    else ui.speechStatus.textContent = "播报已完成";
  };
  utterance.onerror = () => {
    if (session !== voice.session) return;
    voice.busy = false;
    ui.speechStatus.textContent = "播报失败，问题文字仍可阅读。";
    if (voice.timing) void sendMetric(voice.timing, "语音播报失败");
    if (voice.queue.length) pumpSpeech();
  };
  try {
    window.speechSynthesis.speak(utterance);
  } catch {
    voice.busy = false;
    voice.queue = [];
    ui.speechStatus.textContent = "播报失败，问题文字仍可阅读。";
    if (voice.timing) void sendMetric(voice.timing, "语音播报启动失败");
  }
}

function enqueueSpeech(text) {
  if (!voice.enabled || !text.trim() || !("speechSynthesis" in window)) return;
  voice.queue.push(text.trim());
  pumpSpeech();
}

function feedSpeech(text) {
  if (!voice.enabled || !("speechSynthesis" in window)) return;
  voice.buffer += text;
  while (voice.buffer.length) {
    let cut = 0;
    const limit = Math.min(voice.buffer.length, 28);
    for (let index = 9; index < limit; index += 1) {
      if (/[。！？!?；;，,]/u.test(voice.buffer[index])) { cut = index + 1; break; }
    }
    if (!cut && voice.buffer.length >= 28) cut = 28;
    if (!cut) break;
    enqueueSpeech(voice.buffer.slice(0, cut));
    voice.buffer = voice.buffer.slice(cut);
  }
}

function finishSpeech() {
  if (voice.buffer.trim()) enqueueSpeech(voice.buffer);
  voice.buffer = "";
}

function replayQuestion() {
  if (!state.question) return;
  stopRecognition(true);
  beginSpeech(null);
  enqueueSpeech(state.question);
}

function stopRecognition(discard = false) {
  if (discard) state.recognitionToken += 1;
  const recognition = state.recognition;
  if (recognition) {
    try {
      if (discard) recognition.abort();
      else recognition.stop();
    } catch { /* It may already have ended. */ }
    if (discard) state.recognition = null;
  }
  if (discard) {
    ui.recordButton.hidden = false;
    ui.recordButton.classList.remove("recording");
    ui.stopRecordButton.hidden = true;
    ui.recordStatus.textContent = "";
    ui.answerInput.disabled = false;
    ui.confirmButton.disabled = !state.question || state.streaming || state.complete || state.savingAnswer;
  }
}

function startRecognition() {
  if (!state.question || state.streaming || state.complete || state.recognition || state.savingAnswer) return;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    ui.recordStatus.textContent = "当前环境不支持语音识别；请用 Chrome 或 Edge，或在下方输入文字。";
    return;
  }
  stopSpeech();
  stopRecognition(true);
  let recognition;
  try { recognition = new Recognition(); }
  catch {
    ui.recordStatus.textContent = "无法启动语音识别；可直接输入文字。";
    return;
  }
  const token = ++state.recognitionToken;
  const baseline = ui.answerInput.value.trim();
  recognition.lang = "zh-CN";
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onstart = () => {
    if (token !== state.recognitionToken) return;
    ui.recordButton.hidden = true;
    ui.stopRecordButton.hidden = false;
    ui.recordStatus.textContent = "正在通过麦克风识别，请自然作答…";
  };
  recognition.onresult = (event) => {
    if (token !== state.recognitionToken) return;
    let transcript = "";
    for (let index = 0; index < event.results.length; index += 1) {
      transcript += event.results[index][0].transcript;
    }
    ui.answerInput.value = [baseline, transcript.trim()].filter(Boolean).join(" ");
  };
  recognition.onerror = (event) => {
    if (token !== state.recognitionToken) return;
    const reason = event.error === "not-allowed" || event.error === "service-not-allowed"
      ? "麦克风权限被拒绝；可在浏览器地址栏开启权限，或直接输入文字。"
      : `语音识别失败（${event.error || "未知错误"}）；已保留文字，可修改后确认。`;
    ui.recordStatus.textContent = reason;
  };
  recognition.onend = () => {
    if (token !== state.recognitionToken) return;
    state.recognition = null;
    ui.recordButton.hidden = false;
    ui.recordButton.classList.remove("recording");
    ui.stopRecordButton.hidden = true;
    ui.answerInput.disabled = false;
    ui.confirmButton.disabled = !state.question || state.streaming || state.complete || state.savingAnswer;
    if (ui.recordStatus.textContent.startsWith("正在")) {
      ui.recordStatus.textContent = "识别结束；请校对文字后确认。";
    }
  };
  state.recognition = recognition;
  ui.confirmButton.disabled = true;
  ui.answerInput.disabled = true;
  ui.recordButton.hidden = true;
  ui.stopRecordButton.hidden = false;
  try {
    recognition.start();
    ui.recordButton.classList.add("recording");
  } catch {
    state.recognition = null;
    state.recognitionToken += 1;
    ui.answerInput.disabled = false;
    ui.confirmButton.disabled = !state.question || state.streaming || state.complete;
    ui.recordButton.hidden = false;
    ui.stopRecordButton.hidden = true;
    ui.recordStatus.textContent = "无法启动语音识别；请检查浏览器麦克风设置或使用文字输入。";
  }
}

async function sendMetric(timing, browserError = null) {
  if (!timing?.requestId) return;
  try {
    await api("/api/performance/browser", {
      method: "POST",
      body: {
        request_id: timing.requestId,
        first_char_ms: timing.firstCharMs,
        speech_start_ms: timing.speechStartMs,
        browser_error: browserError,
        browser_info: navigator.userAgent.slice(0, 200),
      },
    });
  } catch (error) {
    toast(`性能记录保存失败：${error.message}`);
  }
}

function markFirstChar(timing) {
  if (timing.firstCharMs !== null || timing.firstCharScheduled) return;
  timing.firstCharScheduled = true;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (timing.firstCharMs !== null) return;
    if (state.timing !== timing) return;
    if ($("practicePanel").hidden || ui.interviewPanel.hidden ||
        document.visibilityState === "hidden" || !ui.questionText.textContent.trim()) {
      void sendMetric(timing, "首字呈现时页面不可见或已离开；本次页面测量无效");
      return;
    }
    timing.firstCharMs = performance.now() - timing.clickAt;
    void sendMetric(timing);
  }));
}

function parseEvent(block) {
  let name = "message";
  const data = [];
  for (const line of block.split(/\r?\n/u)) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return { name, payload: data.length ? JSON.parse(data.join("\n")) : {} };
}

function cancelGeneration() {
  if (state.streamController) {
    ui.retryButton.hidden = false;
    setMessage(ui.interviewMessage, "生成已中止；可重试，之前确认的问答已保存。");
  }
  state.streamController?.abort();
  state.streamController = null;
  state.streaming = false;
  state.timing = null;
}

async function generateQuestion(clickAt = performance.now()) {
  if (!state.interviewId || state.complete || state.streaming) return;
  stopRecognition(true);
  stopSpeech();
  state.streaming = true;
  const controller = new AbortController();
  state.streamController = controller;
  state.question = "";
  ui.questionText.textContent = "";
  ui.answerInput.value = "";
  ui.confirmButton.disabled = true;
  ui.replayButton.disabled = true;
  ui.recordButton.disabled = true;
  ui.retryButton.hidden = true;
  setMessage(ui.interviewMessage, "正在连接模型并流式生成当前问题…", true);
  const timing = {
    requestId: null, clickAt, firstCharMs: null, speechStartMs: null,
    firstCharScheduled: false,
  };
  state.timing = timing;
  beginSpeech(timing);
  let finished = false;
  let reader;
  try {
    const response = await fetch(
      `/api/interviews/${state.interviewId}/question/stream?turn=${state.turn}`,
      { credentials: "same-origin", headers: { Accept: "text/event-stream" }, signal: controller.signal },
    );
    if (!response.ok || !response.body) {
      if (response.status === 401) showLogin();
      throw new Error(`无法建立问题流（HTTP ${response.status}）。`);
    }
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      if (controller.signal.aborted || state.streamController !== controller) return;
      if (chunk.done) break;
      // A proxy may use CRLF and split the CR/LF pair across network chunks.
      buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/gu, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (!block.trim()) continue;
        const { name, payload } = parseEvent(block);
        if (name === "meta") {
          timing.requestId = payload.requestId;
          state.turn = payload.turn;
        } else if (name === "delta") {
          ui.questionText.textContent += payload.text || "";
          if (ui.questionText.textContent.trim()) markFirstChar(timing);
          feedSpeech(payload.text || "");
        } else if (name === "done") {
          finished = true;
          state.question = payload.question;
          ui.questionText.textContent = payload.question;
          finishSpeech();
          ui.confirmButton.disabled = false;
          ui.replayButton.disabled = false;
          ui.recordButton.disabled = false;
          setMessage(ui.interviewMessage, "问题已生成。听题后可用麦克风作答，并校对转写。", true);
        } else if (name === "existing") {
          stopSpeech();
          finished = true;
          state.question = payload.question;
          state.turn = payload.turn;
          ui.questionText.textContent = payload.question;
          ui.confirmButton.disabled = false;
          ui.replayButton.disabled = false;
          ui.recordButton.disabled = false;
          setMessage(ui.interviewMessage, "已恢复当前问题。需要时可点击重播。", true);
        } else if (name === "error") {
          throw new Error(payload.message || "生成问题失败，请重试。");
        }
      }
      // "done" and "existing" are terminal events. Do not wait for the
      // connection to close while the answer controls are already enabled.
      if (finished) break;
    }
    if (!finished) throw new Error("问题流提前结束，请重试。已完成的问答仍已保存。");
  } catch (error) {
    if (controller.signal.aborted) return;
    stopSpeech();
    ui.retryButton.hidden = false;
    ui.confirmButton.disabled = true;
    setMessage(ui.interviewMessage, error.message);
    if (timing.requestId) void sendMetric(timing, error.message.slice(0, 200));
  } finally {
    if (state.streamController === controller) {
      state.streamController = null;
      state.streaming = false;
    }
    if (reader) await reader.cancel().catch(() => {});
  }
}

function renderProgress(turn, complete) {
  ui.progressDots.replaceChildren();
  for (let index = 1; index <= 3; index += 1) {
    const dot = document.createElement("span");
    if (complete || index < turn) dot.classList.add("done");
    else if (index === turn) dot.classList.add("current");
    ui.progressDots.append(dot);
  }
  ui.progressLabel.textContent = complete ? "3 / 3 已完成" : `${turn} / 3`;
}

function renderInterview(data) {
  const interview = data.interview;
  const activeRound = data.rounds.find(
    (row) => row.turn === interview.current_turn && row.question_state === "ready",
  );
  state.interviewId = interview.id;
  state.turn = interview.current_turn;
  state.complete = interview.state === "complete";
  state.question = activeRound?.question_text || "";
  state.showSetup = false;
  showPracticeContent();
  ui.interviewTitle.textContent = interview.role;
  ui.interviewState.textContent = state.complete ? "已完成" : "进行中";
  renderProgress(interview.current_turn, state.complete);
  ui.questionArea.hidden = state.complete;
  ui.completeArea.hidden = !state.complete;
  ui.questionText.textContent = state.question || "当前问题尚未生成。";
  ui.answerInput.value = activeRound?.answer_text || "";
  ui.replayButton.disabled = !state.question;
  ui.confirmButton.disabled = !state.question;
  ui.recordButton.disabled = !state.question;
  ui.retryButton.hidden = !!state.question;
  setMessage(ui.interviewMessage, state.complete ? "三轮问答已保存。" : "", true);
}

async function loadInterview(id, { autoGenerate = false, clickAt = performance.now() } = {}) {
  const version = ++state.viewVersion;
  cancelGeneration();
  stopSpeech();
  stopRecognition(true);
  const data = await api(`/api/interviews/${id}`);
  if (version !== state.viewVersion || !state.user) return;
  renderInterview(data);
  openTab("practice");
  if (autoGenerate && !state.complete && !state.question) await generateQuestion(clickAt);
  return data;
}

async function createInterview(event) {
  event.preventDefault();
  if (state.streaming || state.creating || state.savingAnswer) return;
  state.creating = true;
  const version = state.viewVersion;
  const clickAt = performance.now();
  setBusy(ui.startButton, true, "正在创建…");
  setMessage(ui.createMessage, "");
  try {
    const interview = await api("/api/interviews", {
      method: "POST",
      body: {
        role: $("role").value.trim(),
        job_description: $("jobDescription").value.trim(),
        experience_summary: $("experienceSummary").value.trim(),
      },
    });
    if (version !== state.viewVersion || !state.user) return;
    await loadInterview(interview.id, { autoGenerate: true, clickAt });
    void refreshHistory();
  } catch (error) {
    setMessage(ui.createMessage, error.message);
  } finally {
    state.creating = false;
    setBusy(ui.startButton, false);
  }
}

async function confirmAnswer() {
  if (!state.question || state.savingAnswer || state.streaming || state.recognition) return;
  const version = state.viewVersion;
  const interviewId = state.interviewId;
  const text = ui.answerInput.value.trim();
  if (!text) { setMessage(ui.interviewMessage, "请先录音转写或输入回答，并确认文字。" ); return; }
  const clickAt = performance.now();
  state.savingAnswer = true;
  stopSpeech();
  stopRecognition(true);
  setBusy(ui.confirmButton, true, "正在保存…");
  try {
    const result = await api(`/api/interviews/${interviewId}/answers`, {
      method: "POST", body: { turn: state.turn, text },
    });
    if (version !== state.viewVersion || !state.user) return;
    const loaded = await loadInterview(interviewId);
    if (!loaded) return;
    if (result.state === "complete") {
      toast("三轮面试已完成，完整问答可以在历史记录中查看。" );
    } else {
      await generateQuestion(clickAt);
    }
    void refreshHistory();
  } catch (error) {
    setMessage(ui.interviewMessage, error.message);
  } finally {
    state.savingAnswer = false;
    setBusy(ui.confirmButton, false);
    ui.confirmButton.disabled = !state.question || state.complete || state.streaming;
  }
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

async function refreshHistory() {
  const userId = state.user?.id;
  setMessage(ui.historyMessage, "正在加载历史记录…", true);
  try {
    const interviews = await api("/api/interviews");
    if (!userId || state.user?.id !== userId) return [];
    ui.historyList.replaceChildren();
    if (!interviews.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "还没有面试记录。填写岗位和经历，开始第一场练习。";
      ui.historyList.append(empty);
    }
    for (const interview of interviews) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = interview.role;
      const subtitle = document.createElement("small");
      subtitle.textContent = `${formatDate(interview.created_at)} · ${interview.state === "complete" ? "已完成" : `进行到第 ${interview.current_turn} 轮`}`;
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "→";
      text.append(title, subtitle);
      button.append(text, arrow);
      button.addEventListener("click", () => void selectHistory(interview));
      ui.historyList.append(button);
    }
    setMessage(ui.historyMessage, "");
    return interviews;
  } catch (error) {
    setMessage(ui.historyMessage, error.message);
    return [];
  }
}

async function selectHistory(interview) {
  const version = ++state.viewVersion;
  const clickAt = performance.now();
  try {
    if (interview.state !== "complete") {
      await loadInterview(interview.id, { autoGenerate: false, clickAt });
      return;
    }
    const data = await api(`/api/interviews/${interview.id}`);
    if (version !== state.viewVersion || !state.user) return;
    renderHistoryDetail(data);
  } catch (error) {
    setMessage(ui.historyMessage, error.message);
  }
}

function renderHistoryDetail(data) {
  ui.historyDetail.replaceChildren();
  ui.historyDetail.hidden = false;
  const title = document.createElement("h3");
  title.textContent = data.interview.role;
  const meta = document.createElement("p");
  meta.className = "muted small";
  meta.textContent = `${formatDate(data.interview.created_at)} · ${data.interview.state === "complete" ? "已完成" : "进行中"}`;
  ui.historyDetail.append(title, meta);
  for (const [label, value] of [
    ["岗位描述", data.interview.job_description],
    ["经历摘要", data.interview.experience_summary],
  ]) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = label;
    const content = document.createElement("p");
    content.textContent = value || "";
    details.append(summary, content);
    ui.historyDetail.append(details);
  }
  for (const round of data.rounds) {
    if (round.question_state !== "ready") continue;
    const section = document.createElement("section");
    section.className = "round-review";
    const number = document.createElement("span");
    number.className = "round-num";
    number.textContent = `ROUND ${round.turn}`;
    const question = document.createElement("p");
    question.textContent = round.question_text;
    const answer = document.createElement("p");
    answer.className = "answer";
    answer.textContent = round.answer_text || "本轮尚未确认回答。";
    section.append(number, question, answer);
    ui.historyDetail.append(section);
  }
}

function metricCard(label, value) {
  const card = document.createElement("div");
  card.className = "metric-card";
  const caption = document.createElement("span");
  caption.textContent = label;
  const number = document.createElement("strong");
  number.textContent = value;
  card.append(caption, number);
  return card;
}

function ms(value) { return value === null ? "—" : `${value.toFixed(0)} ms`; }

async function refreshMetrics() {
  const userId = state.user?.id;
  setMessage(ui.metricsMessage, "正在读取真实记录…", true);
  try {
    const data = await api("/api/performance");
    if (!userId || state.user?.id !== userId) return;
    ui.metricsSummary.replaceChildren(
      metricCard("成功 / 已结束", `${data.success} / ${data.totalFinished}`),
      metricCard("成功率", data.successRate === null ? "—" : `${(data.successRate * 100).toFixed(1)}%`),
      metricCard(`模型 TTFT P50 · ${data.modelSamples}/${data.success}`, ms(data.modelP50Ms)),
      metricCard(`模型 TTFT P95 · ${data.modelSamples}/${data.success}`, ms(data.modelP95Ms)),
      metricCard(`页面首字 P95 · ${data.firstCharSamples}/${data.success}`, ms(data.firstCharP95Ms)),
      metricCard(`首段语音 P95 · ${data.speechSamples}/${data.success}`, ms(data.speechStartP95Ms)),
    );
    const verdict = data.evidenceComplete
      ? (data.thresholdsPassed ? "样本齐全，当前记录达到门槛；仍需录屏核对。" : "样本齐全，但当前记录未达到全部门槛。")
      : "证据尚未齐全：至少需要 20 次真实请求，覆盖首题和追问，且每次成功请求都有三项延迟。";
    setMessage(ui.metricsMessage,
      `${verdict} 失败 ${data.failure} 次，重试 ${data.retries} 次；首题 ${data.firstQuestions} 次，追问 ${data.followUps} 次。`,
      data.thresholdsPassed,
    );
  } catch (error) {
    setMessage(ui.metricsMessage, error.message);
  }
}

async function login(event) {
  event.preventDefault();
  setBusy(ui.loginButton, true, "正在登录…");
  setMessage(ui.loginMessage, "");
  try {
    const user = await api("/api/login", {
      method: "POST",
      body: { username: $("username").value, password: $("password").value },
    });
    showWorkspace(user);
    const interviews = await refreshHistory();
    const active = interviews.find((item) => item.state === "active");
    if (active) await loadInterview(active.id);
    else { state.showSetup = true; showPracticeContent(); openTab("practice"); }
  } catch (error) {
    setMessage(ui.loginMessage, error.message);
  } finally {
    setBusy(ui.loginButton, false);
  }
}

async function boot() {
  showLogin();
  await loadSamples();
  try {
    const user = await api("/api/me");
    showWorkspace(user);
    const interviews = await refreshHistory();
    const active = interviews.find((item) => item.state === "active");
    if (active) await loadInterview(active.id);
    else { state.showSetup = true; showPracticeContent(); openTab("practice"); }
  } catch { showLogin(); }
}

ui.loginForm.addEventListener("submit", login);
ui.createForm.addEventListener("submit", createInterview);
ui.fillSampleButton.addEventListener("click", fillSample);
ui.replayButton.addEventListener("click", replayQuestion);
ui.stopSpeechButton.addEventListener("click", stopSpeech);
ui.recordButton.addEventListener("click", startRecognition);
ui.stopRecordButton.addEventListener("click", () => stopRecognition(false));
ui.confirmButton.addEventListener("click", confirmAnswer);
ui.retryButton.addEventListener("click", () => void generateQuestion(performance.now()));
ui.refreshHistoryButton.addEventListener("click", () => void refreshHistory());
ui.refreshMetricsButton.addEventListener("click", () => void refreshMetrics());
ui.viewHistoryButton.addEventListener("click", () => openTab("history"));
ui.backToSetup.addEventListener("click", () => {
  state.viewVersion += 1;
  cancelGeneration();
  stopSpeech(); stopRecognition(true); state.interviewId = null;
  state.showSetup = true; showPracticeContent();
});
ui.logoutButton.addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
    showLogin();
  } catch (error) {
    toast(`退出失败：${error.message}`);
  }
});
for (const button of document.querySelectorAll(".tab")) {
  button.addEventListener("click", () => openTab(button.dataset.tab));
}
void boot();
