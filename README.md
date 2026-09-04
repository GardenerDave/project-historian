# Project Historian

Project Historian is a provenance-first project memory system. It keeps
source evidence, curated records, stable IDs, assertion classes, explicit
provenance, and retrieval/search layers separate from model output and
reasoning.

Evidence remains authoritative. Derived summaries, retrieval results, and
model reasoning do not become authority just because they are convenient.

## What is included

- `historian/` - the CLI, validation, search, read-only service, retrieval
  helpers, and optional reasoner client.
- `schema/` - the record specification and JSON schema.
- `records/` - a synthetic example record corpus installed at the public
  distribution root.
- `sources/` - the matching synthetic example source corpus and manifest.
- `tests/` - deterministic tests that validate the public tree and example
  corpus.
- `integrations/anythingllm/` - the local AnythingLLM skills and workspace
  prompt.
- `requirements-retrieval.txt` - optional retrieval/reasoner dependencies.

The shipped `records/` and `sources/` directories are synthetic example data.
They demonstrate the format and tooling; they are not historical evidence
from the maintainer's private project archive.

## Quick start

From a fresh checkout or an exported public candidate:

```sh
python3 -m historian.cli --help
python3 -m historian.cli validate
python3 -m historian.cli search milestone
python3 -m unittest discover -s tests
python3 -m pytest tests -q
```

The example corpus is installed at `records/` and `sources/` in the public
distribution, so these commands work without private data.

## Optional retrieval

Retrieval and reasoner support are optional. Install the extra dependencies
from `requirements-retrieval.txt` when you want grounded retrieval or the
optional reasoner client:

```sh
python3 -m pip install -r requirements-retrieval.txt
```

Model weights are not bundled. Public HuggingFace model identifiers can be
provisioned separately using your own environment and cache settings. The
project does not assume any private local cache.

## Optional reasoner

Deterministic validation and search do not require a reasoner. Grounded
reasoning is optional and uses an explicit endpoint when configured.

Supported configuration includes `HISTORIAN_REASONER_ENDPOINT`:

```sh
export HISTORIAN_REASONER_ENDPOINT=http://127.0.0.1:8080/v1
python3 -m historian.cli ask "what milestone stabilized search?"
```

If no endpoint is configured, `ask` fails closed instead of contacting a
private default service.

## Service mode

The read-only service binds to `127.0.0.1:8765` by default so it stays local
unless you intentionally change the host and port:

```sh
python3 -m historian.cli serve
python3 -m historian.cli serve --host 127.0.0.1 --port 8765
```

That default is deliberate: the public package is meant to be locally
inspectable, not exposed on a network by accident.

## AnythingLLM

See [AnythingLLM integration](docs/ANYTHINGLLM_HISTORIAN_INTEGRATION.md) for
the local skills and workspace prompt.

The included skills can query Historian evidence, but they do not gain write
authority over the evidence store. They also inherit the Project Historian
PolyForm Noncommercial license.

## Public, private, generated

- Private working Historian: may contain real project history and evidence.
- Public distribution: produced deterministically from a positive whitelist
  and synthetic example data.
- Generated/runtime state: caches, indexes, temporary files, model assets,
  and other local execution artifacts.

The public release is derived from the working Historian. It does not replace
the private authoritative evidence store.

## Licensing

Project Historian is source-available for noncommercial use under the
PolyForm Noncommercial License 1.0.0.

Commercial or for-profit use requires explicit permission. See
[LICENSE.md](LICENSE.md) and [COMMERCIAL_USE.md](COMMERCIAL_USE.md).

The usage examples in the licensing documents are illustrative, not
exhaustive. They do not define the full boundary between permitted
noncommercial use and uses requiring commercial permission. The PolyForm
Noncommercial License 1.0.0 controls.
