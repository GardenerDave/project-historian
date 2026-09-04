from __future__ import annotations
import argparse, hashlib, json, pathlib, re, subprocess, sys
from historian.ask import ask as historian_ask
from historian.chatgpt_intake import inventory_export as chatgpt_inventory_export
from historian.chatgpt_intake import preflight as chatgpt_preflight
from historian.interfaces.build_khoj_corpus import validate_projection as validate_khoj_projection
from historian.service import serve as historian_serve
from historian.service import retrieve as historian_retrieve

ROOT = pathlib.Path(__file__).resolve().parents[1]
KINDS = {"event", "evidence", "claim", "experiment", "decision", "hypothesis", "failure", "milestone", "artifact", "revision"}
CLASSES = {"observed_fact", "contemporary_interpretation", "retrospective_interpretation"}
STATUSES = {"active", "superseded", "disputed", "candidate"}
ID_RE = re.compile(r"^[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REF_RE = re.compile(r"(?:SRC-[A-Z0-9-]+|[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*)")

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
    else: print(f"ingested new records: {len(ingest_git(a.source, a.limit, a.source_id))}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
