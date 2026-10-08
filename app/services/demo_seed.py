"""Наполнение демо-аккаунта: воспроизводимый рабочий кабинет для показа интерфейса.

Все данные относительны момента входа, поэтому демо всегда выглядит свежим.
Функции идемпотентны: повторный вход дополняет пустые разделы, но не дублирует записи.
"""

from datetime import UTC, datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    CareEvent,
    Diagnosis,
    DiagnosisAnswer,
    DiagnosisFeedback,
    DiagnosisQuestion,
    DiagnosisRevision,
    Garden,
    Plant,
    PlantPhoto,
    Reminder,
    User,
    UserNotification,
)
from app.schemas import DiagnosisResult

DEMO_PHOTO_DIR = "demo-seed"
DEMO_MODEL = "mock-rule-based"
DEMO_PROMPT = "mock-v1"

CREAM = (245, 240, 232)
PAPER = (250, 247, 242)
BARK = (122, 90, 48)
SOIL = (208, 194, 170)

# theme -> (пластина листа, жилки, пятна)
LEAF_THEMES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], list[tuple[float, float, float, tuple[int, ...]]]]] = {
    "healthy": ((108, 160, 86), (58, 104, 52), []),
    "chlorosis": ((188, 196, 106), (96, 116, 52), [
        (0.34, 0.42, 0.055, (228, 214, 118)),
        (0.55, 0.58, 0.045, (236, 222, 132)),
        (0.68, 0.36, 0.038, (222, 206, 112)),
    ]),
    "scab": ((126, 156, 96), (62, 96, 56), [
        (0.32, 0.46, 0.042, (84, 74, 44)),
        (0.45, 0.62, 0.032, (96, 82, 48)),
        (0.58, 0.40, 0.048, (78, 70, 42)),
        (0.68, 0.56, 0.028, (104, 88, 52)),
    ]),
    "powdery": ((104, 150, 88), (54, 98, 50), [
        (0.36, 0.50, 0.070, (238, 238, 232)),
        (0.54, 0.38, 0.058, (244, 244, 240)),
        (0.62, 0.60, 0.062, (232, 234, 226)),
    ]),
    "blurry": ((118, 158, 96), (70, 110, 62), [
        (0.42, 0.50, 0.090, (176, 190, 150)),
    ]),
}

DISCLAIMER = (
    "По фотографии и вашему описанию наиболее вероятны перечисленные причины. "
    "Это предварительная оценка, а не подтверждённый диагноз."
)
SOURCE_IPM = "https://ipm.ucanr.edu/agriculture/floriculture-and-ornamental-nurseries/diagnosing-plant-problems/"
SOURCE_VAAD = "https://piemeklu.reglab.gov.lv/"


def _leaf_layer(width: int, height: int, theme: str) -> Image.Image:
    blade, vein, spots = LEAF_THEMES[theme]
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse([0, int(height * 0.06), width - 1, height - 1], fill=blade + (255,))
    mid = height // 2
    draw.line([(int(width * 0.06), mid), (int(width * 0.92), mid)], fill=vein + (255,), width=max(2, width // 26))
    for step in range(1, 5):
        x = int(width * (0.2 + 0.15 * step))
        draw.line([(x, mid), (min(width - 2, int(x + width * 0.13)), int(height * 0.16))], fill=vein + (255,), width=2)
        draw.line([(x, mid), (min(width - 2, int(x + width * 0.13)), int(height * 0.84))], fill=vein + (255,), width=2)
    for fx, fy, fr, color in spots:
        radius = int(width * fr)
        cx, cy = int(width * fx), int(height * fy)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color + (235,))
    return layer


def _illustration(theme: str) -> bytes:
    """Стилизованная ботаническая иллюстрация вместо подставной фотографии."""
    canvas = Image.new("RGBA", (640, 440), CREAM + (255,))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 356, 640, 440], fill=SOIL + (255,))
    draw.ellipse([40, 30, 150, 130], outline=(232, 208, 150, 255), width=6)
    draw.line([(150, 210), (490, 210)], fill=BARK + (255,), width=3)
    stems = [(190, 210, 150, -22), (320, 210, 190, 8), (450, 210, 140, 26)]
    for cx, top, size, angle in stems:
        draw.line([(cx, 356), (cx, top)], fill=BARK + (255,), width=6)
        leaf = _leaf_layer(size, int(size * 0.62), theme).rotate(angle, expand=True, resample=Image.BICUBIC)
        canvas.alpha_composite(leaf, (cx - leaf.width // 2, top - leaf.height // 2))
    flat = Image.new("RGB", canvas.size, PAPER)
    flat.paste(canvas, (0, 0), canvas)
    if theme == "blurry":
        flat = flat.filter(ImageFilter.GaussianBlur(7))
    buffer = BytesIO()
    flat.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def _store_photo(db: Session, plant: Plant, theme: str, moment: datetime) -> PlantPhoto:
    directory = settings.upload_dir / DEMO_PHOTO_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / f"{plant.id}-{theme}.jpg").resolve()
    content = _illustration(theme)
    target.write_bytes(content)
    photo = PlantPhoto(
        plant_id=plant.id,
        storage_key=f"{DEMO_PHOTO_DIR}/{plant.id}-{theme}.jpg",
        file_path=str(target),
        content_type="image/jpeg",
        size_bytes=len(content),
        created_at=moment,
    )
    db.add(photo)
    return photo


def _result(**overrides: object) -> dict:
    payload = {
        "input_status": "valid",
        "plant_detected": True,
        "image_quality": "acceptable",
        "cannot_analyze_reason": None,
        "analysis_outcome": "possible_problem",
        "disclaimer": DISCLAIMER,
        "analysis_status": "needs_confirmation",
        "possible_causes": [],
        "safe_actions": ["Не применять препараты до уточнения причины."],
        "questions": [],
        "expert_required": False,
        "sources": [SOURCE_IPM],
    }
    payload.update(overrides)
    return DiagnosisResult.model_validate(payload).model_dump()


SCAB_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Парша яблони",
            "cause_code": "disease.venturia_inaequalis",
            "confidence": "high",
            "matched_signs": ["Оливково-бурые пятна на верхней стороне листа", "Пятна появляются сначала на нижних листьях"],
            "missing_signs": ["Нет снимка нижней стороны листа крупным планом"],
            "checks": ["Осмотрите нижнюю сторону: при парше там заметен оливковый бархатистый налёт"],
            "source_ids": ["uc-ipm-scab", "vaad-fungicides"],
        },
        {
            "name": "Дефицит магния",
            "cause_code": "abiotic.magnesium_deficiency",
            "confidence": "medium",
            "matched_signs": ["Межжилтовой хлороз на старых листьях"],
            "missing_signs": ["Неизвестен состав и pH почвы"],
            "checks": ["Сравните молодые и старые листья: при дефиците страдают сначала старые"],
            "source_ids": ["uc-ipm-nutrients"],
        },
        {
            "name": "Повреждение корневой системы",
            "cause_code": "abiotic.root_stress",
            "confidence": "low",
            "matched_signs": ["Общее ослабление кроны"],
            "missing_signs": ["Корни не осмотрены"],
            "checks": ["Проверьте уплотнение грунта приствольного круга и застой воды"],
            "source_ids": ["uc-ipm-diagnosis"],
        },
    ],
    safe_actions=[
        "Соберйте и вывезите за пределы сада опавшие листья — на них зимует инфекция",
        "Вырежьте сильно поражённые ветки и продезинфицируйте секатор между срезами",
        "Не косите траву под деревом в сухую погоду, чтобы не разносить споры",
    ],
    questions=[
        "Пятна есть и на плодах, или только на листьях?",
        "Когда впервые появились пятна — до или после длительных дождей?",
        "Как давно была последняя подкормка и каким удобрением?",
    ],
    sources=[SOURCE_IPM, SOURCE_VAAD],
)

POWDERY_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Мучнистая роса",
            "cause_code": "disease.powdery_mildew",
            "confidence": "high",
            "matched_signs": ["Белый мучнистый налёт на верхней стороне листа", "Теплица, тёплые дни и прохладные ночи"],
            "missing_signs": ["Не известно, открывали ли теплицу для проветривания"],
            "checks": ["Протрите налёт пальцем: при мучнистой росе он стирается частично"],
            "source_ids": ["uc-ipm-powdery-mildew"],
        },
        {
            "name": "Пероноспороз (ложная мучнистая роса)",
            "cause_code": "disease.downy_mildew",
            "confidence": "medium",
            "matched_signs": ["Ограниченные угловатые пятна между жилками"],
            "missing_signs": ["Нет снимка нижней стороны листа"],
            "checks": ["Осмотрите низ листа утром: при пероноспорозе там сероватый налёт"],
            "source_ids": ["uc-ipm-downy-mildew"],
        },
        {
            "name": "Стеснённая посадка и избыточная влажность",
            "cause_code": "abiotic.greenhouse_humidity",
            "confidence": "low",
            "matched_signs": ["Загущённые посадки, конденсат на стенках"],
            "missing_signs": ["Нет данных о ночной температуре"],
            "checks": ["Измерьте влажность в теплице в предрассветные часы"],
            "source_ids": ["uc-ipm-env"],
        },
    ],
    safe_actions=[
        "Проветривайте теплицу весь день, ночную температуру держите выше 16 °C",
        "Удалите нижние листья с пятнами и не компостируйте их",
        "Поливайте только под корень, не намачивая листву",
    ],
    questions=[
        "Налёт появляется на молодых или на нижних листьях?",
        "Как часто вы открывали теплицу на прошлой неделе?",
    ],
    sources=[SOURCE_IPM, SOURCE_VAAD],
)

HEALTHY_RESULT = _result(
    image_quality="good",
    analysis_outcome="no_visible_problem",
    analysis_status="preliminary",
    possible_causes=[],
    safe_actions=[
        "Продолжайте обычный режим полива и подкормок",
        "Осматривайте нижнюю сторону листьев раз в неделю",
    ],
    questions=[],
)

BLURRY_RESULT = _result(
    input_status="poor_quality",
    plant_detected=True,
    image_quality="poor",
    analysis_outcome="cannot_analyze",
    analysis_status="cannot_analyze",
    cannot_analyze_reason=(
        "Снимок сделан в движении: контуры листьев размыты, поэтому признаки повреждения "
        "нельзя отличить от артефакта фокусировки."
    ),
    possible_causes=[],
    safe_actions=[
        "Снимите один лист крупным планом с расстояния 20–25 см",
        "Используйте дневной рассеянный свет и держите телефон неподвижно",
        "Сфотографируйте нижнюю сторону листа отдельно",
    ],
    questions=[],
)

# name -> (вид, taxon_id, место, примечания, тема иллюстрации)
PLANT_SPECS: dict[str, tuple[str, str, str, str, str]] = {
    "Яблоня «Боровица»": ("Malus domestica", "malus.domestica", "open_ground", "Взрослое дерево, плодоносит с 2019 года.", "scab"),
    "Смородина чёрная": ("Ribes nigrum", "ribes.nigrum", "open_ground", "Куст четырёх лет, нужна ежегодная омолаживающая обрезка.", "chlorosis"),
    "Гортензия метельчатая": ("Hydrangea paniculata", "hydrangea.paniculata", "open_ground", "Сорт Limelight, цветение с июля.", "healthy"),
    "Крыжовник": ("Ribes uva-crispa", "ribes.uva-crispa", "open_ground", "Поражается мучнистой росой в жаркие влажные недели.", "healthy"),
    "Томат «Чёрный принц»": ("Solanum lycopersicum", "solanum.lycopersicum", "greenhouse", "Формирую в два стебля.", "powdery"),
    "Огурец": ("Cucumis sativus", "cucumis.sativus", "greenhouse", "Партенокарпический гибрид.", "chlorosis"),
    "Перец сладкий": ("Capsicum annuum", "capsicum.annuum", "greenhouse", "Страдает при температуре ниже 15 °C.", "healthy"),
    "Базилик": ("Ocimum basilicum", "ocimum.basilicum", "greenhouse", "Регулярно прищипываю верхушки.", "healthy"),
    "Петуния ампельная": ("Calibrachoa", "calibrachoa", "balcony", "Кашпо на южном ограждении.", "healthy"),
    "Пеларгония зональная": ("Pelargonium × hortorum", "pelargonium.hortorum", "balcony", "Перенесла на зиму в помещение.", "chlorosis"),
    "Клубника садовая": ("Fragaria × ananassa", "fragaria.ananassa", "balcony", "Кассетные горшки, полив каждые два дня.", "healthy"),
    "Фикус Бенджамина": ("Ficus benjamina", "ficus.benjamina", "indoor", "Стоит в 1,5 м от южного окна.", "blurry"),
    "Монстера деликатесная": ("Monstera deliciosa", "monstera.deliciosa", "indoor", "Опора из кокосового волокна.", "chlorosis"),
    "Фаленопсис": ("Phalaenopsis", "phalaenopsis.hybrida", "indoor", "Кора, полив методом замачивания.", "healthy"),
}

GARDEN_SPECS: list[dict] = [
    {
        "name": "Городской сад",
        "kind": "garden",
        "location": "Москва",
        "soil_type": "Суглинок, pH 6,4",
        "notes": "Четыре сотки за домом: плодовые деревья, ягодные кустарники и цветник.",
        "plants": ["Яблоня «Боровица»", "Смородина чёрная", "Гортензия метельчатая", "Крыжовник"],
    },
    {
        "name": "Теплица из поликарбоната",
        "kind": "greenhouse",
        "location": "Москва",
        "soil_type": "Тепличный субстрат, pH 6,8",
        "notes": "3×6 м, проветривание боковое, полив шлангом под корень.",
        "plants": ["Томат «Чёрный принц»", "Огурец", "Перец сладкий", "Базилик"],
    },
    {
        "name": "Балкон южной стороны",
        "kind": "balcony",
        "location": "Москва",
        "soil_type": "Готовый цветочный грунт",
        "notes": "Шесть кашпо, прямое солнце с 11:00 до 18:00.",
        "plants": ["Петуния ампельная", "Пеларгония зональная", "Клубника садовая"],
    },
    {
        "name": "Комнатная коллекция",
        "kind": "indoor",
        "location": "Москва",
        "soil_type": None,
        "notes": "Зимой сухой воздух от радиатора, влажность 30–35 %.",
        "plants": ["Фикус Бенджамина", "Монстера деликатесная", "Фаленопсис"],
    },
]


def _care_events() -> list[dict]:
    return [
        {"event_type": "watering", "days": 1, "amount": 12.0, "unit": "л", "product": None,
         "notes": "Заметна пересушка верхних 5 см грунта."},
        {"event_type": "fertilizing", "days": 6, "amount": 40.0, "unit": "г", "product": "Сульфат магния",
         "notes": "Внекорневая подкормка по старым листьям."},
        {"event_type": "treatment", "days": 11, "amount": 5.0, "unit": "л", "product": "Раствор соды 1 %",
         "notes": "Первая обработка по налёту, повтор через 7 дней."},
        {"event_type": "pruning", "days": 18, "amount": None, "unit": None, "product": None,
         "notes": "Удалил нижние листья и ветки, лежащие на земле."},
        {"event_type": "observation", "days": 3, "amount": None, "unit": None, "product": None,
         "notes": "Новых пятен за трое суток нет, налёт не распространяется."},
        {"event_type": "transplanting", "days": 42, "amount": None, "unit": None, "product": "Грунт для томатов",
         "notes": "Перевалка в объём на 3 л больше."},
    ]


def _reminders() -> list[dict]:
    return [
        {"kind": "watering", "title": "Полить под корень", "days": 1, "recurrence": "weekly",
         "channel": "web", "notes": "10–12 л на взрослое дерево, утром."},
        {"kind": "inspection", "title": "Осмотреть нижнюю сторону листьев", "days": 2, "recurrence": "weekly",
         "channel": "both", "notes": "Искать налёт, насекомых и паутину."},
        {"kind": "treatment", "title": "Повторная обработка", "days": 4, "recurrence": None,
         "channel": "web", "notes": "Через 7 дней после первой обработки."},
        {"kind": "fertilizing", "title": "Подкормка калием и фосфором", "days": 9, "recurrence": "monthly",
         "channel": "telegram", "notes": "Осенняя норма, не ранее чем через 2 недели после азота."},
        {"kind": "pruning", "title": "Санитарная обрезка", "days": 16, "recurrence": None,
         "channel": "web", "notes": "Только в сухую погоду, секатор продезинфицировать."},
        {"kind": "other", "title": "Укрыть приствольный круг", "days": 24, "recurrence": None,
         "channel": "both", "notes": "5 см компоста, не прикапывая штамб."},
    ]


NOTIFICATIONS: list[dict] = [
    {"kind": "weather_warning", "title": "Порывы ветра до 65 км/ч", "hours": 4,
     "body": "Балкон южной стороны: закрепите кашпо и уберите лёгкие укрытия до вечера."},
    {"kind": "reminder", "title": "Скоро задача по уходу", "hours": 9,
     "body": "Томат «Чёрный принц»: повторная обработка от мучнистой росы."},
    {"kind": "seasonal_task", "title": "Подготовить растения к зиме", "hours": 26,
     "body": "Городской сад: проверьте опоры, укрытие и защиту контейнеров от промерзания."},
    {"kind": "diagnosis_completed", "title": "Диагностика обновлена", "hours": 52,
     "body": "Яблоня «Боровица»: создана версия 2 анализа с учётом ваших ответов."},
    {"kind": "seasonal_task", "title": "Осенняя уборка и санитарный осмотр", "hours": 96,
     "body": "Городской сад: удалите больные остатки отдельно и запишите проблемы сезона."},
    {"kind": "reminder", "title": "Задача по уходу выполнена", "hours": 130,
     "body": "Огурец: полив под корень 8 л — отмечен как выполненный."},
]


def _garden(db: Session, user: User, spec: dict) -> Garden:
    garden = db.scalar(select(Garden).where(Garden.user_id == user.id, Garden.name == spec["name"]))
    if garden:
        return garden
    garden = Garden(
        user_id=user.id,
        name=spec["name"],
        kind=spec["kind"],
        location=spec["location"],
        soil_type=spec["soil_type"],
        notes=spec["notes"],
    )
    db.add(garden)
    db.flush()
    return garden


def _plant(db: Session, garden: Garden, name: str, now: datetime) -> Plant:
    plant = db.scalar(select(Plant).where(Plant.garden_id == garden.id, Plant.name == name))
    if plant:
        return plant
    species, taxon_id, place, notes, _theme = PLANT_SPECS[name]
    plant = Plant(
        garden_id=garden.id,
        name=name,
        species=species,
        taxon_id=taxon_id,
        growing_place=place,
        region="Riga",
        notes=notes,
        created_at=now - timedelta(days=210),
    )
    db.add(plant)
    db.flush()
    return plant


def _seed_care(db: Session, plant: Plant, now: datetime) -> None:
    if db.scalar(select(CareEvent.id).where(CareEvent.plant_id == plant.id)):
        return
    for entry in _care_events()[: 3 + (plant.id % 4)]:
        db.add(CareEvent(
            plant_id=plant.id,
            event_type=entry["event_type"],
            occurred_at=now - timedelta(days=entry["days"]),
            amount=entry["amount"],
            unit=entry["unit"],
            product=entry["product"],
            notes=entry["notes"],
        ))


def _seed_reminders(db: Session, plant: Plant, now: datetime) -> None:
    if db.scalar(select(Reminder.id).where(Reminder.plant_id == plant.id)):
        return
    specs = _reminders()
    count = 2 + (plant.id % 3)
    for entry in specs[:count]:
        due = now + timedelta(days=entry["days"])
        db.add(Reminder(
            plant_id=plant.id,
            kind=entry["kind"],
            title=entry["title"],
            due_at=due,
            notes=entry["notes"],
            recurrence=entry["recurrence"],
            timezone="Europe/Riga",
            preferred_channel=entry["channel"],
        ))
    done = specs[count]
    db.add(Reminder(
        plant_id=plant.id,
        kind=done["kind"],
        title=done["title"],
        due_at=now - timedelta(days=2),
        notes=done["notes"],
        recurrence=done["recurrence"],
        timezone="Europe/Riga",
        preferred_channel=done["channel"],
        completed_at=now - timedelta(days=2, hours=3),
    ))


def _seed_diagnosis(
    db: Session,
    plant: Plant,
    user: User,
    *,
    symptoms: str,
    damaged_part: str,
    result: dict,
    days_ago: int,
    now: datetime,
    revisions: tuple[dict, ...] = (),
    answers: tuple[int, ...] = (),
    feedback: tuple[int, bool, str] | None = None,
) -> None:
    if db.scalar(select(Diagnosis.id).where(Diagnosis.plant_id == plant.id)):
        return
    created = now - timedelta(days=days_ago)
    theme = PLANT_SPECS[plant.name][4]
    photo = _store_photo(db, plant, theme, created)
    db.flush()
    diagnosis = Diagnosis(
        plant_id=plant.id,
        symptoms=symptoms,
        damaged_part=damaged_part,
        status=result["analysis_status"],
        result=result,
        model_name=DEMO_MODEL,
        prompt_version=DEMO_PROMPT,
        current_revision=1 + len(revisions),
        created_at=created,
    )
    diagnosis.photos = [photo]
    db.add(diagnosis)
    db.flush()
    db.add(DiagnosisRevision(
        diagnosis_id=diagnosis.id, version=1, status=result["analysis_status"],
        result=result, model_name=DEMO_MODEL, prompt_version=DEMO_PROMPT, created_at=created,
    ))
    for position, text in enumerate(result["questions"], start=1):
        db.add(DiagnosisQuestion(
            diagnosis_id=diagnosis.id, revision_number=1, position=position,
            text=text, created_at=created,
        ))
    db.flush()
    for position in answers:
        question = db.scalar(
            select(DiagnosisQuestion).where(
                DiagnosisQuestion.diagnosis_id == diagnosis.id,
                DiagnosisQuestion.position == position,
            )
        )
        if question and question.answer is None:
            db.add(DiagnosisAnswer(
                question_id=question.id,
                answer=revisions[-1]["answers"][position - 1] if revisions else "Уточнено при повторном осмотре.",
                created_at=created + timedelta(hours=6),
            ))
    for version, revision_result in enumerate((r["result"] for r in revisions), start=2):
        moment = created + timedelta(days=version - 1)
        db.add(DiagnosisRevision(
            diagnosis_id=diagnosis.id, version=version, status=revision_result["analysis_status"],
            result=revision_result, model_name=DEMO_MODEL, prompt_version=DEMO_PROMPT, created_at=moment,
        ))
        for position, text in enumerate(revision_result["questions"], start=1):
            db.add(DiagnosisQuestion(
                diagnosis_id=diagnosis.id, revision_number=version, position=position,
                text=text, created_at=moment,
            ))
    if revisions:
        diagnosis.result = revisions[-1]["result"]
        diagnosis.status = revisions[-1]["result"]["analysis_status"]
    if feedback:
        rating, helpful, comment = feedback
        db.add(DiagnosisFeedback(
            diagnosis_id=diagnosis.id, user_id=user.id, rating=rating, helpful=helpful,
            comment=comment, created_at=created + timedelta(days=1),
        ))


def _seed_notifications(db: Session, user: User, now: datetime) -> None:
    existing = set(db.scalars(select(UserNotification.deduplication_key)))
    for position, entry in enumerate(NOTIFICATIONS, start=1):
        key = f"demo:notification:{user.id}:{position}"
        if key in existing:
            continue
        event_at = now - timedelta(hours=entry["hours"])
        db.add(UserNotification(
            user_id=user.id,
            kind=entry["kind"],
            title=entry["title"],
            body=entry["body"],
            delivery_channel="both",
            event_at=event_at,
            deduplication_key=key,
            # Демо-уведомления уже «доставлены»: воркер не должен слать их в реальный чат.
            telegram_sent_at=event_at,
            read_at=event_at + timedelta(hours=2) if entry["hours"] > 48 else None,
        ))


def ensure_demo_workspace(db: Session, user: User, *, now: datetime | None = None) -> None:
    """Создаёт или дополняет демо-сад, растения, журнал ухода, напоминания и диагностики."""
    now = now or datetime.now(UTC)
    for spec in GARDEN_SPECS:
        garden = _garden(db, user, spec)
        for plant_name in spec["plants"]:
            plant = _plant(db, garden, plant_name, now)
            _seed_care(db, plant, now)
            _seed_reminders(db, plant, now)
    plants = {
        plant.name: plant
        for plant in db.scalars(select(Plant).where(Plant.garden_id.in_(
            select(Garden.id).where(Garden.user_id == user.id)
        )))
    }

    apple = plants.get("Яблоня «Боровица»")
    if apple:
        _seed_diagnosis(
            db, apple, user, now=now, days_ago=24, damaged_part="leaf",
            symptoms=(
                "Нижние листья покрываются оливково-бурыми пятнами с чётким краем, "
                "к середине лета часть листьев преждевременно опадает."
            ),
            result=SCAB_RESULT,
            revisions=({"result": SCAB_RESULT, "answers": [
                "Пятна есть и на плодах: на двух яблоках буроватые пробковые участки.",
                "Первые пятна появились через 10 дней после трёх дождливых суток.",
                "Последняя подкормка — монофосфат калия в начале августа.",
            ]},),
            answers=(1, 2, 3),
            feedback=(4, True, "Ответы на вопросы помогли заметить пятна на плодах."),
        )

    tomato = plants.get("Томат «Чёрный принц»")
    if tomato:
        _seed_diagnosis(
            db, tomato, user, now=now, days_ago=6, damaged_part="leaf",
            symptoms=(
                "На верхних листьях появился белый мучнистый налёт, к вечеру часть листьев "
                "скручивается. Теплица закрывается на ночь."
            ),
            result=POWDERY_RESULT,
            feedback=(5, True, "Совпало с прогнозом: проветривание исправило ситуацию."),
        )

    ficus = plants.get("Фикус Бенджамина")
    if ficus:
        _seed_diagnosis(
            db, ficus, user, now=now, days_ago=2, damaged_part="whole_plant",
            symptoms=(
                "За неделю опало около двадцати листьев, но снять подробные кадры не удалось — "
                "фотографии получились смазанными."
            ),
            result=BLURRY_RESULT,
        )

    petunia = plants.get("Петуния ампельная")
    if petunia:
        _seed_diagnosis(
            db, petunia, user, now=now, days_ago=13, damaged_part="flower",
            symptoms=(
                "Проверяю кашпо перед выносом на балкон: листья зелёные, без пятен и насекомых, "
                "цветение ровное."
            ),
            result=HEALTHY_RESULT,
            feedback=(5, True, None),
        )

    _seed_notifications(db, user, now)
