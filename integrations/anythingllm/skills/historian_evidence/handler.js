const HISTORIAN_BASE_URL = process.env.HISTORIAN_BASE_URL || "http://127.0.0.1:8765/v1";

async function callHistorianEvidence(question) {
  const response = await fetch(`${HISTORIAN_BASE_URL}/evidence`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ question }),
  });
  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (error) {
    return JSON.stringify({
      status: "failed",
      error_code: "invalid_response",
      error: "Historian evidence service returned malformed JSON",
    });
  }

  return JSON.stringify({
    status: payload.status || (response.ok ? "ok" : "failed"),
    question: payload.question || question,
    selected_record_ids: payload.selected_record_ids || [],
    evidence: payload.evidence || [],
    retrieval_provenance: payload.retrieval_provenance || {},
    parsed_constraints: payload.parsed_constraints || {},
    error_code: payload.error_code,
    error: payload.error,
  });
}

module.exports.runtime = {
  handler: async function ({ question }) {
    try {
      return await callHistorianEvidence(question);
    } catch (error) {
      return JSON.stringify({
        status: "failed",
        error_code: "historian_unavailable",
        error: "Historian evidence service is unavailable",
      });
    }
  },
};
