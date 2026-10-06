/* Test doubles verify application logic, not real microphone/TTS or performance. */
async function runTests(source) {
  const results = [];
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  function setup() {
    const elements = new Map();
    function element(id) {
      if (!elements.has(id)) elements.set(id, {
        hidden: false, disabled: false, value: "", textContent: "", dataset: {}, children: [],
        classList: { add() {}, remove() {}, toggle() {} },
        addEventListener() {}, append(...items) { this.children.push(...items); },
        replaceChildren(...items) { this.children = items; },
      });
      return elements.get(id);
    }
    const document = { getElementById: element, querySelectorAll: () => [],
      createElement: () => element(Symbol()), visibilityState: "visible" };
    const speech = [];
    class Utterance { constructor(text) { this.text = text; } }
    class Recognition {
      start() { this.onstart?.(); }
      stop() { this.stopped = true; }
      abort() { this.aborted = true; }
    }
    const window = { SpeechSynthesisUtterance: Utterance, SpeechRecognition: Recognition,
      speechSynthesis: { speak(u) { speech.push(u); }, cancel() {} } };
    const frames = [];
    let fetcher = async () => { throw Error("unexpected network call"); };
    const api = new Function("document", "window", "SpeechSynthesisUtterance", "fetch",
      "requestAnimationFrame", "performance", "navigator", "setTimeout", "clearTimeout",
      "AbortController", "TextDecoder",
      source.replace(/void boot\(\);\s*$/, "") + `
      return { state, ui, voice, parseEvent, beginSpeech, feedSpeech, finishSpeech,
        stopSpeech, startRecognition, stopRecognition, markFirstChar, showLogin,
        loadInterview, createInterview, confirmAnswer, refreshHistory, renderInterview,
        generateQuestion, cancelGeneration, openTab };
      `)(document, window, Utterance, (...args) => fetcher(...args),
      (callback) => { frames.push(callback); }, { now: () => 100 },
      { userAgent: "automated-test-double" }, () => 1, () => {},
      class { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; } },
      class { decode(value) { return value; } });
    return { ...api, document, window, speech, frames, element,
      fetchWith(fn) { fetcher = fn; },
      flushFrames() { while (frames.length) frames.shift()(); },
    };
  }
  async function test(name, fn) {
    try { await fn(setup()); results.push({ name, status: "passed" }); }
    catch (error) { results.push({ name, status: "failed", error: String(error) }); }
  }
  function ready(h) {
    h.state.user = { id: "alice" };
    h.state.question = "请介绍这个项目？";
    h.state.interviewId = "interview-1";
    h.state.turn = 1;
  }
  function detail(id = "interview-1") {
    return { interview: { id, role: "开发", current_turn: 1, state: "active" }, rounds: [
      { turn: 1, question_state: "ready", question_text: "已保存的问题", answer_text: null },
    ] };
  }
  const response = (data) => ({ ok: true, json: async () => data });
  const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

  await test("SSE retains Unicode and ignores comments", (h) => {
    const e = h.parseEvent(': heartbeat\r\nevent: delta\r\ndata: {"text":"具体取舍？"}');
    assert(e.name === "delta" && e.payload.text === "具体取舍？", "invalid event");
  });
  await test("Partial model output begins speech before completion", (h) => {
    h.beginSpeech(null);
    h.feedSpeech("请详细介绍你如何设计预约系统中的并发控制，以及当时考虑过的其他方案。");
    assert(h.speech.length === 1, "first segment did not start");
    assert(h.voice.buffer.length > 0 || h.voice.queue.length > 0, "not segmented");
  });
  await test("Speech segments never overlap", (h) => {
    h.beginSpeech(null); h.feedSpeech("甲".repeat(70)); h.finishSpeech();
    assert(h.speech.length === 1, "overlapping segments");
    h.speech[0].onend();
    assert(h.speech.length === 2, "queue did not advance");
  });
  await test("Stop blocks all later streaming speech", (h) => {
    h.beginSpeech(null); h.feedSpeech("甲".repeat(40)); h.stopSpeech();
    h.feedSpeech("乙".repeat(60)); h.finishSpeech();
    h.speech[0].onend();
    assert(h.speech.length === 1, "speech restarted");
  });
  await test("Stop before first delta stays stopped", (h) => {
    h.beginSpeech(null); h.stopSpeech(); h.feedSpeech("甲".repeat(70)); h.finishSpeech();
    assert(h.speech.length === 0, "speech restarted");
  });
  await test("Microphone disables editing/confirmation until final result", async (h) => {
    ready(h); h.ui.answerInput.value = "原有文字"; h.startRecognition();
    const recognition = h.state.recognition;
    assert(h.ui.confirmButton.disabled && h.ui.answerInput.disabled, "unsafe controls");
    await h.confirmAnswer();
    h.stopRecognition(false);
    assert(h.state.recognition === recognition, "recognizer discarded before final result");
    recognition.onresult({ results: [[{ transcript: "最后一段" }]] });
    recognition.onend();
    assert(h.ui.answerInput.value === "原有文字 最后一段", "final transcript lost");
    assert(!h.ui.confirmButton.disabled && !h.ui.answerInput.disabled, "controls not restored");
  });
  await test("Old recognition cannot overwrite a new round", (h) => {
    ready(h); h.startRecognition(); const old = h.state.recognition;
    h.stopRecognition(true); h.ui.answerInput.value = "新回答";
    old.onresult({ results: [[{ transcript: "过期回调" }]] }); old.onend();
    assert(old.aborted && h.ui.answerInput.value === "新回答", "stale transcript applied");
  });
  await test("Recognition constructor failure preserves text fallback", (h) => {
    ready(h); h.window.SpeechRecognition = class { constructor() { throw Error("unsupported"); } };
    h.startRecognition();
    assert(!h.ui.answerInput.disabled && h.state.recognition === null, "fallback unavailable");
  });
  await test("Recognition start failure restores controls", (h) => {
    ready(h); h.window.SpeechRecognition = class { start() { throw Error("permission"); } };
    h.startRecognition();
    assert(!h.ui.answerInput.disabled && !h.ui.confirmButton.disabled, "controls stuck");
    assert(!h.ui.recordButton.hidden && h.ui.stopRecordButton.hidden, "record UI stuck");
  });
  await test("Microphone rejection displays explicit fallback", (h) => {
    ready(h); h.startRecognition(); const recognition = h.state.recognition;
    recognition.onerror({ error: "not-allowed" }); recognition.onend();
    assert(h.ui.recordStatus.textContent.includes("权限被拒绝") && !h.ui.answerInput.disabled, "missing feedback");
  });
  await test("Logout clears previous account text and history", (h) => {
    ready(h); h.ui.answerInput.value = "私人回答"; h.ui.historyDetail.append("私人历史");
    h.showLogin();
    assert(!h.state.interviewId && !h.state.question && !h.ui.answerInput.value, "private state retained");
    assert(h.ui.historyDetail.children.length === 0 && h.ui.historyDetail.hidden, "history retained");
  });
  await test("Pending interview load cannot reopen UI after logout", async (h) => {
    ready(h); let resolve; h.fetchWith(() => new Promise((r) => { resolve = r; }));
    const pending = h.loadInterview("old"); h.showLogin(); resolve(response(detail("old")));
    await pending;
    assert(!h.state.interviewId && !h.state.user && !h.ui.loginView.hidden, "stale load reopened UI");
  });
  await test("Old history request cannot populate another account", async (h) => {
    ready(h); let resolve; h.fetchWith(() => new Promise((r) => { resolve = r; }));
    const pending = h.refreshHistory(); h.showLogin(); h.state.user = { id: "bob" };
    resolve(response([{ id: "private", role: "alice-only" }])); await pending;
    assert(h.ui.historyList.children.length === 0, "cross-account history painted");
  });
  await test("First-character timing waits for a paint opportunity", async (h) => {
    const records = []; h.fetchWith(async (p, init) => { records.push(JSON.parse(init.body)); return response({}); });
    const timing = { requestId: "r", clickAt: 10, firstCharMs: null, speechStartMs: null };
    h.state.timing = timing; h.ui.questionText.textContent = "正文";
    h.markFirstChar(timing);
    assert(timing.firstCharMs === null, "measured before render opportunity");
    h.flushFrames(); await tick();
    assert(timing.firstCharMs === 90 && records[0].first_char_ms === 90, "wrong clock");
  });
  await test("Hidden page never receives a valid first-character measurement", async (h) => {
    h.fetchWith(async () => response({}));
    const timing = { requestId: "r", clickAt: 10, firstCharMs: null, speechStartMs: null };
    h.state.timing = timing; h.ui.questionText.textContent = "正文";
    h.document.visibilityState = "hidden";
    h.markFirstChar(timing); h.flushFrames(); await tick();
    assert(timing.firstCharMs === null, "hidden output counted");
  });
  await test("Leaving a question invalidates its queued first-character measurement", async (h) => {
    const records = [];
    h.fetchWith(async (p, init) => { records.push(JSON.parse(init.body)); return response({}); });
    const timing = { requestId: "old", clickAt: 10, firstCharMs: null, speechStartMs: null };
    h.state.timing = timing;
    h.ui.questionText.textContent = "旧问题";
    h.markFirstChar(timing);
    h.cancelGeneration();
    h.ui.questionText.textContent = "新页面";
    h.flushFrames(); await tick();
    assert(timing.firstCharMs === null && records.length === 0,
      "old request measured new page content");
  });
  await test("Double create click sends one request", async (h) => {
    ready(h); let calls = 0; let resolve;
    h.fetchWith(() => { calls++; return new Promise((r) => { resolve = r; }); });
    const pending = h.createInterview({ preventDefault() {} });
    await h.createInterview({ preventDefault() {} });
    h.showLogin(); resolve(response({ id: "new" })); await pending;
    assert(calls === 1 && !h.state.creating, "duplicate create or stuck loading");
  });
  await test("Double answer confirmation sends one save", async (h) => {
    ready(h); h.ui.answerInput.value = "使用行锁"; let calls = 0; let resolve;
    h.fetchWith(() => { calls++; return new Promise((r) => { resolve = r; }); });
    const pending = h.confirmAnswer(); await h.confirmAnswer();
    h.showLogin(); resolve(response({ state: "active" })); await pending;
    assert(calls === 1 && !h.state.savingAnswer && !h.state.interviewId, "duplicate/stale save");
  });
  await test("Leaving a generation aborts network and exposes retry", (h) => {
    let aborted = false; h.state.streamController = { abort() { aborted = true; } };
    h.state.streaming = true; h.cancelGeneration();
    assert(aborted && !h.state.streaming && !h.ui.retryButton.hidden, "cancel failed");
  });
  await test("Completed interview disables all answer controls", (h) => {
    const data = detail(); data.interview.state = "complete"; data.interview.current_turn = 4;
    h.renderInterview(data);
    assert(h.ui.questionArea.hidden && !h.ui.completeArea.hidden && h.ui.confirmButton.disabled, "completion not enforced");
  });
  await test("Fragmented question stream paints deltas and enables answering only at done", async (h) => {
    ready(h);
    let deliver;
    const packets = [
      'event: meta\ndata: {"requestId":"r","turn":1}\n\n',
      'event: delta\ndata: {"text":"请介绍"}\n\n',
      'event: delta\ndata: {"text":"具体取舍？"}\n\n',
      'event: done\ndata: {"requestId":"r","turn":1,"question":"请介绍具体取舍？"}\n\n',
    ];
    let readIndex = 0;
    h.fetchWith(async (url) => {
      assert(url.endsWith("?turn=1"), "expected turn missing");
      return { ok: true, body: { getReader: () => ({
        read() {
          if (readIndex === 2) {
            readIndex++;
            return new Promise(resolve => { deliver = () => resolve({ done: false, value: packets[2] }); });
          }
          if (readIndex < packets.length) return Promise.resolve({ done: false, value: packets[readIndex++] });
          return Promise.resolve({ done: true });
        },
        cancel: async () => {},
      }) } };
    });
    const pending = h.generateQuestion(10);
    await tick();
    assert(h.ui.questionText.textContent === "请介绍" && h.ui.confirmButton.disabled, "stream not incremental");
    deliver(); await pending;
    assert(h.state.question === "请介绍具体取舍？" && !h.ui.confirmButton.disabled, "done not saved to UI");
  });
  await test("CRLF event boundaries split across chunks still complete the question", async (h) => {
    ready(h);
    const packets = [
      'event: meta\r\ndata: {"requestId":"r","turn":1}\r\n\r',
      '\nevent: delta\r\ndata: {"text":"请介绍项目？"}\r\n\r',
      '\nevent: done\r\ndata: {"requestId":"r","turn":1,"question":"请介绍项目？"}\r\n\r\n',
    ];
    h.fetchWith(async () => ({ ok: true, body: { getReader: () => ({
      read: async () => packets.length
        ? { done: false, value: packets.shift() } : { done: true },
      cancel: async () => {},
    }) } }));
    await h.generateQuestion(10);
    assert(h.state.question === "请介绍项目？" && !h.ui.confirmButton.disabled,
      "CRLF stream was not completed");
  });
  await test("Terminal SSE event releases the UI without waiting for EOF", async (h) => {
    ready(h);
    let reads = 0;
    let cancelled = false;
    h.fetchWith(async () => ({ ok: true, body: { getReader: () => ({
      read: async () => {
        reads += 1;
        return reads === 1
          ? { done: false, value: 'event: existing\ndata: {"turn":1,"question":"已保存的问题"}\n\n' }
          : { done: true };
      },
      cancel: async () => { cancelled = true; },
    }) } }));
    await h.generateQuestion(10);
    assert(reads === 1 && cancelled, "terminal event kept reading the stream");
    assert(!h.state.streaming && !h.state.streamController && !h.ui.confirmButton.disabled,
      "answer UI was left blocked after the terminal event");
  });
  await test("Truncated question stream cannot be confirmed", async (h) => {
    ready(h); let sent = false;
    h.fetchWith(async () => ({ ok: true, body: { getReader: () => ({
      read: async () => {
        if (sent) return { done: true };
        sent = true;
        return { done: false, value: 'event: delta\ndata: {"text":"不完整问题"}\n\n' };
      }, cancel: async () => {},
    }) } }));
    await h.generateQuestion();
    assert(h.ui.confirmButton.disabled && !h.ui.retryButton.hidden && !h.state.question, "truncated output accepted");
  });
  await test("Navigation suppresses late stream packets", async (h) => {
    ready(h); let deliver;
    h.fetchWith(async () => ({ ok: true, body: { getReader: () => ({
      read: () => new Promise(resolve => { deliver = resolve; }), cancel: async () => {},
    }) } }));
    const pending = h.generateQuestion(); await tick();
    h.cancelGeneration(); h.ui.questionText.textContent = "新页面";
    deliver({ done: false, value: 'event: delta\ndata: {"text":"旧问题"}\n\n' });
    await pending;
    assert(h.ui.questionText.textContent === "新页面", "stale stream painted");
  });
  return { kind: "frontend-logic-test-doubles", passed: results.filter(x => x.status === "passed").length,
    failed: results.filter(x => x.status === "failed").length, results };
}

module.exports = { runTests };
if (typeof require !== "undefined" && require.main === module) {
  const fs = require("node:fs");
  const path = require("node:path");
  runTests(fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8"))
    .then(report => { console.log(JSON.stringify(report, null, 2)); process.exitCode = report.failed ? 1 : 0; })
    .catch(error => { console.error(error); process.exitCode = 1; });
}
