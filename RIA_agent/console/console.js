"use strict";

const byId = (id) => document.getElementById(id);
const status = byId("status");
const results = byId("results");
const raw = byId("raw");
const jobId = byId("job-id");

function append(parent, tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  parent.append(element);
}

let selectionVersion = 0;
function clearOutput() {
  results.replaceChildren();
  raw.hidden = true;
  byId("raw-json").textContent = "";
  jobId.value = "";
}
byId("submit-job").addEventListener("input", () => {
  selectionVersion++;
  clearOutput();
  status.textContent = "Ready to submit this document.";
});

function render(job) {
  status.textContent = String(job.status).replaceAll("_", " ");
  jobId.value = job.job_id;
  append(results, "p", job.job_id, "metadata");
  if (job.review_reason) append(results, "p", job.review_reason.replaceAll("_", " "));
  const result = job.result;
  if (result?.document) append(results,"p",result.document.source_url,"metadata");
  if (result) {
    append(results, "p", `Document SHA-256: ${result.sha256}`, "metadata");
    append(results, "p", `Model: ${result.model} · Obligations: ${result.obligations.length}`, "metadata");
    if (result.obligations.length) renderObligations(result.obligations);
  }
  byId("raw-json").textContent = JSON.stringify(job, null, 2);
  raw.hidden = false;
}

function renderObligations(obligations) {
    const table = document.createElement("table");
    const header = table.createTHead().insertRow();
    append(header, "th", "Source");
    append(header, "th", "Obligation");
    append(header, "th", "Tag");
    append(header, "th", "System");
    const body = table.createTBody();
    for (const obligation of obligations) {
      const row = body.insertRow();
      const citation = obligation.citation;
      const paragraph = citation.quote.match(/[“"]([0-9]+[A-Z]?)\.\s/);
      row.insertCell().textContent = `Page ${citation.page}${paragraph ? `, ¶${paragraph[1]}` : ""}`;
      const cell = row.insertCell();
      append(cell, "p", obligation.statement);
      const evidence = document.createElement("details");
      append(evidence, "summary", "Source excerpt");
      append(evidence, "p", citation.quote);
      cell.append(evidence);
      row.insertCell().textContent = obligation.potential_change?.area ?? "?";
      row.insertCell().textContent = obligation.potential_change?.system ?? "?";
    }
    results.append(table);
}

async function run(label, path, options = {}) {
  const version = ++selectionVersion;
  clearOutput();
  raw.open = false;
  byId("raw-json").textContent = "";
  status.classList.remove("error");
  const buttons = document.querySelectorAll("button[type=submit]");
  buttons.forEach((button) => { button.disabled = true; });
  status.textContent = label;
  try {
    const response = await fetch(path, { ...options, redirect: "error" });
    if (response.status === 401) throw new Error("This console requires local test mode.");
    if (response.status === 404) throw new Error("Job not found for this client.");
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const detail = typeof payload?.detail === "string" ? payload.detail : "";
      throw new Error(detail || `Request failed (HTTP ${response.status}).`);
    }
    const job = await response.json();
    if (version === selectionVersion) render(job);
  } catch (error) {
    if (version !== selectionVersion) return;
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

byId("submit-job").addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = new FormData(event.currentTarget);
  const pdf = byId("pdf").files[0];
  if (pdf && pdf.size > 16 * 1024 * 1024) {
    status.textContent = "Choose a PDF at or below 16 MiB.";
    status.classList.add("error");
    return;
  }
  const path = pdf ? `/v1/jobs/upload?${new URLSearchParams(fields)}` : "/v1/jobs/url";
  void run("Running verification, scan, parsing, and extraction…", path, {
    method: "POST",
    headers: { "Content-Type": pdf ? "application/pdf" : "application/json" },
    body: pdf || JSON.stringify(Object.fromEntries(fields)),
  });
});

byId("lookup-job").addEventListener("submit", (event) => {
  event.preventDefault();
  void run("Looking up job…", `/v1/jobs/${encodeURIComponent(jobId.value)}`);
});
