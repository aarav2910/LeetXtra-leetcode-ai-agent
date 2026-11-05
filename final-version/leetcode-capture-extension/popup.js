const captureButton = document.querySelector("#capture");

function render(data) {
  if (window.setActiveCapture) return window.setActiveCapture(data);
}

async function captureFromActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https?:\/\/(www\.)?leetcode\.com\//i.test(tab.url || "")) {
    throw new Error("Open a problem page on leetcode.com before capturing.");
  }
  const injected = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: captureLeetCodePage,
  });
  const data = injected?.[0]?.result;
  if (!data) throw new Error("Could not read problem data from this page. Reload LeetCode and try again.");

  const stored = await chrome.storage.local.get("leetcode_captures");
  const captures = stored.leetcode_captures || {};
  captures[data.problem_key] = data;
  await chrome.storage.local.set({ leetcode_captures: captures });
  await render(data);
}

// This injected function is self-contained because Chrome serializes it into the page.
function captureLeetCodePage() {
  const clean = (s) => (s || "").replace(/\u00a0/g, " ").replace(/[ \t]+\n/g, "\n").trim();
  const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const firstText = (selectors) => {
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      const value = clean(el?.innerText || el?.textContent);
      if (value) return value;
    }
    return "";
  };
  const title = firstText(['[data-cy="question-title"]', '[data-testid="question-title"]', "div.text-title-large", "main h1", "h1"])
    || clean(document.title.replace(/\s*-\s*LeetCode.*$/i, ""));
  const root = document.querySelector('[data-track-load="description_content"]')
    || document.querySelector('[data-cy="question-content"]')
    || document.querySelector(".elfjS")
    || document.querySelector("[class*='description__']")
    || document.querySelector("main");
  const description = clean(root?.innerText || "");
  const topicLinks = [...document.querySelectorAll('a[href*="/tag/"]')]
    .filter(visible).map((a) => clean(a.innerText)).filter(Boolean);
  const topics = [...new Set(topicLinks)];

  let examples = [];
  if (root) {
    examples = [...root.querySelectorAll("pre")].map((el) => clean(el.innerText)).filter(Boolean);
  }
  if (!examples.length && description) {
    const matches = description.match(/Example\s*\d*\s*:[\s\S]*?(?=\n\s*Example\s*\d*\s*:|\n\s*Constraints\s*:|$)/gi);
    examples = (matches || []).map(clean);
  }

  const sectionAfter = (label, stopLabels) => {
    const re = new RegExp("(?:^|\\n)\\s*" + label + "\\s*:?\\s*", "i");
    const match = re.exec(description);
    if (!match) return "";
    const start = match.index + match[0].length;
    const rest = description.slice(start);
    let end = rest.length;
    for (const stop of stopLabels) {
      const stopRe = new RegExp("\\n\\s*" + stop + "\\s*:?", "i");
      const found = stopRe.exec(rest);
      if (found && found.index < end) end = found.index;
    }
    return clean(rest.slice(0, end));
  };
  const constraintText = sectionAfter("Constraints", ["Follow-up", "Follow up", "Hints", "Related Topics"]);
  const constraints = constraintText ? constraintText.split("\n").map(clean).filter(Boolean) : [];
  const followUp = sectionAfter("Follow[- ]?up", ["Constraints", "Hints", "Related Topics"]);

  let code = "";
  let codeSource = "unavailable";
  try {
    // Monaco models retain source text even when only a portion is rendered in the DOM.
    const models = window.monaco?.editor?.getModels?.() || [];
    const model = models.find((item) => item.getValue?.()?.trim());
    if (model) {
      code = model.getValue();
      codeSource = "monaco_model";
    }
  } catch (_) {}
  if (!code) {
    const cm = document.querySelector(".cm-editor .cm-content");
    const editable = document.querySelector('[contenteditable="true"][role="textbox"]');
    const textarea = [...document.querySelectorAll("textarea")].find(visible);
    const candidate = cm || editable || textarea;
    if (candidate) {
      code = candidate.value ?? candidate.innerText ?? candidate.textContent ?? "";
      codeSource = cm ? "editor_dom_may_be_partial" : "editor_dom";
    }
  }

  const languageHints = [...document.querySelectorAll("button,[role='combobox'],[data-cy*='lang'],[class*='lang']")]
    .filter(visible).map((el) => clean(el.innerText || el.getAttribute("aria-label") || el.textContent))
    .filter((s) => /^(Python\s*3?|C\+\+|Java|JavaScript|TypeScript|C#|Go|Rust|Kotlin|Swift|Ruby|PHP)$/i.test(s));
  const language = languageHints[0] || "";

  const resultWords = /Accepted|Wrong Answer|Time Limit Exceeded|Runtime Error|Syntax Error|Compile Error|Compilation Error|Memory Limit Exceeded|Output Limit Exceeded|Pending|Processing|Finished|Error/i;
  const resultRegion = (label) => {
    const exactLabel = (value) => clean(value).toLowerCase() === label.toLowerCase();
    const tabs = [...document.querySelectorAll("[role='tab'],[aria-controls],button")].filter(visible);
    for (const tab of tabs) {
      if (!exactLabel(tab.innerText || tab.getAttribute("aria-label") || "")) continue;
      const controlledId = tab.getAttribute("aria-controls");
      const controlled = controlledId && document.getElementById(controlledId);
      if (controlled && visible(controlled)) return controlled;
      if (tab.getAttribute("aria-selected") === "true") {
        const activePanel = [...document.querySelectorAll("[role='tabpanel']")].find(visible);
        if (activePanel) return activePanel;
      }
    }
    const candidates = [...document.querySelectorAll("[role='tabpanel'],section,main,div")]
      .filter(visible)
      .map((el) => ({ el, value: clean(el.innerText || el.textContent) }))
      .filter(({ value }) => value.length > label.length && value.length < 15000
        && value.toLowerCase().includes(label.toLowerCase()) && resultWords.test(value)
        && (label !== "Submissions" || /\b(status|language|runtime|memory)\b/i.test(value)))
      .sort((a, b) => a.value.length - b.value.length);
    return candidates[0]?.el || null;
  };
  const resultRecord = (details) => {
    const value = clean(details);
    if (!value) return null;
    const status = value.match(resultWords)?.[0] || "Result shown";
    return { status, details: value, captured_at: new Date().toISOString() };
  };
  // A Run Code result belongs to the Test Result panel, independent of button history.
  const collectTestResult = () => {
    const region = resultRegion("Test Result") || resultRegion("Run Code Result");
    return region ? resultRecord(region.innerText || region.textContent) : null;
  };
  // Submission rows are read only from the Submissions view, not from the run panel.
  const collectLatestSubmission = () => {
    const region = resultRegion("Submissions");
    if (!region) return null;
    const toSubmission = (details) => {
      const status = details.match(resultWords)?.[0] || "Result shown";
      const languageMatch = details.match(/\b(C\+\+|Python\s*3?|JavaScript|TypeScript|Java|C#|Go|Rust|Kotlin|Swift|Ruby|PHP)\b/i);
      const runtimeMatch = details.match(/\b\d+(?:\.\d+)?\s*ms\b/i);
      const memoryMatch = details.match(/\b\d+(?:\.\d+)?\s*MB\b/i);
      return {
        status,
        language: languageMatch?.[0] || null,
        runtime: runtimeMatch?.[0] || null,
        memory: memoryMatch?.[0] || null,
        details,
        captured_at: new Date().toISOString(),
      };
    };
    const isSingleSubmission = (details) => {
      const statuses = details.match(new RegExp(resultWords.source, "gi")) || [];
      const runtimes = details.match(/\b\d+(?:\.\d+)?\s*ms\b/gi) || [];
      const memories = details.match(/\b\d+(?:\.\d+)?\s*MB\b/gi) || [];
      return details.length < 2500 && statuses.length === 1 && runtimes.length <= 1 && memories.length <= 1;
    };
    const rowSelector = "[role='row'],tr,li";
    for (const row of region.querySelectorAll(rowSelector)) {
      if (!visible(row)) continue;
      const details = clean(row.innerText || row.textContent);
      if (details && isSingleSubmission(details)) return toSubmission(details);
    }
    // LeetCode can render submission rows as divs without table or ARIA row roles.
    for (const row of region.querySelectorAll("div")) {
      if (!visible(row)) continue;
      const details = clean(row.innerText || row.textContent);
      if (details && isSingleSubmission(details)
        && /\b\d+(?:\.\d+)?\s*ms\b/i.test(details)
        && /\b\d+(?:\.\d+)?\s*MB\b/i.test(details)) return toSubmission(details);
    }
    return null;
  };

  const url = location.href;
  const slugMatch = url.match(/\/problems\/([^/?#]+)/i);
  const problemKey = slugMatch?.[1] || url;
  const stateKey = "__leetcodeLocalCaptureStateV2";
  let state = window[stateKey];
  if (!state || state.url !== url) {
    state = { url, test_results: null, latest_submission: null, observerInstalled: false };
    window[stateKey] = state;
  }
  const updateResults = () => {
    const runResult = collectTestResult();
    if (runResult) state.test_results = runResult;
    const latestSubmission = collectLatestSubmission();
    if (latestSubmission) state.latest_submission = latestSubmission;
  };
  if (!state.observerInstalled) {
    state.observerInstalled = true;
    let timer;
    const observer = new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(updateResults, 350);
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  }
  updateResults();

  return {
    problem_key: problemKey,
    question_name: title,
    url,
    topics,
    description,
    examples,
    constraints,
    follow_up: followUp || null,
    code,
    code_source: codeSource,
    language: language || null,
    test_results: state.test_results,
    latest_submission: state.latest_submission,
    captured_at: new Date().toISOString(),
  };
}

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  captureButton.textContent = "Capturing…";
  try {
    await captureFromActiveTab();
  } catch (error) {
    document.querySelector("#capture-status").textContent = error?.message || "Could not capture this LeetCode question.";
  } finally {
    captureButton.disabled = false;
    captureButton.textContent = "Capture";
  }
});

