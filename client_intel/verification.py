"""Verify structured selection, render bound evidence and re-audit the Markdown."""

from dataclasses import dataclass
import re
from typing import Literal

from .models import Evidence

REFERENCE = re.compile(r"\[\^(e\d+)\]")
NUMBER = re.compile(r"[+−-]?\d[\d,]*(?:\.\d+)?%?")
NUMBER_WORD = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|"
    r"hundred|thousand|million|billion|half|double|triple|first|second|third)\b", re.I
)
SCOPE_LABEL = re.compile(r"\bsecond[- ](?:screen|division)\b", re.I)


@dataclass(frozen=True)
class Line:
    key: str
    kind: Literal["fact", "action"]
    text: str
    evidence: tuple[str, ...]
    readiness: str = ""


def escape(text: str) -> str:
    text = " ".join(text.split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def _issue(issues: list, check: str, target: str, text: str = "", refs=()) -> None:
    issues.append({"check": check, "target": target, "text": text,
                   "evidence_keys": list(refs), "severity": "critical"})


def _unbound_action_number(text: str, cited: list[Evidence]) -> bool:
    # Cited product and competition labels can contain an ordinal as part of the name.
    labels = {label.casefold().replace("-", " ") for item in cited for label in SCOPE_LABEL.findall(item.text)}
    text = SCOPE_LABEL.sub(lambda match: "" if match[0].casefold().replace("-", " ") in labels else match[0], text)
    return bool(NUMBER.search(text) or NUMBER_WORD.search(text))


def verify_draft(draft: dict, candidates: dict[str, Evidence]) -> tuple[dict, list[dict]]:
    accepted = {"lead": [], "context": [], "actions": [], "gaps": []}
    issues = []
    if set(draft) != set(accepted):
        _issue(issues, "writer_schema", "briefing")
    already_displayed = set()
    for section, limit in (("lead", 3), ("context", 4), ("gaps", 3)):
        entries = draft.get(section)
        if not isinstance(entries, list) or len(entries) > limit:
            _issue(issues, "section_limit_or_type", section)
        if not isinstance(entries, list):
            continue
        for index, alias in enumerate(entries[:limit], 1):
            key = f"{section}.{index}"
            if not isinstance(alias, str) or alias not in candidates:
                _issue(issues, "unresolved_or_missing_citation", key, str(alias))
                continue
            fact = candidates[alias]
            if (section == "gaps") != (fact.analysis == "data_quality"):
                _issue(issues, "citation_in_wrong_section", key, refs=(fact.key,))
                continue
            if fact.key not in already_displayed:
                accepted[section].append(Line(key, "fact", fact.text, (fact.key,)))
                already_displayed.add(fact.key)
    actions = draft.get("actions")
    if not isinstance(actions, list) or len(actions) > 2:
        _issue(issues, "section_limit_or_type", "actions")
    for index, action in enumerate(actions[:2] if isinstance(actions, list) else [], 1):
        target = f"action.{index}"
        if not isinstance(action, dict) or set(action) != {"action", "why", "readiness"}:
            _issue(issues, "action_schema", target)
            continue
        text, why, readiness = action["action"], action["why"], action["readiness"]
        if (not isinstance(text, str) or not text.strip() or len(text) > 230 or
                any(ch in text for ch in "\r\n[]<>`*")):
            _issue(issues, "action_text_format", target)
            continue
        if not isinstance(why, list) or not 1 <= len(why) <= 3 or any(
            not isinstance(alias, str) or alias not in candidates for alias in why
        ):
            _issue(issues, "unresolved_or_missing_citation", target, text)
            continue
        refs = tuple(dict.fromkeys(candidates[alias].key for alias in why))
        if _unbound_action_number(text, [candidates[alias] for alias in why]):
            _issue(issues, "unbound_number", target, text, refs)
            continue
        if readiness not in ("ready_for_discussion", "needs_validation"):
            _issue(issues, "action_readiness", target, text, refs)
            continue
        if not text.endswith("?") and text.split()[0].casefold() not in {
            "align", "arrange", "ask", "bring", "check", "clarify", "collect", "compare", "confirm", "contact",
            "coordinate", "demo", "demonstrate", "discuss", "document", "escalate", "establish", "explore",
            "find", "follow", "gather", "identify", "offer", "prepare", "prioritise", "prioritize",
            "propose", "reconcile", "request", "review", "route", "schedule", "share", "test", "triage", "validate", "verify",
        }:
            _issue(issues, "action_is_not_question_or_imperative", target, text, refs)
            continue
        accepted["actions"].append(Line(target, "action", text.strip(), refs, readiness))
    return accepted, issues


def _source_label(evidence: Evidence) -> str:
    pairs = list(dict.fromkeys(f"{item.table}/{item.key}" for item in evidence.sources))
    labels = ", ".join(pairs[:4])
    if len(pairs) > 4:
        labels += ", plus additional source rows in evidence.json"
    return f"{escape(evidence.key)} — {escape(labels)}. See `evidence.json`."


def render(client: dict, as_of: str, trigger: dict, sections: dict, by_key: dict[str, Evidence],
           status: str, notice: str) -> tuple[str, dict]:
    markdown = [f'# {escape(client["client_name"])} · {trigger["label"]}', "",
                f"Snapshot {as_of} · **{status.replace('_', ' ').title()}**", ""]
    if notice:
        markdown.extend((escape(notice), ""))
    footnotes = {}
    mapping = []

    def citation(key: str) -> str:
        identifier = footnotes.setdefault(key, f"e{len(footnotes) + 1}")
        return f"[^{identifier}]"

    def emit(line: Line, prefix: str, section: str) -> None:
        content = escape(line.text)
        if line.readiness:
            content += f" *({line.readiness.replace('_', ' ').capitalize()})*"
        markdown.append(f"{prefix}{content} {' '.join(map(citation, line.evidence))}")
        mapping.append({"claim_id": line.key, "kind": line.kind, "line": len(markdown),
                        "text": line.text, "prefix": prefix, "readiness": line.readiness,
                        "section": section, "evidence_keys": list(line.evidence)})

    for section, heading in (("lead", trigger["lead_heading"]), ("context", trigger["support_heading"]),
                             ("actions", trigger["action_heading"]), ("gaps", "Data gaps and confidence")):
        if not sections[section] and section == "context":
            continue
        markdown.extend((f"## {heading}", ""))
        if section == "gaps" and not sections[section]:
            markdown.append("No material gap was selected from the completed checks. Coverage and source limits are recorded in `evidence.json`.")
        for line in sections[section]:
            emit(line, "- ", section)
            if line.kind == "action":
                for index, key in enumerate(line.evidence, 1):
                    rationale = Line(f"{line.key}.why.{index}", "fact", by_key[key].text, (key,))
                    emit(rationale, "  - Why: ", section)
        markdown.append("")
    for key, identifier in footnotes.items():
        markdown.append(f"[^{identifier}]: {_source_label(by_key[key])}")
    rendered = "\n".join(markdown).rstrip() + "\n"
    claim_lines = {claim["line"] for claim in mapping}
    presentation = {str(index): text for index, text in enumerate(rendered.splitlines(), 1)
                    if text and index not in claim_lines and not re.match(r"^\[\^e\d+\]: ", text)}
    return rendered, {"claims": mapping, "footnotes": footnotes, "presentation": presentation}


def audit_rendered(markdown: str, mapping: dict, evidence: dict[str, Evidence]) -> list[dict]:
    """Compare emitted bullets directly to the source-bound claim and its allowed literals."""
    issues = []
    lines = markdown.splitlines()
    content_lines = {index: text for index, text in enumerate(lines, 1)
                     if text.startswith(("- ", "  - "))}
    definitions = [line for line in lines if re.match(r"^\[\^e\d+\]: ", line)]
    expected_definitions = []
    for key, label in mapping["footnotes"].items():
        if key not in evidence or not evidence[key].sources:
            _issue(issues, "unresolved_footnote", label, refs=(key,))
            continue
        expected_definitions.append(f"[^{label}]: {_source_label(evidence[key])}")
    if sorted(definitions) != sorted(expected_definitions):
        _issue(issues, "invalid_rendered_footnotes", "briefing")
    for claim in mapping["claims"]:
        rendered = content_lines.pop(claim["line"], "")
        key = claim["claim_id"]
        refs = claim["evidence_keys"]
        cited = [mapping["footnotes"].get(ref) for ref in refs]
        if not refs or any(ref not in evidence for ref in refs) or REFERENCE.findall(rendered) != cited:
            _issue(issues, "rendered_missing_or_invalid_citation", key, rendered, refs)
        if claim["kind"] == "fact":
            if len(refs) != 1 or refs[0] not in evidence:
                continue
            expected = escape(evidence[refs[0]].text)
            if claim["text"] != evidence[refs[0]].text:
                _issue(issues, "fact_binding_mismatch", key, rendered, refs)
        else:
            expected = escape(claim["text"]) + f" *({claim['readiness'].replace('_', ' ').capitalize()})*"
        actual = REFERENCE.sub("", rendered).removeprefix(claim["prefix"]).rstrip()
        allowed = set(NUMBER.findall(expected)) if claim["kind"] == "fact" else set()
        if set(NUMBER.findall(actual)) - allowed or (claim["kind"] == "action" and
                _unbound_action_number(actual, [evidence[ref] for ref in refs if ref in evidence])):
            _issue(issues, "rendered_unbound_number", key, actual, refs)
        if actual != expected:
            _issue(issues, "rendered_text_mismatch", key, actual, refs)
    for index, text in content_lines.items():
        _issue(issues, "unmapped_rendered_claim", f"line.{index}", text)
    claim_lines = {claim["line"] for claim in mapping["claims"]}
    presentation = {str(index): text for index, text in enumerate(lines, 1)
                    if text and index not in claim_lines and not re.match(r"^\[\^e\d+\]: ", text)}
    if presentation != mapping["presentation"]:
        _issue(issues, "unexpected_rendered_text", "briefing")
    return issues
