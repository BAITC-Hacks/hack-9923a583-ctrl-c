// Set to false when the Python API is ready.
const MOCK_MODE = true;
const API_URL = "/api/recommend";

const MOCK_CONTRACTORS = [
  {
    id: "HK-97737", name: "Фэй Валентайн", categories: ["Фотограф"], city: "Астана", price: 200000,
    event_formats: ["свадьба", "день рождения", "юбилей"], languages: ["русский"], max_hours: 8,
    synthetic: false, score: 96,
    explanation: "Свободна на выбранную дату и специализируется на съёмке свадеб. Укладывается в ваш бюджет и готова работать до 8 часов.",
    matches: ["В бюджете", "Свадьба", "Русский язык", "До 8 часов"]
  },
  {
    id: "HK-98562", name: "Какаши Хатаке", categories: ["Фотограф"], city: "Астана", price: 200000,
    event_formats: ["свадьба", "юбилей"], languages: ["русский"], max_hours: 10,
    synthetic: true, score: 92,
    explanation: "Работает со свадебными торжествами и может остаться на площадке до 10 часов. Стоимость соответствует бюджету, а дата доступна.",
    matches: ["В бюджете", "Свадьба", "Русский язык", "До 10 часов"]
  },
  {
    id: "HK-61323", name: "Тэммари Собаку", categories: ["Фотограф"], city: "Астана", price: 250000,
    event_formats: ["свадьба", "той"], languages: ["русский", "казахский"], max_hours: 12,
    synthetic: false, score: 89,
    explanation: "Подходит для свадеб и знает культурные особенности торжества. Предлагает съёмку на русском и казахском языках, дата свободна.",
    matches: ["В бюджете", "Свадьба", "Русский и казахский", "До 12 часов"]
  }
];

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function getMockResponse(data) {
  const sameCity = MOCK_CONTRACTORS.filter((person) => person.city === data.city && person.categories.includes(data.category));
  if (!sameCity.length) {
    return {
      status: "category_not_found",
      message: `В городе «${data.city}» пока нет подрядчиков категории «${data.category}».`
    };
  }

  // Mock data represents a single photographer category. It only demonstrates
  // empty states; all real recommendation logic belongs to the Python API.
  if (data.category !== "Фотограф" || data.budget < 200000 ||
      (data.language && !sameCity.some((person) => person.languages.includes(data.language))) ||
      (data.duration && !sameCity.some((person) => person.max_hours >= data.duration))) {
    return {
      status: "no_matches",
      message: "Подрядчики этой категории есть, но ни один не подходит под заданные условия."
    };
  }

  return {
    status: "success",
    message: "Мы подобрали варианты на основе ваших условий.",
    results: sameCity.slice(0, 3)
  };
}

/** Send event requirements and return the API response without ranking locally. */
async function getRecommendations(data) {
  if (MOCK_MODE) {
    await wait(820);
    return getMockResponse(data);
  }

  const response = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });

  if (!response.ok) {
    throw new Error(`Recommendation API returned ${response.status}`);
  }

  return response.json();
}
