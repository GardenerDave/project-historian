#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_root="${1:-${HOME}/.config/anythingllm-desktop/storage/plugins/agent-skills}"

mkdir -p "${target_root}"

for skill in historian_evidence historian_query; do
  src="${repo_root}/integrations/anythingllm/skills/${skill}"
  dst="${target_root}/${skill}"
  if [ -e "${dst}" ] || [ -L "${dst}" ]; then
    if [ -L "${dst}" ] || [ ! -d "${dst}" ]; then
      echo "refusing to sync: ${dst} exists and is not a plain directory" >&2
      exit 1
    fi
    if [ ! -f "${dst}/plugin.json" ] || ! grep -q "\"hubId\"[[:space:]]*:[[:space:]]*\"${skill}\"" "${dst}/plugin.json"; then
      echo "refusing to sync: ${dst} exists but is not the Historian ${skill} skill (plugin.json hubId mismatch)" >&2
      exit 1
    fi
  else
    mkdir "${dst}"
  fi
  cp -a "${src}/." "${dst}/"
done

echo "Synced AnythingLLM skills to ${target_root}"
