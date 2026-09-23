const form = document.querySelector("#search-form");
const submitButton = document.querySelector("#submit-button");
const resultsSection = document.querySelector("#results");
const resultsTitle = document.querySelector("#results-title");
const resultsSubtitle = document.querySelector("#results-subtitle");
const resultsCount = document.querySelector("#results-count");
const resultsContent = document.querySelector("#results-content");
const formatter = new Intl.NumberFormat("ru-RU");
let lastRequest = null;

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function formatPrice(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? `${formatter.format(amount)} ₸` : "Цена уточняется";
}

function collectFormData() {
  const values = new FormData(form);
  return {
    city: values.get("city"),
    date: values.get("date"),
    event_format: values.get("event_format"),
    category: values.get("category"),
    budget: Number(values.get("budget")),
    duration: values.get("duration") ? Number(values.get("duration")) : null,
    language: values.get("language") || null
  };
}

function clearValidation() {
  form.querySelectorAll(".field").forEach((field) => field.classList.remove("has-error"));
  form.querySelectorAll(".field-error").forEach((element) => { element.textContent = ""; });
}

function validateForm(data) {
  clearValidation();
  let valid = true;
  const errors = {};

  for (const key of ["city", "date", "event_format", "category"]) {
    if (!data[key]) errors[key] = "Пожалуйста, заполните это поле.";
  }
  if (!data.budget || data.budget <= 0) errors.budget = "Укажите бюджет больше 0 ₸.";
  if (data.date && (data.date < "2026-09-23" || data.date > "2026-12-31")) {
    errors.date = "Выберите дату с 23 сентября по 31 декабря 2026.";
  }

  Object.entries(errors).forEach(([key, message]) => {
    const field = form.querySelector(`[name="${key}"]`)?.closest(".field");
    if (!field) return;
    valid = false;
    field.classList.add("has-error");
    const errorElement = field.querySelector(".field-error");
    if (errorElement) errorElement.textContent = message;
  });
  return valid;
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  submitButton.classList.toggle("is-loading", isLoading);
  submitButton.querySelector("span").textContent = isLoading ? "Подбираем..." : "Подобрать подрядчиков";
  resultsSection.setAttribute("aria-busy", String(isLoading));
  if (isLoading) {
    resultsSection.hidden = false;
    resultsTitle.textContent = "Ищем вашу команду";
    resultsSubtitle.textContent = "Сверяем условия и готовим рекомендации.";
    resultsCount.textContent = "";
    renderLoading();
  }
}

function renderLoading() {
  resultsContent.innerHTML = `<div class="loading-grid" aria-label="Загрузка рекомендаций">
    ${Array.from({ length: 3 }, () => `<div class="skeleton-card" aria-hidden="true"><div class="skeleton-line short"></div><div class="skeleton-line big"></div><div class="skeleton-line medium"></div><div class="skeleton-line"></div><div class="skeleton-block"></div></div>`).join("")}
  </div>`;
}

function renderContractorCard(person, index) {
  const categories = Array.isArray(person.categories) ? person.categories : [person.category].filter(Boolean);
  const formats = Array.isArray(person.event_formats) ? person.event_formats : [];
  const languages = Array.isArray(person.languages) ? person.languages : [];
  const matches = Array.isArray(person.matches) ? person.matches : [];
  const duration = person.max_hours == null ? "Не ограничена присутствием" : `До ${escapeHTML(person.max_hours)} ч`;
  const score = Number.isFinite(Number(person.score)) ? `<span class="score">Совпадение ${escapeHTML(person.score)}%</span>` : "";
  const synthetic = person.synthetic === true ? `<span class="synthetic-badge">Синтетический профиль</span>` : "";
  const matchMarkup = matches.map((match) => `<span class="match-chip">${escapeHTML(match)}</span>`).join("");

  return `<article class="contractor-card">
    <div class="card-topline"><span class="rank">ВАРИАНТ #${index + 1}</span>${score}</div>
    <h3 class="contractor-name">${escapeHTML(person.name || "Подрядчик")}</h3>
    <div class="contractor-id">ID ${escapeHTML(person.id || "—")}</div>
    ${synthetic}
    <div class="card-tags">${categories.map((item) => `<span class="category-tag">${escapeHTML(item)}</span>`).join("")}<span class="location-tag">${escapeHTML(person.city || "Город не указан")}</span></div>
    <p class="price"><small>от </small>${escapeHTML(formatPrice(person.price))}</p>
    <div class="detail-list">
      <div class="detail-row"><span>Форматы</span><span>${escapeHTML(formats.join(", ") || "Не указаны")}</span></div>
      <div class="detail-row"><span>Языки</span><span>${escapeHTML(languages.join(", ") || "Не указаны")}</span></div>
      <div class="detail-row"><span>Длительность</span><span>${duration}</span></div>
    </div>
    <div class="recommendation"><div class="recommendation-label">Почему рекомендуем</div><p>${escapeHTML(person.explanation || "Подходит под параметры вашего мероприятия.")}</p></div>
    ${matchMarkup ? `<div class="match-list" aria-label="Совпадения">${matchMarkup}</div>` : ""}
  </article>`;
}

function renderResults(response) {
  const results = Array.isArray(response.results) ? response.results.slice(0, 3) : [];
  if (response.status === "success" && results.length) {
    resultsTitle.textContent = `Мы подобрали ${results.length} ${results.length === 1 ? "вариант" : "варианта"}`;
    resultsSubtitle.textContent = response.message || "Вот подрядчики, которые подходят вашему событию.";
    resultsCount.textContent = `${results.length} ${results.length === 1 ? "ПОДРЯДЧИК" : "ПОДРЯДЧИКА"}`;
    resultsContent.innerHTML = `<div class="results-grid">${results.map(renderContractorCard).join("")}</div>`;
    return;
  }

  const categoryMissing = response.status === "category_not_found";
  resultsTitle.textContent = categoryMissing ? "Пока не нашли эту категорию" : "Нужны другие условия?";
  resultsSubtitle.textContent = categoryMissing ? "Подскажем, если появятся новые варианты." : "Попробуйте немного изменить параметры поиска.";
  resultsCount.textContent = "";
  const message = response.message || (categoryMissing
    ? "В выбранном городе пока нет подрядчиков этой категории. Попробуйте выбрать другой город или категорию."
    : "Подрядчики этой категории есть, но ни один не подходит под заданные условия.");
  const hint = categoryMissing ? "Выберите другой город или категорию подрядчика." : "Попробуйте увеличить бюджет, изменить дату или убрать дополнительные ограничения.";
  resultsContent.innerHTML = `<div class="empty-state"><div class="empty-icon" aria-hidden="true">${categoryMissing ? "⌕" : "↗"}</div><div><h3>${categoryMissing ? "Вариантов пока нет" : "Совпадений не нашлось"}</h3><p>${escapeHTML(message)} ${escapeHTML(hint)}</p></div></div>`;
}

function renderError(error) {
  resultsTitle.textContent = "Не удалось получить рекомендации";
  const detail = error?.message || "Неизвестная ошибка.";
  const isNetworkError = detail.includes("Failed to fetch") || detail.includes("NetworkError");
  resultsSubtitle.textContent = isNetworkError
    ? "Проверьте, что API запущен по адресу 127.0.0.1:8000."
    : "Проверьте параметры запроса и ответ API.";
  resultsCount.textContent = "";
  resultsContent.innerHTML = `<div class="empty-state"><div class="empty-icon" aria-hidden="true">!</div><div><h3>Что-то пошло не так</h3><p>${escapeHTML(detail)}</p><button class="submit-button" id="retry-button" type="button"><span>Попробовать снова</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h12m-5-5 5 5-5 5"/></svg></button></div></div>`;
  document.querySelector("#retry-button").addEventListener("click", () => runSearch(lastRequest));
}

async function runSearch(data) {
  if (!data) return;
  lastRequest = data;
  setLoading(true);
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const response = await getRecommendations(data);
    renderResults(response);
  } catch (error) {
    console.error("Recommendation request failed:", error);
    renderError(error);
  } finally {
    setLoading(false);
  }
}

form.addEventListener("input", (event) => {
  const field = event.target.closest(".field");
  if (!field || !field.classList.contains("has-error")) return;
  field.classList.remove("has-error");
  field.querySelector(".field-error").textContent = "";
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = collectFormData();
  if (!validateForm(data)) {
    form.querySelector(".field.has-error input, .field.has-error select")?.focus();
    return;
  }
  runSearch(data);
});
