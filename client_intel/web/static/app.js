const byId = (id) => document.getElementById(id);
const form = byId("request-form");
const button = byId("generate");
const status = byId("status");
const empty = byId("empty");
const output = byId("briefing");
const evidencePanel = byId("evidence");
const downloads = byId("downloads");
const badge = byId("result-badge");
const storageKey = "client-intel-last-run";

function text(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
}

async function json(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(typeof problem.detail === "string" ? problem.detail : `Request failed (${response.status}).`);
  }
  return response.json();
}

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

function setOptions(select, entries) {
  select.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
}

const titleCase = (value) => value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());

function showEvidence(key, item) {
  evidencePanel.replaceChildren(text("h3", `Citation · ${key}`), text("p", item.text));
  if (item.metrics.length) {
    const facts = document.createElement("ul");
    for (const metric of item.metrics) {
      const period = metric.period ? ` · ${metric.period}` : "";
      facts.append(text("li", `${metric.name.replaceAll("_", " ")}: ${metric.display} · ${metric.unit}${period}`));
    }
    evidencePanel.append(facts);
  }
  for (const row of item.sources) {
    const disclosure = document.createElement("details");
    disclosure.append(text("summary", `${row.table} / ${row.key} · row ${row.rowid}`));
    disclosure.append(text("pre", JSON.stringify(row.raw, null, 2)));
    evidencePanel.append(disclosure);
  }
  evidencePanel.hidden = false;
  evidencePanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function render(bundle, verification, artifacts) {
  const config = bundle.trigger_configuration;
  const allEvidence = Object.values(bundle.tools).flatMap((tool) => tool.evidence);
  const facts = new Map(allEvidence.map((item) => [item.key, item]));
  const sections = {
    lead: config.lead_heading, context: config.support_heading, actions: config.action_heading,
    gaps: "Data gaps and confidence",
  };
  byId("result-title").textContent = bundle.client.client_name;
  byId("result-meta").textContent = `${config.label} · ${bundle.client.client_id}`;
  byId("result-meta").hidden = false;
  badge.textContent = titleCase(verification.status);
  badge.dataset.state = verification.status;
  badge.hidden = false;
  output.replaceChildren();
  if (verification.status === "incomplete") {
    const problem = verification.draft_checks.find((issue) => issue.reason);
    output.append(text("p", problem?.reason || "A core answer could not be verified. Review the saved evidence.", "message"));
  } else if (verification.draft_checks.length) {
    output.append(text("p", "Verification withheld some proposed material. See the verification report.", "message"));
  }
  for (const [section, heading] of Object.entries(sections)) {
    const claims = bundle.claims.filter((claim) => claim.section === section);
    if (!claims.length && section !== "gaps") continue;
    const region = document.createElement("section");
    region.append(text("h3", heading));
    if (!claims.length) region.append(text("p", "See the evidence file for source coverage and limitations."));
    const list = document.createElement("ul");
    for (const claim of claims) {
      const why = claim.claim_id.includes(".why.");
      const entry = text("li", why ? `Why: ${claim.text}` : claim.text,
        why ? "reason" : claim.kind === "action" ? "action" : "fact");
      if (claim.readiness) entry.append(text("span", titleCase(claim.readiness), "badge"));
      for (const key of claim.evidence_keys) {
        const label = bundle.footnotes[key];
        const citation = text("button", label.slice(1), "citation");
        citation.type = "button";
        citation.setAttribute("aria-label", `View evidence ${label.slice(1)}`);
        citation.addEventListener("click", () => showEvidence(key, facts.get(key)));
        entry.append(citation);
      }
      list.append(entry);
    }
    if (claims.length) region.append(list);
    output.append(region);
  }
  const labels = {"briefing.md": "Briefing", "evidence.json": "Evidence", "verification.json": "Verification",
    "trace.jsonl": "Trace", "manifest.json": "Manifest"};
  downloads.replaceChildren(...Object.entries(artifacts).map(([name, url]) => {
    const link = text("a", labels[name]);
    link.href = url;
    link.download = name;
    return link;
  }));
  empty.hidden = true;
  output.hidden = false;
  downloads.hidden = false;
  evidencePanel.hidden = true;
}

async function follow(run) {
  const stages = {queued: "Queued…", resolving: "Resolving account…", evidence: "Gathering evidence…",
    writing: "Writing the briefing…", verifying: "Verifying claims…"};
  while (["queued", "running"].includes(run.status)) {
    setStatus(stages[run.stage] || "Running…");
    await new Promise((resolve) => setTimeout(resolve, 600));
    run = await json(`/api/runs/${encodeURIComponent(run.id)}`);
  }
  if (run.status !== "completed") throw new Error(run.error || "The run did not complete.");
  const [bundle, verification] = await Promise.all([
    json(run.artifacts["evidence.json"]), json(run.artifacts["verification.json"]),
  ]);
  render(bundle, verification, run.artifacts);
  setStatus(`Run ${run.id.slice(0, 8)} · finished`);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  button.textContent = "Generating…";
  setStatus("Submitting request…");
  try {
    const run = await json("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.fromEntries(new FormData(form)))});
    sessionStorage.setItem(storageKey, run.id);
    await follow(run);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Generate briefing";
  }
});

async function initialise() {
  const [clients, triggers] = await Promise.all([json("/api/clients"), json("/api/triggers")]);
  setOptions(byId("client"), clients.map((item) => [item.client_id, item.client_name]));
  setOptions(byId("trigger"), triggers.map((item) => [item.id, item.label]));
  const lastRun = sessionStorage.getItem(storageKey);
  try {
    if (lastRun) {
      const run = await json(`/api/runs/${encodeURIComponent(lastRun)}`);
      byId("client").value = run.client_id;
      byId("trigger").value = run.trigger;
      await follow(run);
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
}

initialise().catch((error) => setStatus(error.message, true));
