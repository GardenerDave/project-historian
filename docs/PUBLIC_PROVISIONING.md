# Public Provisioning Guide

This guide covers the public distribution only. It assumes a standard Python
environment and a checkout or exported candidate tree.

## Base mode

The deterministic core works with the repository checkout and standard Python:

```sh
python3 -m historian.cli --help
python3 -m historian.cli validate
python3 -m historian.cli search milestone
python3 -m unittest discover -s tests
```

In the public distribution, the synthetic corpus is installed at `records/`
and `sources/`, so the CLI commands work without private project history.

## Optional retrieval mode

Retrieval support is optional. Install the extra dependencies from
`requirements-retrieval.txt`:

```sh
python3 -m pip install -r requirements-retrieval.txt
```

Model weights are not bundled. Provision any public HuggingFace model
identifiers you want to use in your own environment. The project does not
assume a private cache or private model store.

## Optional local reasoner mode

Grounded reasoning is optional and is enabled by setting an explicit
endpoint:

```sh
export HISTORIAN_REASONER_ENDPOINT=http://127.0.0.1:8080/v1
python3 -m historian.cli ask "what changed the search milestone?"
```

If no endpoint is configured, the command fails closed rather than contacting
an implicit private service.

## Service mode

The read-only service binds to `127.0.0.1:8765` by default:

```sh
python3 -m historian.cli serve
```

That default keeps the service local-only unless you intentionally choose a
different host. If you expose it on a network, do so deliberately and with
your own access controls.

## AnythingLLM integration

Use `scripts/sync_anythingllm_skills.sh` to copy the local skills into the
AnythingLLM Desktop storage area.

Safety behavior:

- it never recursively deletes the destination;
- it replaces only the two Historian skill directories it owns;
- it refuses to touch a destination skill directory that does not carry the
  matching Historian `hubId` in `plugin.json`;
- unrelated operator content is preserved.

Example:

```sh
scripts/sync_anythingllm_skills.sh ~/.config/anythingllm-desktop/storage/plugins/agent-skills
```

See [AnythingLLM integration](docs/ANYTHINGLLM_HISTORIAN_INTEGRATION.md) for
the shared-memory workflow and workspace prompt boundary.

## Generated state

Generated or runtime state should live in ignored locations such as `.work/`
or another documented generated directory. Do not commit caches, model
weights, local indexes, or private intake data.
