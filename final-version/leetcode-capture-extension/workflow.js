const languageInput = document.querySelector("#language");
const settingsView = document.querySelector("#settings-view");
const appView = document.querySelector("#app-view");
const providerSelect = document.querySelector("#provider-select");
const groqModelField = document.querySelector("#groq-model-field");
const groqModelSelect = document.querySelector("#groq-model");
const providerApiKeyInput = document.querySelector("#provider-api-key");
const settingsStatus = document.querySelector("#settings-status");
const defaultGroqModel = "openai/gpt-oss-120b";
const supportedGroqModels = [defaultGroqModel, "openai/gpt-oss-20b"];
let settingsReturnTab = "tab-error";
let providerPrefs = { provider: "openai", groq_model: "openai/gpt-oss-120b", api_keys: {} };
let draftApiKeys = {};
const assistant = {
  capture: null,
  code: "",
  language: "Python",
  activeTab: "tab-error",
  analysisData: null,
  optimizationData: null,
  repairData: null,
  repairSourceCode: "",
  youtubeCache: {},
};
function persistState() {
  assistant.language = languageInput.value;
  return chrome.storage.local.set({ assistant_state: assistant }).catch(() => {});
}

function persistStateNow() {
  assistant.language = languageInput.value;
  return chrome.storage.local.set({ assistant_state: assistant });
}

function youtubeCacheKey() {
  const question = assistant.capture?.problem_key || assistant.capture?.question_name || "";
  return `${question}::${languageInput.value}`;
}

window.setActiveCapture = (data) => {
  assistant.capture = data;
  assistant.code = data.code || "";
  const detected = String(data.language || "");
  assistant.language = /^python/i.test(detected) ? "Python" : (/c\+\+/i.test(detected) ? "C++" : languageInput.value);
  assistant.analysisData = null;
  assistant.optimizationData = null;
  assistant.repairData = null;
  assistant.repairSourceCode = "";
  document.querySelector("#problem-title").textContent = data.question_name || "LeetCode problem";
  document.querySelector("#capture-status").textContent = "Question and editor code captured. Choose a tool below.";
  languageInput.value = assistant.language;
  document.querySelector("#error-status").textContent = "Waiting for code.";
  document.querySelector("#analysis-status").textContent = "Waiting for code.";
  document.querySelector("#optimization-status").textContent = "Waiting for code.";
  document.querySelector("#youtube-status").textContent = "Search videos for this question.";
  document.querySelector("#error-result").replaceChildren();
  document.querySelector("#error-result").hidden = true;
  document.querySelector("#analysis-result").replaceChildren();
  document.querySelector("#analysis-result").hidden = true;
  document.querySelector("#optimization-result").replaceChildren();
  document.querySelector("#optimization-result").hidden = true;
  document.querySelector("#youtube-result").replaceChildren();
  document.querySelector("#youtube-result").hidden = true;
  updateReport();
  return persistStateNow();
};

languageInput.addEventListener("change", () => {
  persistState();
  const cachedVideos = assistant.youtubeCache[youtubeCacheKey()];
  renderYoutubeResult(cachedVideos);
  document.querySelector("#youtube-status").textContent = cachedVideos?.length
    ? `${cachedVideos.length} saved videos for ${languageInput.value}.`
    : `Search YouTube for ${languageInput.value} videos.`;
});

function switchTab(tabId) {
  const tab = document.getElementById(tabId);
  document.querySelectorAll(".tab").forEach((item) => {
    const selected = item === tab;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    const selected = panel.id === tab.getAttribute("aria-controls");
    panel.classList.toggle("active", selected);
    panel.hidden = !selected;
  });
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  assistant.activeTab = tab.id;
  switchTab(tab.id);
  persistState();
}));

async function postJson(path, body) {
  const requestBody = { ...body };
  if (path !== "/api/youtube-search") {
    const stored = await chrome.storage.local.get("provider_settings");
    const saved = stored.provider_settings || providerPrefs;
    const provider = saved.provider || "openai";
    requestBody.provider_settings = {
      provider,
      model: provider === "groq"
        ? (supportedGroqModels.includes(saved.groq_model) ? saved.groq_model : defaultGroqModel)
        : undefined,
      api_key: saved.api_keys?.[provider] || "",
    };
  }
  const response = await fetch(`http://127.0.0.1:8765${path}`, {
    method: "POST",
    headers: { "Content-Type": "text/plain;charset=UTF-8" },
    body: JSON.stringify(requestBody),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "The local app request failed.");
  return result;
}

function displaySettingsForProvider(provider) {
  groqModelField.hidden = provider !== "groq";
  const savedKey = providerPrefs.api_keys?.[provider] || "";
  providerApiKeyInput.value = draftApiKeys[provider] || "";
  providerApiKeyInput.placeholder = savedKey ? "Key saved — paste a replacement" : "Paste provider API key";
}

async function openSettings() {
  settingsReturnTab = assistant.activeTab || "tab-error";
  const stored = await chrome.storage.local.get("provider_settings");
  const saved = stored.provider_settings || {};
  providerPrefs = {
    provider: ["openai", "gemini", "groq"].includes(saved.provider) ? saved.provider : "openai",
    groq_model: supportedGroqModels.includes(saved.groq_model) ? saved.groq_model : defaultGroqModel,
    api_keys: saved.api_keys && typeof saved.api_keys === "object" ? saved.api_keys : {},
  };
  draftApiKeys = {};
  providerSelect.value = providerPrefs.provider;
  groqModelSelect.value = providerPrefs.groq_model;
  displaySettingsForProvider(providerPrefs.provider);
  settingsStatus.textContent = "";
  appView.hidden = true;
  settingsView.hidden = false;
}

document.querySelector("#settings-open").addEventListener("click", openSettings);
document.querySelector("#settings-back").addEventListener("click", () => {
  settingsView.hidden = true;
  appView.hidden = false;
  switchTab(settingsReturnTab);
});
providerSelect.addEventListener("change", (event) => {
  const currentProvider = event.currentTarget.value;
  displaySettingsForProvider(currentProvider);
});
providerApiKeyInput.addEventListener("input", () => {
  draftApiKeys[providerSelect.value] = providerApiKeyInput.value;
});
document.querySelector("#settings-save").addEventListener("click", async () => {
  const provider = providerSelect.value;
  const apiKeys = { ...providerPrefs.api_keys };
  for (const [name, key] of Object.entries(draftApiKeys)) {
    if (key.trim()) apiKeys[name] = key.trim();
  }
  if (!apiKeys[provider]) {
    settingsStatus.textContent = `Paste your ${provider[0].toUpperCase()}${provider.slice(1)} API key first.`;
    return;
  }
  providerPrefs = {
    provider,
    groq_model: groqModelSelect.value,
    api_keys: apiKeys,
  };
  try {
    await chrome.storage.local.set({ provider_settings: providerPrefs });
    draftApiKeys = {};
    displaySettingsForProvider(provider);
    settingsStatus.textContent = "Settings saved.";
  } catch (error) {
    settingsStatus.textContent = `Could not save settings: ${error.message}`;
  }
});

function setBusy(button, busy, busyText, normalText) {
  button.disabled = busy;
  const label = button.querySelector("span");
  if (label) label.textContent = busy ? busyText : normalText;
  else button.textContent = busy ? busyText : normalText;
}

const generationCounts = new Map();
function beginGeneration(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return () => {};
  const loaders = [...panel.querySelectorAll(".tab-loader")];
  const count = (generationCounts.get(panelId) || 0) + 1;
  generationCounts.set(panelId, count);
  loaders.forEach((loader) => { loader.hidden = false; });
  let finished = false;
  return () => {
    if (finished) return;
    finished = true;
    const remaining = Math.max(0, (generationCounts.get(panelId) || 1) - 1);
    if (remaining) generationCounts.set(panelId, remaining);
    else {
      generationCounts.delete(panelId);
      loaders.forEach((loader) => { loader.hidden = true; });
    }
  };
}

function addLabel(container, label, value) {
  const heading = document.createElement("div");
  heading.className = "result-label";
  heading.textContent = label;
  const text = document.createElement("div");
  text.className = "result-value";
  if (label.toLowerCase() === "diagnosis") text.classList.add("diagnosis-value");
  text.style.whiteSpace = "pre-wrap";
  text.style.overflowWrap = "anywhere";
  text.textContent = value == null || value === "" ? "Not available." : String(value);
  container.append(heading, text);
  return text;
}

function extractBigOValues(value) {
  const text = String(value || "");
  const values = [];
  const marker = /O\s*\(/gi;
  let match;
  while ((match = marker.exec(text))) {
    const open = text.indexOf("(", match.index);
    let depth = 0;
    for (let index = open; index < text.length; index += 1) {
      if (text[index] === "(") depth += 1;
      if (text[index] === ")" && --depth === 0) {
        values.push(text.slice(match.index, index + 1).trim());
        marker.lastIndex = index + 1;
        break;
      }
    }
  }
  return values;
}

function addOptimizationComplexity(container, label, value) {
  const heading = document.createElement("div");
  heading.className = "result-label";
  heading.textContent = label;
  const row = document.createElement("div");
  row.className = "result-value optimization-complexity";
  const values = extractBigOValues(value);
  if (values.length > 1) {
    const previous = document.createElement("span");
    previous.className = "complexity-previous";
    previous.textContent = values[0];
    row.append(previous, document.createTextNode(" → "));
  }
  const proposed = document.createElement("span");
  proposed.className = "complexity-proposed";
  proposed.textContent = values.length ? values[values.length - 1] : (value || "Not specified");
  row.append(proposed);
  container.append(heading, row);
}

async function copyCode(code, button) {
  const originalLabel = button.textContent;
  try {
    await navigator.clipboard.writeText(code);
    button.textContent = "Copied";
  } catch (_) {
    button.textContent = "Copy failed";
  }
  setTimeout(() => { button.textContent = originalLabel; }, 1400);
}

const CODE_KEYWORDS = {
  Python: new Set([
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "False", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "None",
    "nonlocal", "not", "or", "pass", "raise", "return", "True", "try",
    "while", "with", "yield",
  ]),
  "C++": new Set([
    "alignas", "auto", "bool", "break", "case", "catch", "char", "class",
    "const", "continue", "default", "delete", "do", "double", "else", "enum",
    "false", "float", "for", "if", "int", "long", "namespace", "new",
    "nullptr", "private", "protected", "public", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "template", "this", "throw",
    "true", "try", "typedef", "typename", "union", "unsigned", "using",
    "virtual", "void", "volatile", "while", "string", "vector", "map",
    "unordered_map", "set", "unordered_set", "pair", "queue", "stack",
    "priority_queue",
  ]),
};

function appendHighlightedCode(container, source, language = languageInput.value) {
  const tokenPattern = /(\/\*[\s\S]*?\*\/|\/\/[^\n]*|#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b|[A-Za-z_$][\w$]*)/g;
  const keywords = CODE_KEYWORDS[language] || CODE_KEYWORDS.Python;
  let cursor = 0;
  for (const match of source.matchAll(tokenPattern)) {
    if (match.index > cursor) {
      container.append(document.createTextNode(source.slice(cursor, match.index)));
    }
    const token = match[0];
    let className = "";
    if (/^(\/\/|\/\*|#)/.test(token)) className = "code-comment";
    else if (/^["'`]/.test(token)) className = "code-string";
    else if (/^\d/.test(token)) className = "code-number";
    else if (keywords.has(token)) className = "code-keyword";
    else if (/^[A-Z][A-Za-z\d_]*$/.test(token)) className = "code-type";
    else if (/^[A-Za-z_$][\w$]*$/.test(token)) {
      const after = source.slice(match.index + token.length).match(/^\s*\(/);
      if (after) className = "code-function";
    }
    if (className) {
      const span = document.createElement("span");
      span.className = className;
      span.textContent = token;
      container.append(span);
    } else {
      container.append(document.createTextNode(token));
    }
    cursor = match.index + token.length;
  }
  if (cursor < source.length) container.append(document.createTextNode(source.slice(cursor)));
}

function renderRepairDiff(originalCode, correctedCode) {
  const original = originalCode.replace(/\n$/, "").split("\n");
  const corrected = correctedCode.replace(/\n$/, "").split("\n");
  const rows = [];
  const width = corrected.length + 1;
  if ((original.length + 1) * width <= 400000) {
    const table = new Uint32Array((original.length + 1) * width);
    for (let i = original.length - 1; i >= 0; i--) {
      for (let j = corrected.length - 1; j >= 0; j--) {
        const cell = i * width + j;
        table[cell] = original[i] === corrected[j] ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (j < corrected.length) {
      if (i < original.length && original[i] === corrected[j]) {
        rows.push({ text: corrected[j], changed: false }); i++; j++;
      } else if (i < original.length && table[(i + 1) * width + j] > table[i * width + j + 1]) i++;
      else { rows.push({ text: corrected[j], changed: true }); j++; }
    }
  } else {
    let prefix = 0;
    while (prefix < original.length && prefix < corrected.length && original[prefix] === corrected[prefix]) prefix++;
    let suffix = 0;
    while (suffix < original.length - prefix && suffix < corrected.length - prefix
      && original[original.length - 1 - suffix] === corrected[corrected.length - 1 - suffix]) suffix++;
    corrected.forEach((text, index) => rows.push({ text, changed: !(index < prefix || index >= corrected.length - suffix) }));
  }
  const diff = document.createElement("div");
  diff.className = "repair-diff";
  rows.forEach((row, index) => {
    const line = document.createElement("div");
    line.className = row.changed ? "repair-line changed" : "repair-line";
    const number = document.createElement("span");
    number.className = "repair-line-number";
    number.textContent = String(index + 1);
    const source = document.createElement("span");
    source.className = "repair-line-code";
    appendHighlightedCode(source, row.text || " ");
    line.append(number, source);
    diff.append(line);
  });
  return diff;
}

function updateReport() {
  const report = document.querySelector("#report-result");
  report.replaceChildren();
  if (!assistant.analysisData && !assistant.repairData) {
    report.textContent = "Run an analysis or error check to build the report.";
    return;
  }
  if (assistant.capture?.question_name) addLabel(report, "Question", assistant.capture.question_name);
  if (assistant.analysisData) {
    const final = assistant.analysisData.final_report || {};
    addLabel(report, "Overview", final.overview);
    addLabel(report, "Current approach", final.approach_card?.current);
    addLabel(report, "Key idea", final.approach_card?.key_idea);
    addLabel(report, "Complexity", final.complexity_takeaway);
    addLabel(report, "Optimization", final.optimization_result);
    addLabel(report, "Recommendation", final.recommendation);
  }
  if (assistant.repairData) {
    addLabel(report, "Error check", assistant.repairData.diagnosis);
    if (assistant.repairData.has_fix) addLabel(report, "Suggested correction", "Open the Error check tab to review the highlighted changes.");
  }
}

function renderRepairResult() {
  const output = document.querySelector("#error-result");
  output.replaceChildren();
  if (!assistant.repairData) {
    output.hidden = true;
    return;
  }
  const errorType = addLabel(output, "Error type", assistant.repairData.error_type);
  if (String(assistant.repairData.error_type || "").trim().toLowerCase() === "none") {
    errorType.classList.add("no-error");
  }
  addLabel(output, "Diagnosis", assistant.repairData.diagnosis);
  if (assistant.repairData.verification_message) {
    addLabel(output, "Local check", assistant.repairData.verification_message);
  }
  if (assistant.repairData.diagnostics) {
    addLabel(output, "Compiler/runtime output", assistant.repairData.diagnostics);
  }
  if (assistant.repairData.has_fix && assistant.repairData.corrected_code) {
    const copyButton = document.createElement("button");
    copyButton.className = "copy-button";
    copyButton.textContent = "Copy corrected code";
    copyButton.addEventListener("click", () => copyCode(assistant.repairData.corrected_code, copyButton));
    output.append(copyButton);
    output.append(renderRepairDiff(assistant.repairSourceCode, assistant.repairData.corrected_code));
  }
  output.hidden = false;
}

function makeAnalysisCard(title, icon) {
  const card = document.createElement("section");
  card.className = "analysis-card";
  const heading = document.createElement("h3");
  heading.textContent = `${icon} ${title}`;
  card.append(heading);
  return card;
}

function addAnalysisRow(card, label, value, tone = "") {
  if (value == null || value === "") return;
  const row = document.createElement("div");
  row.className = "analysis-row";
  const key = document.createElement("span");
  key.className = "analysis-key";
  key.textContent = `${label}:`;
  const content = document.createElement("span");
  content.className = `analysis-value ${tone}`.trim();
  content.textContent = String(value);
  row.append(key, content);
  card.append(row);
}

function bigOOnly(value) {
  const text = String(value || "");
  const start = text.search(/O\s*\(/i);
  if (start < 0) return "Not available";

  const open = text.indexOf("(", start);
  let depth = 0;
  for (let index = open; index < text.length; index += 1) {
    if (text[index] === "(") depth += 1;
    if (text[index] === ")") {
      depth -= 1;
      if (depth === 0) return text.slice(start, index + 1).replace(/\s+/g, "");
    }
  }
  return "Not available";
}

function renderAnalysisResult() {
  const output = document.querySelector("#analysis-result");
  output.replaceChildren();
  if (!assistant.analysisData) {
    output.hidden = true;
    return;
  }
  const approach = assistant.analysisData.approach_analysis || {};
  const complexity = assistant.analysisData.complexity_analysis || {};
  const summary = document.createElement("div");
  summary.className = "analysis-summary";
  summary.textContent = "✓ Approach   ✓ Efficiency";
  output.append(summary);
  const approachCard = makeAnalysisCard("Approach", "↗");
  addAnalysisRow(approachCard, "Current", approach.current_approach);
  addAnalysisRow(approachCard, "Key idea", approach.key_idea);
  const efficiencyCard = makeAnalysisCard("Efficiency", "ϟ");
  addAnalysisRow(efficiencyCard, "Time complexity", bigOOnly(complexity.time_complexity), "complexity-value");
  addAnalysisRow(efficiencyCard, "Space complexity", bigOOnly(complexity.space_complexity), "complexity-value");
  output.append(approachCard, efficiencyCard);
  output.hidden = false;
}

function renderOptimizationResult() {
  const output = document.querySelector("#optimization-result");
  output.replaceChildren();
  if (!assistant.optimizationData) {
    output.hidden = true;
    return;
  }
  const approaches = assistant.optimizationData.approaches || [];
  if (!approaches.length) {
    output.textContent = assistant.optimizationData.overall_note || "Your approach is optimal for the code and requirements provided.";
    output.classList.add("optimization-optimal");
    output.hidden = false;
    return;
  }
  output.classList.remove("optimization-optimal");
  for (const approach of approaches) {
    if (approach.code) {
      const heading = document.createElement("div");
      heading.className = "result-label";
      heading.textContent = "Optimized code";
      const code = document.createElement("pre");
      code.className = "optimized-code";
      appendHighlightedCode(code, approach.code);
      const copyButton = document.createElement("button");
      copyButton.className = "copy-button";
      copyButton.textContent = "Copy optimized code";
      copyButton.addEventListener("click", () => copyCode(approach.code, copyButton));
      output.append(heading, code, copyButton);
    }
    addLabel(output, "Approach", approach.technique || approach.name);
    addLabel(output, "How it works", approach.change);
    addLabel(output, "Why it helps", approach.why_it_helps);
    addOptimizationComplexity(output, "Time complexity", approach.time_complexity);
    addOptimizationComplexity(output, "Space complexity", approach.space_complexity);
    addLabel(output, "Severity", approach.impact ? `${approach.impact[0].toUpperCase()}${approach.impact.slice(1)} impact` : "Not specified");
  }
  output.hidden = false;
}

function renderYoutubeResult(videos) {
  const output = document.querySelector("#youtube-result");
  output.replaceChildren();
  if (!videos?.length) {
    output.hidden = true;
    return;
  }
  for (const video of videos) {
    const link = document.createElement("a");
    link.className = "video-card";
    link.href = video.video_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const title = document.createElement("strong");
    title.textContent = video.title;
    const channel = document.createElement("span");
    channel.textContent = video.channel;
    link.append(title, channel);
    output.append(link);
  }
  output.hidden = false;
}

document.querySelector("#check-errors").addEventListener("click", async (event) => {
  const finishGeneration = beginGeneration("panel-error");
  const button = event.currentTarget;
  const status = document.querySelector("#error-status");
  const output = document.querySelector("#error-result");
  output.replaceChildren();
  output.hidden = true;
  setBusy(button, true, "Checking…", "Check code");
  status.textContent = "Compiling and running captured examples…";
  try {
    assistant.repairSourceCode = assistant.code || assistant.capture?.code || "";
    assistant.repairData = await postJson("/api/repair", {
      code: assistant.repairSourceCode,
      language: languageInput.value,
      problem_context: assistant.capture ? {
        question_name: assistant.capture.question_name,
        description: assistant.capture.description,
        examples: assistant.capture.examples || [],
      } : null,
    });
    renderRepairResult();
    status.textContent = "Review complete.";
    updateReport();
    await persistStateNow();
  } catch (error) { status.textContent = `Could not check code: ${error.message}`; }
  finally {
    finishGeneration();
    setBusy(button, false, "Checking…", "Check code");
  }
});

document.querySelector("#analyze").addEventListener("click", async (event) => {
  const finishGeneration = beginGeneration("panel-analysis");
  const button = event.currentTarget;
  const status = document.querySelector("#analysis-status");
  const output = document.querySelector("#analysis-result");
  output.replaceChildren();
  output.hidden = true;
  setBusy(button, true, "Analyzing…", "Analyze code");
  status.textContent = "Running code analysis…";
  try {
    assistant.analysisData = await postJson("/api/analyze", {
      code: assistant.code || assistant.capture?.code || "",
      language: languageInput.value,
    });
    assistant.optimizationData = assistant.analysisData.optimization_analysis || null;
    renderAnalysisResult();
    renderOptimizationResult();
    status.textContent = "Analysis complete.";
    document.querySelector("#optimization-status").textContent = "Optimization result is ready.";
    updateReport();
    await persistStateNow();
  } catch (error) { status.textContent = `Analysis failed: ${error.message}`; }
  finally {
    finishGeneration();
    setBusy(button, false, "Analyzing…", "Analyze code");
  }
});

document.querySelector("#optimize").addEventListener("click", async (event) => {
  const finishGeneration = beginGeneration("panel-optimization");
  const button = event.currentTarget;
  const status = document.querySelector("#optimization-status");
  setBusy(button, true, "Optimizing…", "Find optimization");
  status.textContent = "Checking for a major Big O improvement…";
  try {
    if (!assistant.optimizationData) {
      if (!assistant.analysisData) {
        assistant.analysisData = await postJson("/api/analyze", {
          code: assistant.code || assistant.capture?.code || "",
          language: languageInput.value,
        });
      }
      assistant.optimizationData = assistant.analysisData.optimization_analysis || null;
    }
    renderOptimizationResult();
    updateReport();
    status.textContent = "Optimization review complete.";
    await persistStateNow();
  } catch (error) { status.textContent = `Optimization failed: ${error.message}`; }
  finally {
    finishGeneration();
    setBusy(button, false, "Optimizing…", "Find optimization");
  }
});

document.querySelector("#search-youtube").addEventListener("click", async (event) => {
  const finishGeneration = beginGeneration("panel-youtube");
  const button = event.currentTarget;
  const status = document.querySelector("#youtube-status");
  const output = document.querySelector("#youtube-result");
  output.replaceChildren();
  output.hidden = true;
  setBusy(button, true, "Searching…", "Search YouTube");
  status.textContent = "Searching for relevant videos…";
  try {
    if (!assistant.capture?.question_name) throw new Error("Capture a question before searching YouTube.");
    const key = youtubeCacheKey();
    let videos = assistant.youtubeCache[key];
    if (!videos) {
      const result = await postJson("/api/youtube-search", {
        question_name: assistant.capture.question_name,
        language: languageInput.value,
      });
      videos = result.videos || [];
      if (!videos.length) throw new Error("No videos found for this question and language.");
      assistant.youtubeCache[key] = videos;
      await persistStateNow();
    }
    renderYoutubeResult(videos);
    status.textContent = `${videos.length} saved videos for ${languageInput.value}.`;
  } catch (error) { status.textContent = `YouTube search failed: ${error.message}`; }
  finally {
    finishGeneration();
    setBusy(button, false, "Searching…", "Search YouTube");
  }
});

async function restoreAssistantState() {
  try {
    const stored = await chrome.storage.local.get("assistant_state");
    const saved = stored.assistant_state;
    if (!saved) return;
    Object.assign(assistant, saved);
    assistant.youtubeCache ||= {};
    assistant.code = assistant.code || assistant.capture?.code || "";
    languageInput.value = assistant.language || "Python";
    if (assistant.capture) {
      document.querySelector("#problem-title").textContent = assistant.capture.question_name || "LeetCode problem";
      document.querySelector("#capture-status").textContent = "Restored the last question, captured code, and saved results.";
    }
    switchTab(assistant.activeTab || "tab-error");
    renderRepairResult();
    renderAnalysisResult();
    assistant.optimizationData ||= assistant.analysisData?.optimization_analysis || null;
    renderOptimizationResult();
    updateReport();
    if (assistant.repairData) document.querySelector("#error-status").textContent = "Restored saved error check.";
    if (assistant.analysisData) document.querySelector("#analysis-status").textContent = "Restored saved analysis.";
    if (assistant.optimizationData) document.querySelector("#optimization-status").textContent = "Restored saved optimization.";
    const savedVideos = assistant.youtubeCache[youtubeCacheKey()];
    if (savedVideos?.length) {
      renderYoutubeResult(savedVideos);
      document.querySelector("#youtube-status").textContent = `${savedVideos.length} saved videos for ${languageInput.value}.`;
    }
  } catch (_) {}
}

restoreAssistantState();
