from __future__ import annotations
import argparse, hashlib, json, pathlib, re, subprocess, sys
from historian.ask import ask as historian_ask
from historian.chatgpt_intake import inventory_export as chatgpt_inventory_export
from historian.chatgpt_intake import preflight as chatgpt_preflight
from historian.chatgpt_evidence import resolve_candidate_evidence
from historian.corroboration import (
    find_hold_corroboration_opportunities,
    render_corroboration_json,
    render_corroboration_markdown,
)
from historian.chatgpt_review_index import build_current_intake_candidate_lookup, build_review_index, resolve_current_intake_candidate
from historian.privacy_representation import (
    candidate_requires_privacy_representation,
    build_privacy_safe_representation,
    load_privacy_safe_representation,
    require_semantic_review_ready,
    render_privacy_safe_representation_json,
    render_privacy_safe_representation_markdown,
)
from historian.review_closeout import build_review_closeout, render_review_closeout_json, render_review_closeout_markdown
from historian.interfaces.build_khoj_corpus import validate_projection as validate_khoj_projection
from historian.review_lifecycle import (
    AUTHORITATIVE_SOURCE_NAMESPACE,
    AuthoritativeCandidate,
    ReviewLifecycleEvent,
    append_event_to_log,
    append_lifecycle_event,
    lifecycle_log_path,
    load_lifecycle_events,
    replay_lifecycle_events,
)
from historian.service import serve as historian_serve
from historian.service import retrieve as historian_retrieve

ROOT = pathlib.Path(__file__).resolve().parents[1]
KINDS = {"event", "evidence", "claim", "experiment", "decision", "hypothesis", "failure", "milestone", "artifact", "revision"}
CLASSES = {"observed_fact", "contemporary_interpretation", "retrospective_interpretation"}
STATUSES = {"active", "superseded", "disputed", "candidate"}
ID_RE = re.compile(r"^[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REF_RE = re.compile(r"(?:SRC-[A-Z0-9-]+|[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*)")
REVIEW_EVENT_TYPES = {"evidence_resolved", "privacy_deferred", "privacy_clear_for_semantic_review", "accept_for_next_packet", "duplicate", "low_value", "hold_for_corroboration", "promoted"}

def source_ids():
    data = json.loads((ROOT / "sources" / "manifest.json").read_text(encoding="utf-8"))
    return {x["source_id"] for x in data["sources"]}

def stable_id(kind: str, key: str) -> str:
    return f"{kind[:3].upper()}-{hashlib.sha256(f'{kind}:{key}'.encode()).hexdigest()[:16]}"

def records():
    return sorted((ROOT / "records").rglob("*.md"))

def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"): raise ValueError(f"{path}: missing front matter")
    end = text.find("\n---\n", 4)
    if end < 0: raise ValueError(f"{path}: unterminated front matter")
    data = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1); data[k.strip()] = v.strip()
    return data, text[end + 5:]

def validate():
    errors = []
    seen = {}
    known_sources = source_ids()
    fingerprints = {}
    for p in records():
        try: d, _ = frontmatter(p)
        except ValueError as e: errors.append(str(e)); continue
        for k in ("id", "kind", "title", "assertion_class", "status", "source_ids", "evidence", "relationships", "ingestion"):
            if k not in d: errors.append(f"{p}: missing {k}")
        if d.get("kind") not in KINDS: errors.append(f"{p}: invalid kind")
        if d.get("assertion_class") not in CLASSES: errors.append(f"{p}: invalid assertion_class")
        if d.get("status") not in STATUSES: errors.append(f"{p}: invalid status")
        if not ID_RE.match(d.get("id", "")): errors.append(f"{p}: invalid stable id")
        if d.get("id") in seen: errors.append(f"duplicate id {d['id']}: {p} and {seen[d['id']]}")
        seen[d.get("id")] = p
        for sid in re.findall(r"SRC-[A-Z0-9-]+", d.get("source_ids", "")):
            if sid not in known_sources: errors.append(f"{p}: unknown source_id {sid}")
        for sid in re.findall(r"source_id:\s*(SRC-[A-Z0-9-]+)", d.get("evidence", "")):
            if sid not in known_sources: errors.append(f"{p}: unknown evidence source_id {sid}")
        for raw_hash in re.findall(r"sha256:\s*([^,}\]]+)", d.get("evidence", "")):
            value = raw_hash.strip()
            if value == "null":
                if "hash_status:" not in d.get("evidence", ""): errors.append(f"{p}: null evidence hash requires hash_status")
            elif not SHA_RE.match(value):
                errors.append(f"{p}: evidence sha256 must be 64-hex or explicit null, got {value}")
        for target in re.findall(r"target:\s*([^,}]+)", d.get("relationships", "")):
            target = target.strip()
            if target.startswith("SRC-"):
                if target not in known_sources: errors.append(f"{p}: unknown relationship source {target}")
            elif target not in seen and not any(target == q for q in [x.stem for x in records()]):
                # Validate after the full scan below; this catches missing record IDs.
                pass
        fp = re.search(r"source_fingerprint:\s*([^,}]+)", d.get("ingestion", ""))
        if fp:
            value = fp.group(1).strip()
            if value in fingerprints: errors.append(f"duplicate source_fingerprint {value}: {p} and {fingerprints[value]}")
            fingerprints[value] = p
    record_ids = set(seen)
    for p in records():
        d, _ = frontmatter(p)
        for target in re.findall(r"target:\s*([^,}]+)", d.get("relationships", "")):
            target = target.strip()
            if not target.startswith("SRC-") and target not in record_ids:
                errors.append(f"{p}: unknown relationship target {target}")
    if errors: raise AssertionError("\n".join(errors))
    return len(seen)

def validate_projection():
    return validate_khoj_projection()

def search(term):
    term = term.lower()
    return [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8").splitlines()[1]) for p in records() if term in p.read_text(encoding="utf-8").lower()]

def git_source_id(override=None):
    if override:
        return override
    data = json.loads((ROOT / "sources" / "manifest.json").read_text(encoding="utf-8"))
    for source in data["sources"]:
        if source.get("kind") == "git repository":
            return source["source_id"]
    raise ValueError("no 'git repository' source in sources/manifest.json; pass --source-id explicitly")

def ingest_git(source, limit=30, source_id=None):
    source = pathlib.Path(source).resolve()
    sid = git_source_id(source_id)
    out = ROOT / "records" / "event"; out.mkdir(parents=True, exist_ok=True)
    fmt = "%H%x1f%aI%x1f%s%x1f%an"
    raw = subprocess.check_output(["git", "-C", str(source), "log", f"--format={fmt}", f"-{limit}"], text=True)
    made = []
    for line in raw.splitlines():
        commit, date, subject, author = line.split("\x1f", 3)
        rid = stable_id("event", f"git:{source}:{commit}")
        p = out / f"{rid}.md"
        if not p.exists():
            p.write_text(f"---\nid: {rid}\nkind: event\ntitle: Git commit {commit[:12]} — {subject}\nassertion_class: observed_fact\nstatus: active\nsource_ids: [{sid}]\nevidence: [{{source_id: {sid}, locator: {commit}, sha256: null, hash_status: git-object-id}}]\nrelationships: []\ningestion: {{method: git, source_fingerprint: {commit}}}\n---\n\nCommit `{commit}` by {author} at {date}.\n\nSubject: {subject}\n", encoding="utf-8")
            made.append(rid)
    return made


def _current_intake_resolver(work_root: pathlib.Path):
    lookup = build_current_intake_candidate_lookup(work_root=work_root, repo_root=ROOT)

    def resolve(candidate_id: str, source_namespace: str) -> AuthoritativeCandidate | None:
        row = lookup.get(candidate_id)
        if row is None:
            return None
        if row.get("source_namespace") not in {None, source_namespace}:
            return None
        source_locator = row.get("bounded_evidence_locator") or row.get("source_locator")
        if not isinstance(source_locator, str) or not source_locator.strip():
            source_locator = f"{row.get('conversation_id')}#{row.get('source_opaque_id')}"
        return AuthoritativeCandidate(
            candidate_id=str(row.get("candidate_id") or row.get("stable_candidate_id") or candidate_id),
            source_namespace=str(row.get("source_namespace") or AUTHORITATIVE_SOURCE_NAMESPACE),
            source_conversation_id=str(row.get("conversation_id") or ""),
            source_locator=str(source_locator),
            title=row.get("title"),
            privacy_state=row.get("privacy_state"),
        )

    return resolve


def _review_event_log_path(work_root: pathlib.Path, event_log: pathlib.Path | None) -> pathlib.Path:
    return lifecycle_log_path(work_root, event_log)


def _build_review_event(candidate_id: str, source_namespace: str, event_type: str, *, evidence_locator: str | None = None, batch_id: str | None = None, run_id: str | None = None, note: str | None = None, sequence: int = 0) -> ReviewLifecycleEvent:
    kwargs = {
        "schema_version": "historian.review_lifecycle.v1",
        "event_id": hashlib.sha256(
            "|".join(
                [
                    candidate_id,
                    source_namespace,
                    event_type,
                    evidence_locator or "",
                    batch_id or "",
                    run_id or "",
                    note or "",
                ]
            ).encode("utf-8")
        ).hexdigest()[:24],
        "candidate_id": candidate_id,
        "source_namespace": source_namespace,
        "event_type": event_type,
        "sequence": sequence,
        "batch_id": batch_id,
        "run_id": run_id,
        "note": note,
    }
    if evidence_locator is not None:
        kwargs["evidence_locator"] = evidence_locator
    return ReviewLifecycleEvent(**kwargs)


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _review_state_payload(result, candidate_id: str | None = None) -> dict[str, object]:
    if candidate_id is None:
        return result.to_dict()
    state = result.states.get(candidate_id)
    if state is None:
        raise LookupError(f"no lifecycle state for {candidate_id}")
    return state.to_dict()


def _review_queue_payload(work_root: pathlib.Path, result) -> dict[str, object]:
    review_index = build_review_index(work_root=work_root, repo_root=ROOT)
    lifecycle_state = result.states
    queue_rows = []
    for row in review_index.reviewable_rows:
        candidate_id = row["stable_candidate_id"]
        state = lifecycle_state.get(candidate_id)
        if state and (state.promoted or state.current_semantic_disposition in {"accept_for_next_packet", "duplicate", "low_value", "hold_for_corroboration"} or state.privacy_deferred_active):
            continue
        queue_rows.append(row)
    return {
        "summary": review_index.summary,
        "reviewable_rows": queue_rows,
    }

def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("validate-projection")
    s = sub.add_parser("search"); s.add_argument("term")
    g = sub.add_parser("ingest-git"); g.add_argument("source"); g.add_argument("--limit", type=int, default=30); g.add_argument("--source-id", default=None)
    c = sub.add_parser("chatgpt-intake")
    csub = c.add_subparsers(dest="chatgpt_cmd", required=True)
    cp = csub.add_parser("preflight")
    cp.add_argument("export_path")
    cp.add_argument("--work-root", default=".work/chatgpt-intake")
    ci = csub.add_parser("inventory")
    ci.add_argument("export_path")
    ci.add_argument("--work-root", default=".work/chatgpt-intake")
    default_query_root = ROOT / ".work" / "historian_queries"
    default_smoke_root = ROOT / ".work" / "historian_retrieval_smoke"
    a = sub.add_parser("ask"); a.add_argument("question"); a.add_argument("--work-dir", default=str(default_query_root)); a.add_argument("--endpoint", default=None); a.add_argument("--max-tokens", type=int, default=1536)
    r = sub.add_parser("retrieve-smoke"); r.add_argument("question"); r.add_argument("--work-dir", default=str(default_smoke_root))
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765); s.add_argument("--work-dir", default=str(default_query_root)); s.add_argument("--endpoint", default=None)
    review = sub.add_parser("review")
    review.add_argument("--work-root", default=str(ROOT / ".work" / "chatgpt-intake"))
    rsub = review.add_subparsers(dest="review_cmd", required=True)
    record = rsub.add_parser("record-event")
    record.add_argument("--event-log", default=None)
    record.add_argument("candidate_id")
    record.add_argument("--source-namespace", default=AUTHORITATIVE_SOURCE_NAMESPACE)
    record.add_argument("--event-type", required=True, choices=sorted(REVIEW_EVENT_TYPES))
    record.add_argument("--evidence-locator", default=None)
    record.add_argument("--batch-id", default=None)
    record.add_argument("--run-id", default=None)
    record.add_argument("--note", default=None)
    record.add_argument("--privacy-representation", default=None)
    record.add_argument("--dry-run", action="store_true")
    evidence = rsub.add_parser("evidence-resolved")
    evidence.add_argument("--event-log", default=None)
    evidence.add_argument("candidate_id")
    evidence.add_argument("--source-namespace", default=AUTHORITATIVE_SOURCE_NAMESPACE)
    evidence.add_argument("--evidence-locator", required=True)
    evidence.add_argument("--batch-id", default=None)
    evidence.add_argument("--run-id", default=None)
    evidence.add_argument("--note", default=None)
    evidence.add_argument("--privacy-representation", default=None)
    evidence.add_argument("--dry-run", action="store_true")
    privacy = rsub.add_parser("privacy-representation")
    privacy.add_argument("candidate_id")
    privacy.add_argument("--source-namespace", default=AUTHORITATIVE_SOURCE_NAMESPACE)
    privacy.add_argument("--work-root", default=str(ROOT / ".work" / "chatgpt-intake"))
    privacy.add_argument("--semantic-proposition", default=None)
    privacy.add_argument("--epistemic-shape", default=None)
    privacy.add_argument("--transformation-method", default="manual.regeneration")
    privacy.add_argument("--json", action="store_true")
    privacy.add_argument("--output", default=None)
    privacy.add_argument("--overwrite", action="store_true")
    privacy.add_argument("--require-ready", action="store_true")
    ev = rsub.add_parser("evidence")
    ev.add_argument("candidate_id")
    ev.add_argument("--source-namespace", default=AUTHORITATIVE_SOURCE_NAMESPACE)
    ev.add_argument("--work-root", default=str(ROOT / ".work" / "chatgpt-intake"))
    ev.add_argument("--json", action="store_true")
    ev.add_argument("--show-text", action="store_true")
    corroboration = rsub.add_parser("corroboration")
    corroboration.add_argument("candidate_id")
    corroboration.add_argument("--source-namespace", default=AUTHORITATIVE_SOURCE_NAMESPACE)
    corroboration.add_argument("--event-log", default=None)
    corroboration.add_argument("--hold-source-mode", default="auto", choices=["auto", "legacy", "lifecycle"])
    corroboration.add_argument("--privacy-representation", default=None)
    corroboration.add_argument("--json", action="store_true")
    corroboration.add_argument("--output", default=None)
    corroboration.add_argument("--overwrite", action="store_true")
    state = rsub.add_parser("state")
    state.add_argument("--event-log", default=None)
    state.add_argument("candidate_id", nargs="?")
    state.add_argument("--json", action="store_true")
    history = rsub.add_parser("history")
    history.add_argument("--event-log", default=None)
    history.add_argument("candidate_id")
    history.add_argument("--json", action="store_true")
    queue = rsub.add_parser("queue")
    queue.add_argument("--event-log", default=None)
    queue.add_argument("--json", action="store_true")
    closeout = rsub.add_parser("closeout")
    closeout.add_argument("--event-log", default=None)
    closeout.add_argument("--work-root", default=str(ROOT / ".work" / "chatgpt-intake"))
    closeout.add_argument("--json", action="store_true")
    closeout.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "validate": print(f"valid records: {validate()}")
    elif a.cmd == "validate-projection": print(f"projected records: {validate_projection()}")
    elif a.cmd == "search":
        for p, line in search(a.term): print(f"{p}: {line}")
    elif a.cmd == "retrieve-smoke":
        result = historian_retrieve(a.question, work_root=pathlib.Path(a.work_dir))
        print(json.dumps({
            "status": result.get("status"),
            "request_id": result.get("request_id"),
            "selected_record_ids": result.get("selected_record_ids", []),
            "retrieval_provenance": result.get("retrieval_provenance", {}),
        }, indent=2))
    elif a.cmd == "ask":
        result = historian_ask(a.question, endpoint=a.endpoint, work_root=pathlib.Path(a.work_dir), max_tokens=a.max_tokens)
        if result.get("status") == "ok":
            print(result.get("answer", ""))
            print("cited_record_ids:", ", ".join(result.get("cited_record_ids", [])))
            if result.get("uncertainty_or_limitations"):
                print("uncertainty_or_limitations:", result["uncertainty_or_limitations"])
            if result.get("contradictions_or_missing_evidence"):
                print("contradictions_or_missing_evidence:", ", ".join(result["contradictions_or_missing_evidence"]))
            return 0
        print(json.dumps({"request_id": result.get("request_id"), "status": result.get("status"), "error": result.get("error")}, indent=2))
        return 1
    elif a.cmd == "serve":
        server = historian_serve(
            host=a.host,
            port=a.port,
            work_root=pathlib.Path(a.work_dir),
            endpoint=a.endpoint,
        )
        print(f"Historian read-only service listening on http://{a.host}:{a.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    elif a.cmd == "chatgpt-intake":
        try:
            export_path = pathlib.Path(a.export_path)
            if a.chatgpt_cmd == "preflight":
                result = chatgpt_preflight(export_path, work_root=pathlib.Path(a.work_root))
                if result["ok"]:
                    print(json.dumps({"status": "ok", "checks": [item["name"] for item in result["checks"]]}, indent=2, sort_keys=True))
                    return 0
                failed = [item["name"] for item in result["checks"] if not item["ok"]]
                print(json.dumps({"status": "failed", "failed_checks": failed}, indent=2, sort_keys=True))
                return 1
            elif a.chatgpt_cmd == "inventory":
                result = chatgpt_inventory_export(export_path, work_root=pathlib.Path(a.work_root))
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0
            return 0
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"error: {exc}")
            return 1
    elif a.cmd == "review":
        work_root = pathlib.Path(a.work_root)
        event_log = _review_event_log_path(work_root, pathlib.Path(a.event_log) if getattr(a, "event_log", None) else None)
        resolver = _current_intake_resolver(work_root)
        try:
            if a.review_cmd == "evidence":
                resolved = resolve_candidate_evidence(
                    a.candidate_id,
                    a.source_namespace,
                    work_root=work_root,
                    show_text=a.show_text,
                )
                payload = resolved.to_dict()
                if a.json:
                    _print_json(payload)
                else:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                return 0 if resolved.status == "resolved" else 1
            if a.review_cmd == "record-event":
                event = _build_review_event(
                    a.candidate_id,
                    a.source_namespace,
                    a.event_type,
                    evidence_locator=a.evidence_locator,
                    batch_id=a.batch_id,
                    run_id=a.run_id,
                    note=a.note,
                )
                validated = event
                from historian.review_lifecycle import validate_lifecycle_event
                history = load_lifecycle_events(event_log) if event_log.exists() else []
                candidate = validate_lifecycle_event(validated, prior_events=history, resolver=resolver)
                privacy_representation_path = getattr(a, "privacy_representation", None)
                if candidate_requires_privacy_representation(candidate.privacy_state) and a.event_type in {"privacy_clear_for_semantic_review", "accept_for_next_packet", "duplicate", "low_value", "hold_for_corroboration"}:
                    if not privacy_representation_path:
                        raise ValueError("privacy-safe representation required for privacy-gated candidate")
                    representation = require_semantic_review_ready(
                        load_privacy_safe_representation(pathlib.Path(privacy_representation_path)),
                        expected_candidate_id=a.candidate_id,
                        expected_source_namespace=a.source_namespace,
                    )
                    if representation.evidence_reference is None:
                        raise ValueError("privacy-safe representation missing provenance reference")
                elif privacy_representation_path:
                    representation = require_semantic_review_ready(
                        load_privacy_safe_representation(pathlib.Path(privacy_representation_path)),
                        expected_candidate_id=a.candidate_id,
                        expected_source_namespace=a.source_namespace,
                    )
                    if representation.evidence_reference is None:
                        raise ValueError("privacy-safe representation missing provenance reference")
                if a.dry_run:
                    projected_history = append_lifecycle_event(history, validated, resolver=resolver)
                    projected = replay_lifecycle_events(projected_history)
                    _print_json(
                        {
                            "status": "dry_run",
                            "event": json.loads(json.dumps(validated, default=lambda o: o.__dict__, sort_keys=True)),
                            "projected_state": projected.states[a.candidate_id].to_dict(),
                        }
                    )
                    return 0
                appended = append_event_to_log(event_log, validated, resolver=resolver)
                _print_json({"status": "ok", "event_count": len(appended), "event": appended[-1].__dict__})
                return 0
            if a.review_cmd == "evidence-resolved":
                event = _build_review_event(
                    a.candidate_id,
                    a.source_namespace,
                    "evidence_resolved",
                    evidence_locator=a.evidence_locator,
                    batch_id=a.batch_id,
                    run_id=a.run_id,
                    note=a.note,
                )
                from historian.review_lifecycle import validate_lifecycle_event
                history = load_lifecycle_events(event_log) if event_log.exists() else []
                candidate = validate_lifecycle_event(event, prior_events=history, resolver=resolver)
                privacy_representation_path = getattr(a, "privacy_representation", None)
                if candidate_requires_privacy_representation(candidate.privacy_state) and not privacy_representation_path:
                    raise ValueError("privacy-safe representation required for privacy-gated candidate")
                if privacy_representation_path:
                    representation = require_semantic_review_ready(
                        load_privacy_safe_representation(pathlib.Path(privacy_representation_path)),
                        expected_candidate_id=a.candidate_id,
                        expected_source_namespace=a.source_namespace,
                    )
                    if representation.evidence_reference is None:
                        raise ValueError("privacy-safe representation missing provenance reference")
                if a.dry_run:
                    projected_history = append_lifecycle_event(history, event, resolver=resolver)
                    projected = replay_lifecycle_events(projected_history)
                    _print_json(
                        {
                            "status": "dry_run",
                            "event": json.loads(json.dumps(event, default=lambda o: o.__dict__, sort_keys=True)),
                            "projected_state": projected.states[a.candidate_id].to_dict(),
                        }
                    )
                    return 0
                appended = append_event_to_log(event_log, event, resolver=resolver)
                _print_json({"status": "ok", "event_count": len(appended), "event": appended[-1].__dict__})
                return 0
            if a.review_cmd == "privacy-representation":
                resolved = resolve_candidate_evidence(
                    a.candidate_id,
                    a.source_namespace,
                    work_root=work_root,
                    show_text=False,
                )
                if resolved.status != "resolved":
                    print(f"error: {resolved.error or resolved.status}")
                    return 1
                representation = build_privacy_safe_representation(
                    resolved,
                    proposition=a.semantic_proposition,
                    epistemic_shape=a.epistemic_shape,
                    transformation_method=a.transformation_method,
                )
                if a.require_ready:
                    require_semantic_review_ready(
                        representation,
                        expected_candidate_id=a.candidate_id,
                        expected_source_namespace=a.source_namespace,
                    )
                output = render_privacy_safe_representation_json(representation) if a.json else render_privacy_safe_representation_markdown(representation)
                if a.output:
                    output_path = pathlib.Path(a.output)
                    if output_path.exists() and not a.overwrite:
                        raise FileExistsError(f"{output_path}: output exists; use --overwrite to replace")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(output, encoding="utf-8")
                else:
                    print(output, end="" if output.endswith("\n") else "\n")
                return 0
            if a.review_cmd == "corroboration":
                privacy_representation_path = getattr(a, "privacy_representation", None)
                result = find_hold_corroboration_opportunities(
                    a.candidate_id,
                    a.source_namespace,
                    work_root=work_root,
                    privacy_representation_path=pathlib.Path(privacy_representation_path) if privacy_representation_path else None,
                    hold_source_mode=a.hold_source_mode,
                    event_log=pathlib.Path(a.event_log) if getattr(a, "event_log", None) else None,
                )
                output = render_corroboration_json(result) if a.json else render_corroboration_markdown(result)
                if a.output:
                    output_path = pathlib.Path(a.output)
                    if output_path.exists() and not a.overwrite:
                        raise FileExistsError(f"{output_path}: output exists; use --overwrite to replace")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(output, encoding="utf-8")
                else:
                    print(output, end="" if output.endswith("\n") else "\n")
                return 0 if result.status == "ok" else 1
            events = load_lifecycle_events(event_log) if event_log.exists() else []
            result = replay_lifecycle_events(events)
            if a.review_cmd == "state":
                if a.candidate_id:
                    payload = _review_state_payload(result, a.candidate_id)
                else:
                    payload = result.to_dict()
                if a.json:
                    _print_json(payload)
                else:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            if a.review_cmd == "history":
                state = result.states.get(a.candidate_id)
                if state is None:
                    raise LookupError(f"no lifecycle state for {a.candidate_id}")
                payload = {"candidate_id": a.candidate_id, "events": [json.loads(json.dumps(event.__dict__, sort_keys=True)) for event in state.events]}
                if a.json:
                    _print_json(payload)
                else:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            if a.review_cmd == "queue":
                payload = _review_queue_payload(work_root, result)
                if a.json:
                    _print_json(payload)
                else:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            if a.review_cmd == "closeout":
                closeout_result = build_review_closeout(
                    work_root=work_root,
                    repo_root=ROOT,
                    event_log=pathlib.Path(a.event_log) if getattr(a, "event_log", None) else None,
                )
                output = render_review_closeout_json(closeout_result) if a.json else render_review_closeout_markdown(closeout_result)
                if a.output:
                    output_path = pathlib.Path(a.output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(output, encoding="utf-8")
                else:
                    print(output, end="" if output.endswith("\n") else "\n")
                return 0
            return 0
        except (LookupError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"error: {exc}")
            return 1
    else: print(f"ingested new records: {len(ingest_git(a.source, a.limit, a.source_id))}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
