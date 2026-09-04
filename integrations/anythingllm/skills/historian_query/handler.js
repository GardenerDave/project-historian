const HISTORIAN_BASE_URL = process.env.HISTORIAN_BASE_URL || "http://127.0.0.1:8765/v1";

async function callHistorianQuery(question) {
  const response = await fetch(`${HISTORIAN_BASE_URL}/query`, {
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
      error: "Historian query service returned malformed JSON",
    });
  }

  return JSON.stringify({
    status: payload.status || (response.ok ? "ok" : "failed"),
    answer: payload.answer,
    cited_record_ids: payload.cited_record_ids || [],
    evidence_used: payload.evidence_used || [],
    uncertainty_or_limitations: payload.uncertainty_or_limitations || "",
    contradictions_or_missing_evidence: payload.contradictions_or_missing_evidence || [],
    validation: payload.validation || {},
    error_code: payload.error_code,
    error: payload.error,
  });
}

module.exports.runtime = {
  handler: async function ({ question }) {
    try {
      return await callHistorianQuery(question);
    } catch (error) {
      return JSON.stringify({
        status: "failed",
        error_code: "historian_unavailable",
        error: "Historian query service is unavailable",
      });
    }
  },
};
