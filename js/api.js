const API_URL = "http://127.0.0.1:8000/api/recommend";

/** Send the event requirements to the Python API. */
async function getRecommendations(data) {
  const response = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Recommendation API returned ${response.status}: ${detail}`);
  }

  return response.json();
}
