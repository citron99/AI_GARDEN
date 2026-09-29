from dataclasses import dataclass
from datetime import datetime

from app.models import Garden, User


@dataclass(frozen=True)
class SeasonalTemplate:
    key: str
    month: int
    day: int
    kinds: tuple[str, ...]
    titles: dict[str, str]
    descriptions: dict[str, str]


TEMPLATES = (
    SeasonalTemplate("seed-plan", 2, 15, ("garden", "vegetable_garden", "greenhouse", "balcony"),
        {"ru": "Спланировать посевы", "lv": "Plānot sēju", "en": "Plan sowing"},
        {"ru": "Проверьте семена, сроки рассады и севооборот.", "lv": "Pārbaudiet sēklas, stādu termiņus un augu maiņu.", "en": "Check seeds, seedling dates, and crop rotation."}),
    SeasonalTemplate("spring-pruning", 3, 15, ("garden", "client_plot"),
        {"ru": "Весенний осмотр и обрезка", "lv": "Pavasara apskate un apgriešana", "en": "Spring inspection and pruning"},
        {"ru": "Осмотрите зимние повреждения; обрезайте только подходящие для весны культуры.", "lv": "Pārbaudiet ziemas bojājumus; apgrieziet tikai pavasarī piemērotās kultūras.", "en": "Inspect winter damage; prune only crops suitable for spring pruning."}),
    SeasonalTemplate("soil-prep", 4, 10, ("garden", "vegetable_garden", "greenhouse", "client_plot"),
        {"ru": "Подготовить почву", "lv": "Sagatavot augsni", "en": "Prepare soil"},
        {"ru": "Оцените влажность, структуру и необходимость анализа почвы.", "lv": "Novērtējiet mitrumu, struktūru un augsnes analīzes nepieciešamību.", "en": "Assess moisture, structure, and whether soil testing is needed."}),
    SeasonalTemplate("frost-check", 5, 10, ("garden", "vegetable_garden", "greenhouse", "balcony", "client_plot"),
        {"ru": "Проверить риск поздних заморозков", "lv": "Pārbaudīt vēlo salnu risku", "en": "Check late-frost risk"},
        {"ru": "Перед высадкой сверяйтесь с прогнозом и подготовьте укрытие.", "lv": "Pirms stādīšanas pārbaudiet prognozi un sagatavojiet segumu.", "en": "Check the forecast before planting out and prepare protection."}),
    SeasonalTemplate("summer-inspection", 6, 15, ("garden", "vegetable_garden", "greenhouse", "balcony", "client_plot"),
        {"ru": "Летний осмотр растений", "lv": "Augu vasaras apskate", "en": "Summer plant inspection"},
        {"ru": "Проверьте влажность, вредителей и признаки болезней до обработки.", "lv": "Pirms apstrādes pārbaudiet mitrumu, kaitēkļus un slimību pazīmes.", "en": "Check moisture, pests, and disease signs before treatment."}),
    SeasonalTemplate("autumn-cleanup", 9, 20, ("garden", "vegetable_garden", "greenhouse", "balcony", "client_plot"),
        {"ru": "Осенняя уборка и санитарный осмотр", "lv": "Rudens uzkopšana un sanitārā apskate", "en": "Autumn cleanup and sanitation check"},
        {"ru": "Удалите больные остатки отдельно и запишите проблемы сезона.", "lv": "Slimās atliekas noņemiet atsevišķi un pierakstiet sezonas problēmas.", "en": "Remove diseased debris separately and record this season's problems."}),
    SeasonalTemplate("winter-protection", 10, 15, ("garden", "vegetable_garden", "greenhouse", "balcony", "client_plot"),
        {"ru": "Подготовить растения к зиме", "lv": "Sagatavot augus ziemai", "en": "Prepare plants for winter"},
        {"ru": "Проверьте опоры, укрытие и защиту контейнеров от промерзания.", "lv": "Pārbaudiet balstus, segumu un konteineru aizsardzību pret sasalšanu.", "en": "Check supports, covers, and container frost protection."}),
)


def seasonal_calendar_items(gardens: list[Garden], user: User, start: datetime, end: datetime) -> list[dict]:
    language = user.language if user.language in {"ru", "lv", "en"} else "ru"
    result: list[dict] = []
    for year in range(start.year, end.year + 1):
        for garden in gardens:
            for template in TEMPLATES:
                if garden.kind not in template.kinds:
                    continue
                occurs = datetime(year, template.month, template.day, 9, tzinfo=start.tzinfo)
                if start <= occurs < end:
                    result.append({
                        "item_type": "seasonal_task",
                        "reference_id": f"seasonal:{template.key}:{garden.id}:{year}",
                        "garden_id": garden.id,
                        "garden_name": garden.name,
                        "event_type": template.key,
                        "title": template.titles[language],
                        "description": template.descriptions[language],
                        "starts_at": occurs,
                        "completed": False,
                    })
    return result