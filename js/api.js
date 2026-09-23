const API_URL = "http://127.0.0.1:8000/api/recommend";
const API_TIMEOUT_MS = 10000;

class RecommendationError extends Error {
  constructor(code, detail) {
    super(detail || code);
    this.name = "RecommendationError";
    this.code = code;
  }
}

function validateRecommendationResponse(response) {
  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isText = (value) => typeof value === "string" && value.trim().length > 0;
  const isTextList = (value) => Array.isArray(value) && value.every(isText);
  const invalid = () => { throw new RecommendationError("invalid_response", "Invalid recommendation response contract"); };

  if (!isObject(response)
    || !["success", "category_not_found", "no_matches"].includes(response.status)
    || !Array.isArray(response.results)
    || !isText(response.message)) invalid();

  if (response.status !== "success") {
    if (response.results.length !== 0) invalid();
    return response;
  }

  if (response.results.length < 1 || response.results.length > 3) invalid();
  for (const person of response.results) {
    if (!isObject(person)
      || !isText(person.id)
      || !isText(person.name)
      || !isText(person.city)
      || !isTextList(person.categories) || person.categories.length === 0
      || !isText(person.explanation)
      || !Number.isFinite(person.score)
      || !Number.isFinite(person.max_score) || person.max_score <= 0
      || typeof person.synthetic !== "boolean") invalid();
    // An unknown price is displayed explicitly rather than converted to zero.
    if (person.price != null && person.price !== ""
      && (typeof person.price !== "number" || !Number.isFinite(person.price) || person.price < 0)) invalid();
    for (const key of ["matches", "event_formats", "languages"]) {
      if (person[key] !== undefined && !isTextList(person[key])) invalid();
    }
    if (person.description != null && typeof person.description !== "string") invalid();
  }
  return response;
}

/** Send the event requirements to the Python API. */
async function getRecommendations(data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      signal: controller.signal
    });

    if (!response.ok) {
      const detail = await response.text();
      const code = response.status === 422 ? "validation"
        : response.status === 503 ? "catalog_unavailable" : "server";
      throw new RecommendationError(code, `Recommendation API returned ${response.status}: ${detail}`);
    }

    let result;
    try {
      result = await response.json();
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new RecommendationError("invalid_response", `Response is not valid JSON: ${error.message}`);
    }
    return validateRecommendationResponse(result);
  } catch (error) {
    if (controller.signal.aborted) throw new RecommendationError("timeout", "Recommendation request timed out");
    if (error instanceof RecommendationError) throw error;
    throw new RecommendationError("network", error.message);
  } finally {
    clearTimeout(timeout);
  }
}
