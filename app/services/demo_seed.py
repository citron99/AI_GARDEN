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
    "aphids": ((86, 132, 74), (48, 88, 48), [
        (0.62, 0.44, 0.020, (186, 204, 128)),
        (0.68, 0.52, 0.017, (200, 214, 146)),
        (0.58, 0.56, 0.016, (178, 198, 120)),
        (0.71, 0.38, 0.015, (196, 208, 140)),
        (0.65, 0.62, 0.018, (184, 202, 126)),
        (0.54, 0.40, 0.014, (204, 216, 152)),
        (0.40, 0.50, 0.030, (96, 104, 78)),
        (0.34, 0.60, 0.022, (104, 112, 84)),
    ]),
    "spider_mite": ((128, 156, 104), (68, 104, 58), [
        (0.30, 0.42, 0.010, (226, 232, 200)),
        (0.36, 0.56, 0.009, (232, 236, 208)),
        (0.42, 0.36, 0.010, (224, 230, 198)),
        (0.48, 0.60, 0.009, (230, 234, 206)),
        (0.54, 0.44, 0.011, (228, 232, 202)),
        (0.60, 0.58, 0.010, (234, 238, 212)),
        (0.66, 0.40, 0.009, (226, 230, 198)),
        (0.72, 0.54, 0.010, (230, 234, 206)),
        (0.50, 0.50, 0.075, (214, 222, 188)),
    ]),
    "blight": ((104, 140, 84), (56, 92, 52), [
        (0.30, 0.46, 0.085, (88, 84, 62)),
        (0.52, 0.60, 0.072, (70, 68, 54)),
        (0.66, 0.38, 0.060, (104, 96, 70)),
        (0.42, 0.32, 0.038, (126, 118, 88)),
    ]),
    "scorch": ((110, 148, 88), (60, 100, 54), [
        (0.76, 0.50, 0.100, (112, 78, 48)),
        (0.62, 0.34, 0.058, (130, 92, 56)),
        (0.86, 0.62, 0.048, (96, 66, 42)),
        (0.55, 0.62, 0.034, (138, 100, 62)),
    ]),
    "scale": ((98, 140, 82), (54, 94, 50), [
        (0.34, 0.44, 0.026, (150, 110, 72)),
        (0.42, 0.58, 0.022, (120, 86, 54)),
        (0.52, 0.38, 0.028, (156, 116, 78)),
        (0.60, 0.60, 0.024, (126, 90, 58)),
        (0.68, 0.46, 0.021, (148, 108, 70)),
        (0.50, 0.50, 0.080, (214, 208, 140)),
    ]),
    "blotch": ((116, 152, 92), (64, 102, 56), [
        (0.34, 0.44, 0.070, (126, 74, 44)),
        (0.55, 0.60, 0.050, (150, 96, 52)),
        (0.68, 0.38, 0.062, (108, 64, 40)),
        (0.45, 0.30, 0.034, (216, 206, 146)),
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

APHID_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Большая смородинная тль",
            "cause_code": "pest.aphis_fabae",
            "confidence": "high",
            "matched_signs": ["Глянцевитые зелёные личинки плотными колониями на молодых верхушках", "Листья скручиваются кверху и деформируются"],
            "missing_signs": ["Не сняты крупным планом нижняя сторона листа и основание побега"],
            "checks": ["Проведите пальцем по верхушке: колония сминается, остаётся липкий налёт"],
            "source_ids": ["uc-ipm-aphids"],
        },
        {
            "name": "Муравьи, которые «разводят» тлю",
            "cause_code": "abiotic.ant_activity",
            "confidence": "medium",
            "matched_signs": ["По штамбу и ветвям идут муравьи", "Колонии появляются снизу вверх"],
            "missing_signs": ["Не проверено, есть ли муравьиные ходы у основания куста"],
            "checks": ["Обмотайте основание штамба лентой с клеевым слоем и посмотрите, остановятся ли муравьи"],
            "source_ids": ["uc-ipm-ants"],
        },
        {
            "name": "Избыток азотных подкормок",
            "cause_code": "abiotic.nitrogen_excess",
            "confidence": "low",
            "matched_signs": ["Быстрый рыхлый прирост, который любит тля"],
            "missing_signs": ["Нет данных о последних дозах азота"],
            "checks": ["Вспомните, чем и когда подкармливали куст после цветения"],
            "source_ids": ["uc-ipm-nutrients"],
        },
    ],
    safe_actions=[
        "Выщипните 5–7 см верхушек с колониями и вывезите их за пределы сада, не в компост",
        "Смойте оставшуюся тлю сильной струёй воды утром, чтобы листья высохли до ночи",
        "Не обрабатывайте цветущий куст инсектицидами: они убьют сирфид и божьих коровок, которые подавляют тлю сами",
    ],
    questions=[
        "Есть ли муравьиные ходы у основания куста?",
        "Скручены только верхушки или и листья в середине ветви?",
        "Чем подкармливали смородину после цветения?",
    ],
    sources=[SOURCE_IPM],
)

SPIDER_MITE_RESULT = _result(
    image_quality="acceptable",
    possible_causes=[
        {
            "name": "Паутинный клещ",
            "cause_code": "pest.tetranychus_urbicae",
            "confidence": "high",
            "matched_signs": ["Меловатые точки-проколы по всей пластинке", "Тонкая паутина в узлах жилок и на нижней стороне"],
            "missing_signs": ["Нет снимка нижней стороны листа на просвет"],
            "checks": ["Поднесите лист к свету: при клеще видны движущиеся точки и блестящая плёнка"],
            "source_ids": ["uc-ipm-spider-mite"],
        },
        {
            "name": "Пересушённый воздух теплицы",
            "cause_code": "abiotic.low_humidity",
            "confidence": "medium",
            "matched_signs": ["Жарко, проветривание недостаточное, листья теряют тургор к полудню"],
            "missing_signs": ["Нет измерений влажности за последние дни"],
            "checks": ["Измерьте влажность в полдень: ниже 45 % — условия для размножения клеща"],
            "source_ids": ["uc-ipm-env"],
        },
        {
            "name": "Дефицит калия",
            "cause_code": "abiotic.potassium_deficiency",
            "confidence": "low",
            "matched_signs": ["Подсыхающий край старых листьев"],
            "missing_signs": ["Состав питания не записан"],
            "checks": ["Сравните старые и молодые листья: при калии страдают сначала старые"],
            "source_ids": ["uc-ipm-nutrients"],
        },
    ],
    safe_actions=[
        "Поднимите влажность: опрыскивайте дорожки и стенки, а не сами листья в жару",
        "Удалите сильно повреждённые листья и вывезите их, не компостируйте",
        "Не снимайте все поражения за одну неделю — клещ быстро восстанавливается на сухом воздухе",
    ],
    questions=[
        "Как давно теплица не открывалась на целый день?",
        "Есть ли паутина на соседних растениях?",
        "Как часто вы поливаете огурец в жару?",
    ],
    sources=[SOURCE_IPM],
)

IRON_CHLOROSIS_RESULT = _result(
    image_quality="good",
    analysis_outcome="possible_problem",
    analysis_status="preliminary",
    possible_causes=[
        {
            "name": "Дефицит железа из-за повышенного pH",
            "cause_code": "abiotic.iron_deficiency",
            "confidence": "high",
            "matched_signs": ["Межжилтовой хлороз: прожилки остаются тёмно-зелёными", "Сначала светлеют молодые листья на верхушках"],
            "missing_signs": ["Нет свежего замера pH почвы"],
            "checks": ["Измерьте pH у корней: выше 6,5 железо становится недоступ растению"],
            "source_ids": ["uc-ipm-nutrients"],
        },
        {
            "name": "Уплотнение грунта и подтопление корней",
            "cause_code": "abiotic.root_stress",
            "confidence": "medium",
            "matched_signs": ["Хлороз усиливается после обильных поливов"],
            "missing_signs": ["Не проверен дренаж и застой воды"],
            "checks": ["Проверьте, уходит ли вода из приствольного круга за час"],
            "source_ids": ["uc-ipm-diagnosis"],
        },
    ],
    safe_actions=[
        "Отмените раскисление под этой посадкой: гортензии нужен кислый субстрат",
        "Пролейте приствольный круг водой с подкислителем (9 % уксус, 10 мл на 10 л) и замульчируйте корой",
        "Хелат железа по листу — только разовая помощь, он не заменяет подкислённую подложку",
    ],
    questions=[
        "Сколько извести или древесной золы внесли за сезон?",
        "Хлороз появился до или после жарких недель?",
    ],
    sources=[SOURCE_IPM],
)

BLIGHT_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Фитофтороз картофеля",
            "cause_code": "disease.phytophthora_infestans",
            "confidence": "high",
            "matched_signs": ["Бурые расплывчатые пятна с бледным краем, листья мокнут", "Признаки появились после холодных рос и дождливой недели"],
            "missing_signs": ["Нет снимка нижней стороны листа с белым налётом спорулирования"],
            "checks": ["Осмотрите низ листа утром: при фитофторозе там беловатый венчик мицелия"],
            "source_ids": ["uc-ipm-blight", "vaad-fungicides"],
        },
        {
            "name": "Макроспориоз (бурая пятнистость)",
            "cause_code": "disease.alternaria_solani",
            "confidence": "medium",
            "matched_signs": ["Сухие концентрические пятна на старых листьях"],
            "missing_signs": ["Пятна не измерены и не сняты на просвет"],
            "checks": ["Сравните: у макроспориоза пятна сухие и многочисленные, у фитофтороза — расплывчатые и водянистые"],
            "source_ids": ["uc-ipm-leaf-spot"],
        },
        {
            "name": "Нехватка влаги в корневой зоне гряды",
            "cause_code": "abiotic.drought_stress",
            "confidence": "low",
            "matched_signs": ["Верхние листья вянут к полудню"],
            "missing_signs": ["Нет записей о поливах и мульчировании"],
            "checks": ["Проверьте влажность грунта на глубине 10 см"],
            "source_ids": ["uc-ipm-water"],
        },
    ],
    safe_actions=[
        "Срежьте и вывезите ботву с пятнами до того, как споры попадут в клубни",
        "Увеличьте окучивание и мульчу: клубни под слоем почвы почти не заражаются",
        "Переберите урожай в сухую погоду и отложите больные клубни отдельно, не храните их с здоровыми",
    ],
    questions=[
        "Есть ли белый налёт на нижней стороне листьев по утрам?",
        "Как давно не окучивали гряду?",
        "Хранили ли вы прошлый урожай вместе с ботвой?",
    ],
    sources=[SOURCE_IPM, SOURCE_VAAD],
)

MONILIA_RESULT = _result(
    image_quality="acceptable",
    possible_causes=[
        {
            "name": "Монилиальный ожог",
            "cause_code": "disease.monilinia",
            "confidence": "high",
            "matched_signs": ["Верхушки побегов бурые и засохшие, как обожжённые", "Поражение идёт от конца побега к основанию"],
            "missing_signs": ["Не сняты плодовые веточки и основания поражённых побегов"],
            "checks": ["Найдёте под корой овальные подушки серого цвета — это спороношение гриба"],
            "source_ids": ["uc-ipm-monilia"],
        },
        {
            "name": "Подмерзание побегов",
            "cause_code": "abiotic.frost_damage",
            "confidence": "medium",
            "matched_signs": ["Потемнение только на верхушках, кора на штамбе цела"],
            "missing_signs": ["Неизвестна зимняя температура и наличие снегового покрова"],
            "checks": ["Срежьте верхушку: древесина равномерно зелёная — это мороз, бурая с пятнами — инфекция"],
            "source_ids": ["uc-ipm-diagnosis"],
        },
        {
            "name": "Загущённая посадка без проветривания",
            "cause_code": "abiotic.poor_airflow",
            "confidence": "low",
            "matched_signs": ["Внутри куста долго держится сырость"],
            "missing_signs": ["Нет схемы посадок рядом стоящих растений"],
            "checks": ["Оцените, проходит ли воздух через середину куста"],
            "source_ids": ["uc-ipm-env"],
        },
    ],
    safe_actions=[
        "Вырежьте поражённые побеги на 10–15 см ниже бурых пятен, до здоровой древесины",
        "Дезинфицируйте секатор между каждым резом — иначе разнесёте инфекцию по кусту",
        "Соберите и вывезите опавшие листья и мумифицированные плоды, на них гриб зимует",
    ],
    questions=[
        "Побеги потемнели после заморозков или после тёплой влажной недели?",
        "Есть ли такие же признаки на соседних кустарниках?",
    ],
    sources=[SOURCE_IPM],
)

SCALE_RESULT = _result(
    image_quality="acceptable",
    possible_causes=[
        {
            "name": "Щитовка и ложнощитовка",
            "cause_code": "pest.coccidae",
            "confidence": "high",
            "matched_signs": ["Плоские коричневые бляшки 2–4 мм, которые снимаются ногтем", "Липкие выделения на листьях ниже колонии"],
            "missing_signs": ["Нет снимка ствола и черешков, где обычно сидят взрослые самки"],
            "checks": ["Подцепите бляшку: если она не давится, а отходит целой — это щитовка"],
            "source_ids": ["uc-ipm-scale"],
        },
        {
            "name": "Сухой воздух у радиатора",
            "cause_code": "abiotic.dry_air",
            "confidence": "medium",
            "matched_signs": ["Влажность 30–35 %, кончики листьев сохнут"],
            "missing_signs": ["Нет регулярных замеров влажности в комнате"],
            "checks": ["Поставьте гигрометр на уровне горшка на неделю"],
            "source_ids": ["uc-ipm-houseplants"],
        },
        {
            "name": "Сажа на медвяной росе",
            "cause_code": "disease.sooty_mould",
            "confidence": "low",
            "matched_signs": ["Тёмный налёт, который частично стирается влажной тканью"],
            "missing_signs": ["Не проверено, есть ли налёт на подоконнике под растением"],
            "checks": ["Сотрите налёт: если под ним зелёная кожица — это сажистый гриб, а не некроз"],
            "source_ids": ["uc-ipm-sooty"],
        },
    ],
    safe_actions=[
        "Снимите видимые бляшки губкой, смоченной в мыльном растворе, по черешкам и стволу",
        "Изолируйте растение от остальных и осмотрите их: щитовка раньше всего заметна на соседнем горшке",
        "Повторите обработку через 7–10 дней: личинки выходят из яиц позже, чем взрослые особи",
    ],
    questions=[
        "Как давно растение не пересаживали и в каком субстрате оно стоит?",
        "Есть ли липкие пятна на подоконнике под горшком?",
    ],
    sources=[SOURCE_IPM],
)

GOOSEBERRY_POWDERY_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Американская мучнистая роса крыжовника",
            "cause_code": "disease.cryptostigma_cuspidatum",
            "confidence": "high",
            "matched_signs": ["Белый паутинистый налёт, который переходит с листьев на ягоды", "Ягоды перестают расти и покрываются ржавой сеткой"],
            "missing_signs": ["Не сняты верхушки текущего прироста"],
            "checks": ["Сотрите налёт: у американской росы он плотный и не сходит до конца"],
            "source_ids": ["uc-ipm-powdery-mildew", "vaad-fungicides"],
        },
        {
            "name": "Слишком ранняя сильная обрезка",
            "cause_code": "agritechnical.hard_pruning",
            "confidence": "medium",
            "matched_signs": ["Бурный молодой прирост, наиболее чувствительный к росе"],
            "missing_signs": ["Не записаны сроки и сила обрезки"],
            "checks": ["Вспомните, сколько старых ветвей удалено за одну обрезку"],
            "source_ids": ["uc-ipm-pruning"],
        },
        {
            "name": "Дефицит калия и загущение",
            "cause_code": "abiotic.potassium_deficiency",
            "confidence": "low",
            "matched_signs": ["Подсыхающий край старых листьев внутри куста"],
            "missing_signs": ["Нет данных осенней подкормки"],
            "checks": ["Осмотрите середину куста: густые ветви держат сырость"],
            "source_ids": ["uc-ipm-nutrients"],
        },
    ],
    safe_actions=[
        "Вырежьте и вывезите верхушки прироста с налётом, оставляя здоровую часть ветви",
        "Не перекармливайте азотом: рыхлые побеги поражаются сильнее",
        "Пролейте под корень водой с зольным настоем (200 г золы на 10 л, настоять сутки) — источник калия",
    ],
    questions=[
        "Какой сорт крыжовника и сколько ему лет?",
        "Была ли обрезка этой весной и насколько сильная?",
    ],
    sources=[SOURCE_IPM, SOURCE_VAAD],
)

CHESTNUT_BLOTCH_RESULT = _result(
    image_quality="good",
    possible_causes=[
        {
            "name": "Антракноз и пятнистости листьев каштана",
            "cause_code": "disease.gnomonia_aesculi",
            "confidence": "high",
            "matched_signs": ["Крупные угловатые бурые пятна между жилками", "Поражение сильнее на нижнем ярусе кроны"],
            "missing_signs": ["Нет снимка опавших листьев и корневой шейки"],
            "checks": ["Осмотрите самые нижние ветви: инфекция обычно поднимается снизу вверх"],
            "source_ids": ["uc-ipm-anthracnose"],
        },
        {
            "name": "Солнечный и ветровой ожог края листа",
            "cause_code": "abiotic.sunscald",
            "confidence": "medium",
            "matched_signs": ["Коричневая сухая кайма по краю без переходов в мякоть"],
            "missing_signs": ["Неизвестна экспозиция посадки в полуденные часы"],
            "checks": ["Сравните теневую и солнечную сторону кроны"],
            "source_ids": ["uc-ipm-env"],
        },
        {
            "name": "Недостаток влаги в приствольном круге",
            "cause_code": "abiotic.drought_stress",
            "confidence": "low",
            "matched_signs": ["Листья преждевременно желтеют и опадают в жару"],
            "missing_signs": ["Нет данных о поливах и мульче"],
            "checks": ["Проверьте влажность грунта на глубине 15 см под мульчей"],
            "source_ids": ["uc-ipm-water"],
        },
    ],
    safe_actions=[
        "Соберите и вывезите опавшие листья — на них гриб переходит в следующую весну",
        "Замульчируйте приствольный круг 8–10 см щепой, не прикапывая корневую шейку",
        "Поливайте редко и обильно, под корень, чтобы не намачивать крону",
    ],
    questions=[
        "Пятна появились до или после самой долгой жаркой недели?",
        "Убираете ли вы опавшую листву под деревом?",
    ],
    sources=[SOURCE_IPM],
)

# name -> (вид, taxon_id, место, примечания, тема иллюстрации)
PLANT_SPECS: dict[str, tuple[str, str, str, str, str]] = {
    # Городской сад: плодовое дерево, ягодные кустарники, декоративная группа.
    "Яблоня «Боровица»": ("Malus domestica", "malus.domestica", "open_ground", "Взрослое дерево, плодоносит с 2019 года. Крона 4 м, приствольный круг под мульчей.", "scab"),
    "Смородина чёрная «Ядрёная»": ("Ribes nigrum", "ribes.nigrum", "open_ground", "Куст четырёх лет, нужна ежегодная омолаживающая обрезка. Молодые верхушки охотно заселяет тля.", "aphids"),
    "Гортензия метельчатая «Limelight»": ("Hydrangea paniculata", "hydrangea.paniculata", "open_ground", "Цветение с июля. Осенью под куст внесли известь, теперь светлеют молодые листья.", "chlorosis"),
    "Крыжовник «Слабошиповатый-2»": ("Ribes uva-crispa", "ribes.uva-crispa", "open_ground", "В жаркие влажные недели ягоды и верхушки покрываются белым налётом.", "powdery"),
    "Малина «Таруса»": ("Rubus idaeus", "rubus.idaeus", "open_ground", "Две канавы по 2,5 м на шпалере, полив 15 л на метр канавы раз в неделю.", "healthy"),
    # Лесосад: ягодные кустарники и деревья у воды, минимальный уход.
    "Хеномелис «Nivalis»": ("Chaenomeles × superba", "chaenomeles.superba", "open_ground", "Кустарник 1,5 м, цветёт в мае, ягоды идут на настойку. После влажной недели темнеют верхушки побегов.", "scorch"),
    "Каштан павия": ("Aesculus pavia", "aesculus.pavia", "open_ground", "Невысокое дерево на солнечной опушке у воды. К концу лета на листьях появляются бурые угловатые пятна.", "blotch"),
    "Ирга канадская «Smoky»": ("Amelanchier alnifolia", "amelanchier.alnifolia", "open_ground", "Плодоносит с третьего года, часть урожая отдают птицам, поэтому ставят сетку.", "scab"),
    "Скумпия кожевенная «Royal Purple»": ("Cotinus coggygria", "cotinus.coggygria", "open_ground", "Дымный эффект соцветий на бедной почве и полном солнце. Пересадку переносит плохо.", "healthy"),
    "Пузыреплодник «Dart's Gold»": ("Physocarpus opulifolius", "physocarpus.opulifolius", "open_ground", "Живая изгородь вдоль дорожки, стрижка дважды за сезон.", "chlorosis"),
    # Огород: гряды с разным циклом — от чеснока до кабачка.
    "Картофель «Невский»": ("Solanum tuberosum", "solanum.tuberosum", "open_ground", "Гряда 4 м, окучивание дважды за сезон. В сырую неделю на ботве появляются расплывчатые бурые пятна.", "blight"),
    "Морковь «Нантская-4»": ("Daucus carota", "daucus.carota", "open_ground", "Посев в апреле, прореживание до 5 см, полив малыми нормами, чтобы корнеплоды не растрескивались.", "healthy"),
    "Кабачок «Грибовский»": ("Cucurbita pepo", "cucurbita.pepo", "open_ground", "Два растения на южном краю гряды, сбор каждые два дня, иначе завязи грубеют.", "powdery"),
    "Чеснок озимый «Любаша»": ("Allium sativum", "allium.sativum", "open_ground", "Зубки под зиму, мульча торфом 6 см, стрелки удаляю сразу.", "healthy"),
    "Фасоль вьющаяся «Победитель»": ("Phaseolus vulgaris", "phaseolus.vulgaris", "open_ground", "Опора из сетки 2 м, полив критичен в пору налива бобов.", "healthy"),
    "Томат «Санька»": ("Solanum lycopersicum", "solanum.lycopersicum", "open_ground", "Ранний низкорослый, ведётся без пасынкования, подвязка к кольям.", "powdery"),
    # Теплица: культуры, которым нужны проветривание и полив под корень.
    "Томат «Чёрный принц»": ("Solanum lycopersicum", "solanum.lycopersicum", "greenhouse", "Формирую в два стебля, подвязка к шпалере, полив под корень раз в четыре дня.", "powdery"),
    "Огурец «Мурашка»": ("Cucumis sativus", "cucumis.sativus", "greenhouse", "Партенокарпический гибрид, требует влажности выше 60 %. В жару на листьях появляется меловатая точечность.", "spider_mite"),
    "Перец сладкий «Калифорнийское чудо»": ("Capsicum annuum", "capsicum.annuum", "greenhouse", "Страдает при температуре ниже 15 °C, подкормка раз в две недели.", "healthy"),
    "Базилик «Геновезе»": ("Ocimum basilicum", "ocimum.basilicum", "greenhouse", "Регулярно прищипываю верхушки, срезка раз в неделю до цветения.", "healthy"),
    "Салат «Лолло Росса»": ("Lactuca sativa", "lactuca.sativa", "greenhouse", "Подсев каждые 12 дней, чтобы держать конвейер листьев.", "healthy"),
    # Балкон: контейнеры с быстрой потерей влаги и разным режимом полива.
    "Петуния ампельная": ("Calibrachoa", "calibrachoa", "balcony", "Кашпо на южном ограждении, подкормка раз в неделю, прищипка после затяжных дождей.", "healthy"),
    "Пеларгония зональная «Розовый шарм»": ("Pelargonium × hortorum", "pelargonium.hortorum", "balcony", "Перенесена на зиму в помещение, грунт между поливами просыхает на треть.", "chlorosis"),
    "Клубника садовая «Альбион»": ("Fragaria × ananassa", "fragaria.ananassa", "balcony", "Кассетные горшки, полив каждые два дня, усы удаляю для урожая.", "healthy"),
    "Лаванда узколистная «Hidcote Blue»": ("Lavandula angustifolia", "lavandula.angustifolia", "balcony", "Горшок с дренажем 5 см, полив редко, зимует на застеклённом балконе.", "healthy"),
    "Мята перечная": ("Mentha × piperita", "mentha.piperita", "balcony", "Держу отдельно: расползается по всему балкону. Стрижка каждые три недели.", "healthy"),
    # Комнатная коллекция: сухой воздух, досветка, разные субстраты.
    "Фикус Бенджамина": ("Ficus benjamina", "ficus.benjamina", "indoor", "Стоит в 1,5 м от южного окна, не любит перестановок и сбрасывает листья при сухом воздухе.", "blurry"),
    "Монстера деликатесная": ("Monstera deliciosa", "monstera.deliciosa", "indoor", "Опора из кокосового волокна. На черешках появились коричневые бляшки, которые снимаются ногтем.", "scale"),
    "Фаленопсис": ("Phalaenopsis", "phalaenopsis.hybrida", "indoor", "Кора вместо грунта, полив замачиванием раз в неделю, осенью досветка.", "healthy"),
    "Сансевиерия трёхполосная": ("Dracaena trifasciata", "dracaena.trifasciata", "indoor", "Зимой полив раз в две недели, гибнет от воды в поддоне.", "healthy"),
    "Хлорофитум хохматый": ("Chlorophytum comosum", "chlorophytum.comosum", "indoor", "Отводки укореняю в стакане с водой, кончики листьев сохнут от жёсткой водопроводной воды.", "chlorosis"),
}

GARDEN_SPECS: list[dict] = [
    {
        "name": "Городской сад",
        "kind": "garden",
        "location": "Москва",
        "soil_type": "Суглинок, pH 6,4",
        "notes": "Четыре сотки за домом: плодовые деревья, ягодные кустарники и цветник.",
        "plants": [
            "Яблоня «Боровица»",
            "Смородина чёрная «Ядрёная»",
            "Гортензия метельчатая «Limelight»",
            "Крыжовник «Слабошиповатый-2»",
            "Малина «Таруса»",
        ],
    },
    {
        "name": "Лесосад у пруда",
        "kind": "garden",
        "location": "Москва",
        "soil_type": "Купированный суглинок с компостом, pH 5,8",
        "notes": "Ярусные посадки у воды: ягодные кустарники, два молодых дерева, подлесок без перекопки.",
        "plants": [
            "Хеномелис «Nivalis»",
            "Каштан павия",
            "Ирга канадская «Smoky»",
            "Скумпия кожевенная «Royal Purple»",
            "Пузыреплодник «Dart's Gold»",
        ],
    },
    {
        "name": "Огород у забора",
        "kind": "vegetable_garden",
        "location": "Москва",
        "soil_type": "Высокие гряды, компост 50 %, pH 6,7",
        "notes": "Шесть грядов вдоль забора: овощи длительного хранения и быстрая зелень по краю.",
        "plants": [
            "Картофель «Невский»",
            "Морковь «Нантская-4»",
            "Кабачок «Грибовский»",
            "Чеснок озимый «Любаша»",
            "Фасоль вьющаяся «Победитель»",
            "Томат «Санька»",
        ],
    },
    {
        "name": "Теплица из поликарбоната",
        "kind": "greenhouse",
        "location": "Москва",
        "soil_type": "Тепличный субстрат, pH 6,8",
        "notes": "3×6 м, проветривание боковое, полив шлангом под корень, мульча скошенной травой.",
        "plants": [
            "Томат «Чёрный принц»",
            "Огурец «Мурашка»",
            "Перец сладкий «Калифорнийское чудо»",
            "Базилик «Геновезе»",
            "Салат «Лолло Росса»",
        ],
    },
    {
        "name": "Балкон южной стороны",
        "kind": "balcony",
        "location": "Москва",
        "soil_type": "Готовый цветочный грунт",
        "notes": "Восемь кашпо, прямое солнце с 11:00 до 18:00, грунт пересыхает за сутки.",
        "plants": [
            "Петуния ампельная",
            "Пеларгония зональная «Розовый шарм»",
            "Клубника садовая «Альбион»",
            "Лаванда узколистная «Hidcote Blue»",
            "Мята перечная",
        ],
    },
    {
        "name": "Комнатная коллекция",
        "kind": "indoor",
        "location": "Москва",
        "soil_type": None,
        "notes": "Зимой сухой воздух от радиатора, влажность 30–35 %. Горшки на трёх окнах.",
        "plants": [
            "Фикус Бенджамина",
            "Монстера деликатесная",
            "Фаленопсис",
            "Сансевиерия трёхполосная",
            "Хлорофитум хохматый",
        ],
    },
]


# Журнал ухода: event_type, days (сколько дней назад), amount/unit, product, notes.
CARE_SPECS: dict[str, list[dict]] = {
    "Яблоня «Боровица»": [
        {"event_type": "watering", "days": 1, "amount": 12.0, "unit": "л", "product": None, "notes": "Грунт под мульчей просох на 8 см, проливал медленно в три приёма."},
        {"event_type": "observation", "days": 3, "amount": None, "unit": None, "product": None, "notes": "На двух плодах буроватые пробковые пятна, на нижних листьях новые оливковые точки."},
        {"event_type": "treatment", "days": 8, "amount": 6.0, "unit": "л", "product": "Настой золы (200 г на 10 л)", "notes": "Пролил приствольный круг после сбора опавших листьев."},
        {"event_type": "pruning", "days": 18, "amount": None, "unit": None, "product": None, "notes": "Вырезал две ветви с пятнами, секатор дезинфицировал между резами."},
        {"event_type": "fertilizing", "days": 26, "amount": 40.0, "unit": "г", "product": "Монофосфат калия", "notes": "Осенняя норма по влажному грунту, азот не давал."},
    ],
    "Смородина чёрная «Ядрёная»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "После смывания новых скрученных листьев нет, муравьи у основания остались."},
        {"event_type": "treatment", "days": 4, "amount": 3.0, "unit": "л", "product": "Мыльный раствор (4 г на л)", "notes": "Смыл колонии тли с верхушек утром, к вечеру листья подсохли."},
        {"event_type": "watering", "days": 2, "amount": 8.0, "unit": "л", "product": None, "notes": "По канавкам вдоль проекции кроны, не по центру куста."},
        {"event_type": "pruning", "days": 9, "amount": None, "unit": None, "product": None, "notes": "Выщипнул 5–7 см верхушек с колониями и вывез за пределы участка."},
        {"event_type": "fertilizing", "days": 14, "amount": 50.0, "unit": "г", "product": "Сульфат калия", "notes": "В канавку по краю кроны, затем полив."},
    ],
    "Гортензия метельчатая «Limelight»": [
        {"event_type": "watering", "days": 1, "amount": 15.0, "unit": "л", "product": None, "notes": "Подкислённая вода (10 мл 9 % уксуса на 10 л), пролив по мульче из коры."},
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Молодые листья светлее старых, прожилки остаются тёмными."},
        {"event_type": "fertilizing", "days": 7, "amount": 10.0, "unit": "мл", "product": "Хелат железа", "notes": "По листу в вечерние часы, разовая помощь до измерения pH."},
        {"event_type": "treatment", "days": 21, "amount": 12.0, "unit": "л", "product": "Вода с торфом (2 л на 10 л)", "notes": "Пролив для подкисления после внесения извести осенью."},
        {"event_type": "pruning", "days": 40, "amount": None, "unit": None, "product": None, "notes": "Удалил отцветшие соцветия на первую пару листьев."},
    ],
    "Крыжовник «Слабошиповатый-2»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "Налёт на ягодах не стирается до конца, новые верхушки чистые."},
        {"event_type": "pruning", "days": 5, "amount": None, "unit": None, "product": None, "notes": "Вырезнул верхушки прироста с налётом до здоровой древесины."},
        {"event_type": "watering", "days": 3, "amount": 10.0, "unit": "л", "product": None, "notes": "Под корень, листву не намачивал."},
        {"event_type": "fertilizing", "days": 12, "amount": 1.0, "unit": "л", "product": "Зольный настой", "notes": "Пролив по влажному грунту, источник калия."},
        {"event_type": "treatment", "days": 19, "amount": 5.0, "unit": "л", "product": "Раствор соды 1 %", "notes": "Первая обработка по налёту, повтор через неделю."},
    ],
    "Малина «Таруса»": [
        {"event_type": "watering", "days": 2, "amount": 15.0, "unit": "л", "product": None, "notes": "15 л на метр канавы, вода из бочки, прогретая за день."},
        {"event_type": "pruning", "days": 6, "amount": None, "unit": None, "product": None, "notes": "Вырезал отплодоносившие побеги у основания."},
        {"event_type": "observation", "days": 4, "amount": None, "unit": None, "product": None, "notes": "Молодые побеги ровные, следов клеща на нижней стороне нет."},
        {"event_type": "fertilizing", "days": 16, "amount": 60.0, "unit": "г", "product": "Компост из своей кучи", "notes": "Слой 3 см по канаве, слегка заделан тяпкой."},
    ],
    "Хеномелис «Nivalis»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "Бурые верхушки трёх побегов, на штамбе кора цела."},
        {"event_type": "pruning", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Срезал поражённые концы на 12 см ниже бурых пятен, инструмент протирал спиртом."},
        {"event_type": "watering", "days": 5, "amount": 10.0, "unit": "л", "product": None, "notes": "У пруда влаги достаточно, полив только в жару под корень."},
        {"event_type": "treatment", "days": 14, "amount": 4.0, "unit": "л", "product": "Настой хвоща 1:5", "notes": "Опрыскивание кроны в сухое безветренное утро."},
    ],
    "Каштан павия": [
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Пятна держатся на нижнем ярусе кроны, выше по стволу листьев чистые."},
        {"event_type": "watering", "days": 4, "amount": 20.0, "unit": "л", "product": None, "notes": "Редко и обильно, вода уходит за 40 минут."},
        {"event_type": "treatment", "days": 10, "amount": 8.0, "unit": "л", "product": None, "notes": "Пролив приствольного круга, потом мульча щепой 8 см."},
        {"event_type": "pruning", "days": 30, "amount": None, "unit": None, "product": None, "notes": "Убрал две сухие ветви внутри кроны для проветривания."},
    ],
    "Ирга канадская «Smoky»": [
        {"event_type": "observation", "days": 3, "amount": None, "unit": None, "product": None, "notes": "Птицы обледили верхнюю часть кроны, сетку поставил на две стороны."},
        {"event_type": "watering", "days": 7, "amount": 10.0, "unit": "л", "product": None, "notes": "По периметру приствольного круга."},
        {"event_type": "pruning", "days": 22, "amount": None, "unit": None, "product": None, "notes": "Проредил поросль у основания, оставил четыре самых сильных ствола."},
        {"event_type": "fertilizing", "days": 35, "amount": 50.0, "unit": "г", "product": "Нитроаммофоска", "notes": "Весенняя норма, заделана в верхний слой."},
    ],
    "Скумпия кожевенная «Royal Purple»": [
        {"event_type": "observation", "days": 6, "amount": None, "unit": None, "product": None, "notes": "«Дымка» соцветий равномерная, листьев без пятен."},
        {"event_type": "pruning", "days": 24, "amount": None, "unit": None, "product": None, "notes": "Короткая стрижка для новых побегов с яркой антоциановой окраской."},
        {"event_type": "watering", "days": 12, "amount": 6.0, "unit": "л", "product": None, "notes": "Только в засуху, переувлажнения скумпия не выносит."},
    ],
    "Пузыреплодник «Dart's Gold»": [
        {"event_type": "pruning", "days": 5, "amount": None, "unit": None, "product": None, "notes": "Формовочная стрижка изгороди по натянутому шнуру."},
        {"event_type": "watering", "days": 2, "amount": 12.0, "unit": "л", "product": None, "notes": "Полосой вдоль изгороди, 12 л на погонный метр."},
        {"event_type": "observation", "days": 8, "amount": None, "unit": None, "product": None, "notes": "После стрижки молодой прирост светлее обычного — проверил pH, 7,1."},
        {"event_type": "fertilizing", "days": 20, "amount": 40.0, "unit": "г", "product": "Полное минеральное удобрение", "notes": "По влажной земле после стрижки."},
    ],
    "Картофель «Невский»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "На трёх листьях расплывчатые бурые пятна с бледным краем, по утрам низ листа влажный."},
        {"event_type": "treatment", "days": 2, "amount": 5.0, "unit": "л", "product": "Настой чеснока (200 г на 10 л)", "notes": "Опрыскал ботву вечером, чтобы сдержать спорулирование."},
        {"event_type": "pruning", "days": 4, "amount": None, "unit": None, "product": None, "notes": "Срезал и вывёз листья с самыми крупными пятнами, компост не использовал."},
        {"event_type": "watering", "days": 6, "amount": 20.0, "unit": "л", "product": None, "notes": "По бороздам, после полива окучил выше."},
        {"event_type": "transplanting", "days": 45, "amount": None, "unit": None, "product": "Органика на гряду", "notes": "Перекопка с компостом, гряда поднята на 12 см."},
    ],
    "Морковь «Нантская-4»": [
        {"event_type": "watering", "days": 1, "amount": 6.0, "unit": "л", "product": None, "notes": "Малыми нормами по бороздам, чтобы корнеплоды не растрескивались."},
        {"event_type": "observation", "days": 3, "amount": None, "unit": None, "product": None, "notes": "Ботва ровная, объедённого слизнями края не найден."},
        {"event_type": "pruning", "days": 12, "amount": None, "unit": None, "product": None, "notes": "Прореживание до 5 см, лишние сеянцы вынес с гряды."},
        {"event_type": "fertilizing", "days": 25, "amount": 30.0, "unit": "г", "product": "Сульфат калия", "notes": "Подкормка в августе, азот не давал."},
    ],
    "Кабачок «Грибовский»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "На двух старых листьях белый налёт, молодые чистые."},
        {"event_type": "watering", "days": 1, "amount": 8.0, "unit": "л", "product": None, "notes": "Тёплой водой под корень, листву не мочил."},
        {"event_type": "treatment", "days": 3, "amount": 4.0, "unit": "л", "product": "Раствор соды 1 %", "notes": "Первая обработка по налёту, повтор через 7 дней."},
        {"event_type": "pruning", "days": 7, "amount": None, "unit": None, "product": None, "notes": "Удалил три переросшие завязи и два затеняющих листа."},
        {"event_type": "fertilizing", "days": 15, "amount": 1.0, "unit": "л", "product": "Настой крапивы 1:10", "notes": "По влажному грунту в период плодоношения."},
    ],
    "Чеснок озимый «Любаша»": [
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Перья ровные, кончики не сохнут, головки завязываются."},
        {"event_type": "pruning", "days": 8, "amount": None, "unit": None, "product": None, "notes": "Выломал стрелки на высоте 10 см, чтобы питание шло в головку."},
        {"event_type": "watering", "days": 5, "amount": 5.0, "unit": "л", "product": None, "notes": "По борозде, за три недели до уборки поливы прекращу."},
        {"event_type": "fertilizing", "days": 30, "amount": 25.0, "unit": "г", "product": "Нитроаммофоска", "notes": "Весенняя подкормка по талому грунту."},
    ],
    "Фасоль вьющаяся «Победитель»": [
        {"event_type": "watering", "days": 1, "amount": 9.0, "unit": "л", "product": None, "notes": "Налив бобов — самый требовательный этап, почва держу влажной."},
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Листья зелёные, по нижним бобам слизней нет."},
        {"event_type": "pruning", "days": 9, "amount": None, "unit": None, "product": None, "notes": "Собрал переросшие бобы, чтобы куст продолжал завязывать."},
        {"event_type": "transplanting", "days": 60, "amount": None, "unit": None, "product": "Сетка-опора 2 м", "notes": "Установил опору, побеги направил вручную."},
    ],
    "Томат «Санька»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "Низовые листья с бурыми точками, на кисти плоды ровные."},
        {"event_type": "watering", "days": 2, "amount": 6.0, "unit": "л", "product": None, "notes": "Под колышек, редкий и обильный полив."},
        {"event_type": "treatment", "days": 5, "amount": 3.0, "unit": "л", "product": "Настой золы", "notes": "Опрыскал по листу в вечернее время."},
        {"event_type": "pruning", "days": 11, "amount": None, "unit": None, "product": None, "notes": "Удалил жёлтые листья ниже первой кисти."},
    ],
    "Томат «Чёрный принц»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "Новых пятен нет, налёт на верхних листьях не распространяется."},
        {"event_type": "treatment", "days": 3, "amount": 5.0, "unit": "л", "product": "Раствор соды 1 %", "notes": "Повторная обработка по налёту, через 7 дней после первой."},
        {"event_type": "watering", "days": 2, "amount": 8.0, "unit": "л", "product": None, "notes": "Под корень, нагретой водой, без попадания на листья."},
        {"event_type": "pruning", "days": 6, "amount": None, "unit": None, "product": None, "notes": "Удалил нижние листья с пятнами и два пасынка."},
        {"event_type": "fertilizing", "days": 13, "amount": 30.0, "unit": "г", "product": "Сульфат калия", "notes": "Подкормка в период налива плодов."},
    ],
    "Огурец «Мурашка»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "На просвет видны меловатые точки, в узлах жилок тонкая паутина."},
        {"event_type": "watering", "days": 1, "amount": 6.0, "unit": "л", "product": None, "notes": "Ежедневный полив тёплой водой, дорожки пролиты для влажности."},
        {"event_type": "treatment", "days": 2, "amount": 4.0, "unit": "л", "product": "Мыльный раствор", "notes": "Смыл клеща с нижней стороны листьев, повтор через 5 дней."},
        {"event_type": "pruning", "days": 5, "amount": None, "unit": None, "product": None, "notes": "Убрал три сильно повреждённых листа, не компостировал."},
        {"event_type": "fertilizing", "days": 9, "amount": 1.0, "unit": "л", "product": "Настой трав 1:10", "notes": "Раз в неделю чередую с зольным настоем."},
    ],
    "Перец сладкий «Калифорнийское чудо»": [
        {"event_type": "watering", "days": 2, "amount": 5.0, "unit": "л", "product": None, "notes": "Тёплой водой, холодный полив задерживает цветение."},
        {"event_type": "observation", "days": 4, "amount": None, "unit": None, "product": None, "notes": "Плоды наливаются равномерно, вершина не подсыхает."},
        {"event_type": "pruning", "days": 10, "amount": None, "unit": None, "product": None, "notes": "Удалил первые «бумажные» цветки и нижние пасынки."},
        {"event_type": "fertilizing", "days": 14, "amount": 25.0, "unit": "г", "product": "Калийная селитра", "notes": "Раз в две недели по влажному грунту."},
    ],
    "Базилик «Геновезе»": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "Лист плотный, аромат сильный, цветочные стрелки удалены."},
        {"event_type": "watering", "days": 1, "amount": 3.0, "unit": "л", "product": None, "notes": "Утром, по поддону, чтобы не остудить корни."},
        {"event_type": "pruning", "days": 4, "amount": None, "unit": None, "product": None, "notes": "Прищипнул верхушки на пару листьев для ветвления."},
        {"event_type": "transplanting", "days": 28, "amount": None, "unit": None, "product": "Грунт для томатов", "notes": "Перевалка в горшок на 2 л больше."},
    ],
    "Салат «Лолло Росса»": [
        {"event_type": "watering", "days": 1, "amount": 2.0, "unit": "л", "product": None, "notes": "Ежедневно мелкой нормой, грунт всегда слегка влажный."},
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Розетки набирают цвет, горечи нет."},
        {"event_type": "pruning", "days": 6, "amount": None, "unit": None, "product": None, "notes": "Выборочная срезка наружных листьев."},
        {"event_type": "transplanting", "days": 12, "amount": None, "unit": None, "product": "Рассада из кассеты", "notes": "Подсев новой партии каждые 12 дней."},
    ],
    "Петуния ампельная": [
        {"event_type": "watering", "days": 1, "amount": 3.0, "unit": "л", "product": None, "notes": "Каждый день в жару, грунт в кашпо просыхает полностью."},
        {"event_type": "fertilizing", "days": 3, "amount": 5.0, "unit": "мл", "product": "Удобрение для цветущих", "notes": "Раз в неделю половинной дозой."},
        {"event_type": "pruning", "days": 7, "amount": None, "unit": None, "product": None, "notes": "Прищипка после затяжного дождя, удалил повреждённые веточки."},
        {"event_type": "observation", "days": 13, "amount": None, "unit": None, "product": None, "notes": "Перед диагностикой: листья чистые, насекомых нет."},
    ],
    "Пеларгония зональная «Розовый шарм»": [
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Межжилтовый светлый фон на старых листьях, новые — нормальные."},
        {"event_type": "watering", "days": 4, "amount": 1.5, "unit": "л", "product": None, "notes": "Умеренно, грунт просыхает на треть горшка."},
        {"event_type": "fertilizing", "days": 8, "amount": 30.0, "unit": "г", "product": "Сульфат магния", "notes": "Внекорневая подкормка 20 г на 10 л."},
        {"event_type": "pruning", "days": 20, "amount": None, "unit": None, "product": None, "notes": "Короткая обрезка перед переносом в помещение."},
    ],
    "Клубника садовая «Альбион»": [
        {"event_type": "watering", "days": 1, "amount": 2.0, "unit": "л", "product": None, "notes": "Каждые два дня, по краю горшка, не на ягоды."},
        {"event_type": "pruning", "days": 3, "amount": None, "unit": None, "product": None, "notes": "Удалил усы и два листа с сухим краем."},
        {"event_type": "fertilizing", "days": 6, "amount": 3.0, "unit": "г", "product": "Удобрение для земляники", "notes": "Норма по инструкции, по влажному субстрату."},
        {"event_type": "observation", "days": 9, "amount": None, "unit": None, "product": None, "notes": "Ягодки ровные, серой гнили на плодах нет."},
    ],
    "Лаванда узколистная «Hidcote Blue»": [
        {"event_type": "watering", "days": 5, "amount": 1.5, "unit": "л", "product": None, "notes": "Редко, после просыхания грунта; дренаж 5 см проверен."},
        {"event_type": "pruning", "days": 18, "amount": None, "unit": None, "product": None, "notes": "Срез соцветий, лёгкая формирующая стрижка по кроне."},
        {"event_type": "observation", "days": 9, "amount": None, "unit": None, "product": None, "notes": "Окраска листвы не темнеет, застой воды в поддоне не найден."},
    ],
    "Мята перечная": [
        {"event_type": "watering", "days": 1, "amount": 2.5, "unit": "л", "product": None, "notes": "Держу грунт постоянно влажным, пересушку мята не прощает."},
        {"event_type": "pruning", "days": 6, "amount": None, "unit": None, "product": None, "notes": "Срезал верхушки на 10 см для ветвления."},
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Ржавых пятен на листьях нет, соседние горшки не заражены."},
        {"event_type": "transplanting", "days": 33, "amount": None, "unit": None, "product": "Грунт для зелени", "notes": "Разделил куртину, часть вынес в отдельный горшок."},
    ],
    "Фикус Бенджамина": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "За неделю опало около двадцати листьев, снимаю крупные кадры заново."},
        {"event_type": "watering", "days": 2, "amount": 1.5, "unit": "л", "product": None, "notes": "Отстоянная вода комнатной температуры, поддон слил через 20 минут."},
        {"event_type": "treatment", "days": 5, "amount": 0.5, "unit": "л", "product": "Тёплый душ", "notes": "Промыл листья от пыли, грунт закрыл плёнкой."},
        {"event_type": "fertilizing", "days": 12, "amount": 5.0, "unit": "мл", "product": "Удобрение для декоративнолистных", "notes": "Половинная доза зимой."},
    ],
    "Монстера деликатесная": [
        {"event_type": "observation", "days": 1, "amount": None, "unit": None, "product": None, "notes": "На черешках коричневые бляшки 2–4 мм, снимаются ногтем целиком."},
        {"event_type": "treatment", "days": 2, "amount": 0.5, "unit": "л", "product": "Мыльный раствор", "notes": "Снял бляшки губкой по черешкам и стволу."},
        {"event_type": "watering", "days": 3, "amount": 2.0, "unit": "л", "product": None, "notes": "По поддону, опора смочена из пульверизатора."},
        {"event_type": "pruning", "days": 17, "amount": None, "unit": None, "product": None, "notes": "Удалил лист с механическим разрывом, воздушные корни направил в грунт."},
    ],
    "Фаленопсис": [
        {"event_type": "watering", "days": 4, "amount": 0.8, "unit": "л", "product": None, "notes": "Замачивание коры на 15 минут, затем полный сток."},
        {"event_type": "observation", "days": 7, "amount": None, "unit": None, "product": None, "notes": "Корни зелёные после полива, кончики цветоноса не сохнут."},
        {"event_type": "fertilizing", "days": 10, "amount": 2.0, "unit": "мл", "product": "Удобрение для орхидей", "notes": "Раз в месяц, четверть нормы."},
    ],
    "Сансевиерия трёхполосная": [
        {"event_type": "observation", "days": 5, "amount": None, "unit": None, "product": None, "notes": "Листья упругие, мягких оснований не найдено."},
        {"event_type": "watering", "days": 8, "amount": 0.6, "unit": "л", "product": None, "notes": "Редкий полив, поддон сразу осушен."},
        {"event_type": "pruning", "days": 26, "amount": None, "unit": None, "product": None, "notes": "Срезал старый лист у основания."},
    ],
    "Хлорофитум хохматый": [
        {"event_type": "observation", "days": 2, "amount": None, "unit": None, "product": None, "notes": "Кончики коричневые на трёх листьях, отводки в стакане с корнями."},
        {"event_type": "watering", "days": 1, "amount": 1.2, "unit": "л", "product": "Фильтрованная вода", "notes": "Жёсткую воду из-под крана больше не даю."},
        {"event_type": "treatment", "days": 6, "amount": 0.3, "unit": "л", "product": None, "notes": "Промыл листья под тёплым душем."},
        {"event_type": "transplanting", "days": 21, "amount": None, "unit": None, "product": "Грунт для пальм", "notes": "Перевалка, часть отводков отсадил."},
    ],
}

# Напоминания: kind, title, days (через сколько дней), recurrence, channel, notes;
# ключ done означает «выполнено N дней назад».
REMINDER_SPECS: dict[str, list[dict]] = {
    "Яблоня «Боровица»": [
        {"kind": "watering", "title": "Полить 12 л под корень", "days": 1, "recurrence": "weekly", "channel": "web", "notes": "Утром, по приствольному кругу, не по штамбу."},
        {"kind": "inspection", "title": "Осмотреть нижнюю сторону листьев", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Искать оливковые пятна и бархатистый налёт."},
        {"kind": "treatment", "title": "Повторить обработку по результатам диагностики", "days": 4, "recurrence": None, "channel": "web", "notes": "Не ранее 10 дней после предыдущей обработки."},
        {"kind": "other", "title": "Собрать опавшие листья", "done": 5, "recurrence": "weekly", "channel": "both", "notes": "Вывез за пределы сада, в компост не клал."},
        {"kind": "fertilizing", "title": "Осенняя подкормка калием и фосфором", "days": 12, "recurrence": "monthly", "channel": "telegram", "notes": "Не ранее двух недель после последней подкормки."},
    ],
    "Смородина чёрная «Ядрёная»": [
        {"kind": "inspection", "title": "Проверить верхушки на тлю", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Осмотреть пять случайных побегов, считать колонии."},
        {"kind": "treatment", "title": "Повторное смывание колоний", "days": 4, "recurrence": None, "channel": "web", "notes": "Струя воды утром, чтобы листья высохли до ночи."},
        {"kind": "watering", "title": "Полить по канавкам 8 л", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "Под корень, листву не намачивать."},
        {"kind": "pruning", "title": "Проредить середину куста", "done": 6, "recurrence": None, "channel": "web", "notes": "Убрал старые ветви у основания для проветривания."},
    ],
    "Гортензия метельчатая «Limelight»": [
        {"kind": "watering", "title": "Подкислённый полив 15 л", "days": 1, "recurrence": "weekly", "channel": "web", "notes": "10 мл 9 % уксуса на 10 л воды, по мульче из коры."},
        {"kind": "other", "title": "Измерить pH грунта у корней", "days": 3, "recurrence": None, "channel": "both", "notes": "Выше 6,5 — железо не усваивается, раскисление отменить."},
        {"kind": "fertilizing", "title": "Хелат железа по листу", "days": 7, "recurrence": None, "channel": "web", "notes": "Вечером, разово, до результата замера pH."},
        {"kind": "inspection", "title": "Сравнить молодые и старые листья", "done": 4, "recurrence": "weekly", "channel": "web", "notes": "При хлорозе светлеют сначала молодые верхушки."},
    ],
    "Крыжовник «Слабошиповатый-2»": [
        {"kind": "treatment", "title": "Повторная обработка от мучнистой росы", "days": 4, "recurrence": None, "channel": "web", "notes": "Через 7 дней после первого раствора соды."},
        {"kind": "inspection", "title": "Осмотреть молодые верхушки", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Налёт, который не стирается до конца, — признак нового цикла."},
        {"kind": "watering", "title": "Полив под корень 10 л", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "Листву не намачивать."},
        {"kind": "pruning", "title": "Вырезать верхушки прироста с налётом", "done": 5, "recurrence": None, "channel": "web", "notes": "Срез до здоровой древесины, остатки вывез."},
    ],
    "Малина «Таруса»": [
        {"kind": "watering", "title": "Полить канаву 15 л на метр", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Вода из бочки, прогретая за день."},
        {"kind": "pruning", "title": "Вырезать отплодоносившие побеги", "days": 5, "recurrence": None, "channel": "both", "notes": "У основания, не оставляя пеньков."},
        {"kind": "inspection", "title": "Осмотреть нижнюю сторону листьев", "done": 3, "recurrence": "weekly", "channel": "web", "notes": "Следов клеща и паутинки не найдено."},
    ],
    "Хеномелис «Nivalis»": [
        {"kind": "inspection", "title": "Проверить, идёт ли потемнение дальше по побегам", "days": 2, "recurrence": None, "channel": "both", "notes": "Сравнить фотографии поражённых верхушек."},
        {"kind": "pruning", "title": "Досмотреть срезы и замазать раны", "days": 6, "recurrence": None, "channel": "web", "notes": "Ровная здоровая древесина — обрезка завершена."},
        {"kind": "treatment", "title": "Опрыскать крону настоем хвоща", "days": 9, "recurrence": "weekly", "channel": "web", "notes": "В сухое безветренное утро."},
        {"kind": "other", "title": "Собрать опавшие листья под кустом", "done": 4, "recurrence": None, "channel": "telegram", "notes": "Вывез с участка, источник зимующей инфекции."},
    ],
    "Каштан павия": [
        {"kind": "inspection", "title": "Осмотреть нижний ярус кроны", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "Инфекция поднимается снизу вверх."},
        {"kind": "other", "title": "Подновить мульчу щепой 8 см", "days": 8, "recurrence": None, "channel": "both", "notes": "Не прикапывая корневую шейку."},
        {"kind": "watering", "title": "Обильный полив 20 л", "days": 5, "recurrence": "weekly", "channel": "web", "notes": "Редко и глубоко, лучше утром."},
        {"kind": "pruning", "title": "Убрать сухие ветви внутри кроны", "done": 7, "recurrence": None, "channel": "web", "notes": "Для проветривания, секатор дезинфицировать."},
    ],
    "Ирга канадская «Smoky»": [
        {"kind": "other", "title": "Поставить сетку от птиц", "days": 4, "recurrence": None, "channel": "telegram", "notes": "Хотя бы с солнечной стороны кроны."},
        {"kind": "watering", "title": "Полить по периметру 10 л", "days": 6, "recurrence": "weekly", "channel": "web", "notes": "Влага нужна в период налива ягод."},
        {"kind": "inspection", "title": "Осмотреть кора на штамбе", "days": 10, "recurrence": None, "channel": "web", "notes": "Отслоений и тёмных пятен быть не должно."},
    ],
    "Скумпия кожевенная «Royal Purple»": [
        {"kind": "inspection", "title": "Проверить дренаж и застой воды", "days": 7, "recurrence": None, "channel": "web", "notes": "Скумпия гибнет от переувлажнения быстрее, чем от засухи."},
        {"kind": "pruning", "title": "Стрижка для ярких соцветий", "days": 20, "recurrence": None, "channel": "web", "notes": "Коротко по кроне в начале лета."},
        {"kind": "watering", "title": "Полив только в засуху", "days": 12, "recurrence": "weekly", "channel": "telegram", "notes": "6 л под куст, не чаще."},
    ],
    "Пузыреплодник «Dart's Gold»": [
        {"kind": "fertilizing", "title": "Подкормка после стрижки", "days": 4, "recurrence": "monthly", "channel": "web", "notes": "40 г полного удобрения по влажной земле."},
        {"kind": "inspection", "title": "Замерить pH у изгороди", "days": 6, "recurrence": None, "channel": "both", "notes": "При pH выше 7 молодой прирост светлеет."},
        {"kind": "pruning", "title": "Вторая формирующая стрижка", "days": 18, "recurrence": None, "channel": "web", "notes": "По натянутому шнуру, в сухую погоду."},
    ],
    "Картофель «Невский»": [
        {"kind": "inspection", "title": "Осмотреть низ листьев по утрам", "days": 1, "recurrence": "daily", "channel": "both", "notes": "Беловатый венчик мицелия — признак активного спорулирования."},
        {"kind": "other", "title": "Окучить выше по влажному грунту", "days": 2, "recurrence": None, "channel": "web", "notes": "Клубни под слоем почвы почти не заражаются."},
        {"kind": "treatment", "title": "Удалить ботву с пятнами перед уборкой", "days": 5, "recurrence": None, "channel": "web", "notes": "Срезать и вывезти, не оставлять на гряде."},
        {"kind": "inspection", "title": "Перебрать урожай в сухую погоду", "done": 3, "recurrence": None, "channel": "both", "notes": "Больные клубни отложены отдельно."},
    ],
    "Морковь «Нантская-4»": [
        {"kind": "watering", "title": "Полив малыми нормами", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Резкая влага после сушки ведёт к растрескиванию."},
        {"kind": "inspection", "title": "Проверить ботву на объедённые края", "days": 4, "recurrence": None, "channel": "web", "notes": "Слизни появляются по сырым вечерам."},
        {"kind": "fertilizing", "title": "Калийная подкормка", "done": 8, "recurrence": "monthly", "channel": "telegram", "notes": "30 г сульфата калия на погонный метр."},
    ],
    "Кабачок «Грибовский»": [
        {"kind": "treatment", "title": "Повторная обработка по налёту", "days": 4, "recurrence": None, "channel": "web", "notes": "Через 7 дней после первого раствора соды."},
        {"kind": "watering", "title": "Полив 8 л тёплой водой под корень", "days": 1, "recurrence": "weekly", "channel": "web", "notes": "Листву не намачивать."},
        {"kind": "inspection", "title": "Осмотреть молодые листья", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Собрать урожай вовремя: переросшие плети болеют сильнее."},
        {"kind": "pruning", "title": "Удалить переросшие завязи", "done": 3, "recurrence": None, "channel": "web", "notes": "Стимулирует новую завязь."},
    ],
    "Чеснок озимый «Любаша»": [
        {"kind": "pruning", "title": "Выломить оставшиеся стрелки", "days": 3, "recurrence": None, "channel": "web", "notes": "Иначе питание уходит в стрелку, а не в головку."},
        {"kind": "other", "title": "Прекратить поливы за три недели до уборки", "days": 10, "recurrence": None, "channel": "telegram", "notes": "Сухой грунт лучше хранит головки."},
        {"kind": "inspection", "title": "Проверить кончики перьев", "days": 5, "recurrence": None, "channel": "web", "notes": "Сухие кончики — признак нехватки калия или переувлажнения."},
    ],
    "Фасоль вьющаяся «Победитель»": [
        {"kind": "watering", "title": "Полив в пору налива бобов", "days": 1, "recurrence": "daily", "channel": "web", "notes": "9 л на метр ряда, без пересушки."},
        {"kind": "other", "title": "Собрать бобы до огрубения", "days": 3, "recurrence": None, "channel": "both", "notes": "Куст продолжает завязывать, пока убирают переросшие."},
        {"kind": "inspection", "title": "Осмотреть нижние бобы на слизней", "days": 4, "recurrence": None, "channel": "web", "notes": "Проверка после дождливых вечеров."},
    ],
    "Томат «Санька»": [
        {"kind": "inspection", "title": "Осмотреть нижние листья", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Бурые точки снизу — сигнал к удалению листа."},
        {"kind": "watering", "title": "Редкий обильный полив 6 л", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "Под колышек, не по стеблю."},
        {"kind": "fertilizing", "title": "Зольный пролив", "done": 5, "recurrence": "monthly", "channel": "telegram", "notes": "Калий поддерживает наливы плодов."},
    ],
    "Томат «Чёрный принц»": [
        {"kind": "treatment", "title": "Повторная обработка от мучнистой росы", "days": 4, "recurrence": None, "channel": "web", "notes": "Через 7 дней после первой обработки."},
        {"kind": "inspection", "title": "Осмотреть верхние листья на налёт", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Налёт, стирающийся частично, — мучнистая роса."},
        {"kind": "watering", "title": "Полить под корень 8 л", "days": 1, "recurrence": "weekly", "channel": "web", "notes": "Тёплой водой, без попадания на листву."},
        {"kind": "other", "title": "Проветрить теплицу на весь день", "done": 2, "recurrence": "daily", "channel": "web", "notes": "Ночная температура выше 16 °C, конденсат убран."},
    ],
    "Огурец «Мурашка»": [
        {"kind": "watering", "title": "Полить под корень 6 л", "days": 1, "recurrence": "daily", "channel": "web", "notes": "Тёплой водой, дорожки пролить для влажности."},
        {"kind": "treatment", "title": "Повторное смывание клеща", "days": 3, "recurrence": None, "channel": "web", "notes": "Через 5 дней после первой обработки."},
        {"kind": "inspection", "title": "Посмотреть узлы жилок на просвет", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Тонкая паутина и движущиеся точки."},
        {"kind": "fertilizing", "title": "Подкормка настоем трав", "done": 4, "recurrence": "weekly", "channel": "telegram", "notes": "Чередовать с зольным настоем."},
    ],
    "Перец сладкий «Калифорнийское чудо»": [
        {"kind": "watering", "title": "Полить тёплой водой 5 л", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Холодная вода задерживает цветение."},
        {"kind": "other", "title": "Проверить ночную температуру в теплице", "days": 3, "recurrence": "daily", "channel": "telegram", "notes": "Ниже 15 °C пыльца не формируется."},
        {"kind": "fertilizing", "title": "Калийная подкормка", "days": 8, "recurrence": "monthly", "channel": "web", "notes": "25 г калийной селитры по влажному грунту."},
    ],
    "Базилик «Геновезе»": [
        {"kind": "watering", "title": "Полив по поддону", "days": 1, "recurrence": "daily", "channel": "web", "notes": "Тёплой водой, холод остужает корни."},
        {"kind": "pruning", "title": "Прищипка верхушек", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "На пару листьев, иначе уходит в цвет."},
        {"kind": "inspection", "title": "Осмотреть листья перед срезкой", "done": 2, "recurrence": "weekly", "channel": "both", "notes": "Пятен и насекомых нет."},
    ],
    "Салат «Лолло Росса»": [
        {"kind": "watering", "title": "Ежедневный полив мелкой нормой", "days": 1, "recurrence": "daily", "channel": "web", "notes": "Грунт всегда слегка влажный, пересушка даёт горечь."},
        {"kind": "other", "title": "Подсеять новую партию", "days": 6, "recurrence": "monthly", "channel": "telegram", "notes": "Конвейер с шагом 12 дней."},
        {"kind": "pruning", "title": "Срез наружных листьев", "done": 1, "recurrence": "weekly", "channel": "web", "notes": "Выборочно, розетку не трогать."},
    ],
    "Петуния ампельная": [
        {"kind": "watering", "title": "Полив кашпо 3 л", "days": 1, "recurrence": "daily", "channel": "web", "notes": "В жару грунт просыхает полностью за сутки."},
        {"kind": "fertilizing", "title": "Подкормка для цветущих", "days": 4, "recurrence": "weekly", "channel": "web", "notes": "Половинная доза, по влажному субстрату."},
        {"kind": "pruning", "title": "Прищипка после дождя", "done": 6, "recurrence": None, "channel": "both", "notes": "Удалить повреждённые веточки для нового прироста."},
    ],
    "Пеларгония зональная «Розовый шарм»": [
        {"kind": "watering", "title": "Умеренный полив 1,5 л", "days": 4, "recurrence": "weekly", "channel": "web", "notes": "Грунт должен просохнуть на треть горшка."},
        {"kind": "inspection", "title": "Сравнить старые и новые листья", "days": 3, "recurrence": "weekly", "channel": "both", "notes": "При нехватке магния светлеют старые листья."},
        {"kind": "fertilizing", "title": "Внекорневая подкормка сульфатом магния", "done": 5, "recurrence": "monthly", "channel": "web", "notes": "20 г на 10 л, по старым листьям."},
    ],
    "Клубника садовая «Альбион»": [
        {"kind": "watering", "title": "Полить каждые два дня", "days": 1, "recurrence": "daily", "channel": "web", "notes": "2 л по краю горшка, не на ягоды."},
        {"kind": "pruning", "title": "Удалить усы", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "Иначе куст уходит в зелень вместо урожая."},
        {"kind": "inspection", "title": "Проверить ягоды на серую гниль", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Контакт с мокрым субстратом — главный риск."},
    ],
    "Лаванда узколистная «Hidcote Blue»": [
        {"kind": "watering", "title": "Полив только после просыхания грунта", "days": 5, "recurrence": "weekly", "channel": "web", "notes": "1,5 л, поддон обязательно осушить."},
        {"kind": "other", "title": "Проверить дренаж в горшке", "days": 9, "recurrence": None, "channel": "web", "notes": "Слой 5 см, застой воды убивает лаванду."},
        {"kind": "pruning", "title": "Срез соцветий", "done": 7, "recurrence": None, "channel": "telegram", "notes": "По кроне, не затрагивя одревесневшую часть."},
    ],
    "Мята перечная": [
        {"kind": "watering", "title": "Поддержать грунт влажным", "days": 1, "recurrence": "daily", "channel": "web", "notes": "Пересушка сразу сказывается на листьях."},
        {"kind": "pruning", "title": "Стрижка для ветвления", "days": 6, "recurrence": "weekly", "channel": "web", "notes": "Срез верхушек на 10 см."},
        {"kind": "other", "title": "Осмотреть соседние горшки", "done": 3, "recurrence": "monthly", "channel": "both", "notes": "Мята быстро осваивает соседние ёмкости."},
    ],
    "Фикус Бенджамина": [
        {"kind": "observation", "title": "Переснять листья крупным планом", "days": 1, "recurrence": None, "channel": "web", "notes": "С 20–25 см, нижняя сторона отдельно."},
        {"kind": "watering", "title": "Полив отстоянной водой 1,5 л", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Поддон слить через 20 минут."},
        {"kind": "other", "title": "Поставить горшок ближе к влажности", "days": 4, "recurrence": None, "channel": "telegram", "notes": "Поддон с керамзитом и водой, влажность 45–50 %."},
        {"kind": "fertilizing", "title": "Подкормка половинной дозой", "done": 6, "recurrence": "monthly", "channel": "web", "notes": "Зимой удобрение не нужно при слабом свете."},
    ],
    "Монстера деликатесная": [
        {"kind": "treatment", "title": "Повторная обработка от щитовки", "days": 5, "recurrence": None, "channel": "web", "notes": "Личинки выходят позже взрослых, пропуск цикла обнуляет результат."},
        {"kind": "inspection", "title": "Осмотреть соседние горшки", "days": 2, "recurrence": "weekly", "channel": "both", "notes": "Бляшки искать на черешках и нижней стороне."},
        {"kind": "other", "title": "Поднять влажность у радиатора", "days": 3, "recurrence": None, "channel": "telegram", "notes": "30–35 % сейчас — слишком сухо."},
    ],
    "Фаленопсис": [
        {"kind": "watering", "title": "Замачивание коры на 15 минут", "days": 3, "recurrence": "weekly", "channel": "web", "notes": "После полива дать стечь полностью."},
        {"kind": "fertilizing", "title": "Подкормка орхидей", "days": 10, "recurrence": "monthly", "channel": "web", "notes": "Четверть нормы по влажной коре."},
        {"kind": "inspection", "title": "Проверить корни на зелень", "done": 4, "recurrence": "weekly", "channel": "both", "notes": "Зелёные после полива — всё в порядке, серые и мягкие — риск."},
    ],
    "Сансевиерия трёхполосная": [
        {"kind": "watering", "title": "Редкий полив 0,6 л", "days": 6, "recurrence": "monthly", "channel": "web", "notes": "Зимой между поливами грунт просыхает на всю глубину."},
        {"kind": "inspection", "title": "Проверить основание листьев", "days": 8, "recurrence": None, "channel": "web", "notes": "Мягкие основания — перелив, поливы прекратить."},
    ],
    "Хлорофитум хохматый": [
        {"kind": "watering", "title": "Полив фильтрованной водой", "days": 1, "recurrence": "weekly", "channel": "web", "notes": "Жёсткая вода ведёт к сухим кончикам."},
        {"kind": "other", "title": "Отсадить укоренённые отводки", "days": 5, "recurrence": None, "channel": "both", "notes": "Корни уже 3–4 см, пора в отдельные стаканы."},
        {"kind": "treatment", "title": "Тёплый душ от пыли", "done": 3, "recurrence": "monthly", "channel": "web", "notes": "Грунт закрыть плёнкой."},
    ],
}

DEFAULT_CARE: list[dict] = [
    {"event_type": "watering", "days": 2, "amount": 6.0, "unit": "л", "product": None, "notes": "Полив по пересохшему грунту."},
    {"event_type": "observation", "days": 4, "amount": None, "unit": None, "product": None, "notes": "Новых пятен и насекомых нет."},
    {"event_type": "fertilizing", "days": 12, "amount": 40.0, "unit": "г", "product": None, "notes": "Подкормка по влажному грунту."},
    {"event_type": "pruning", "days": 20, "amount": None, "unit": None, "product": None, "notes": "Удалены повреждённые листья."},
    {"event_type": "treatment", "days": 9, "amount": 3.0, "unit": "л", "product": None, "notes": "Профилактическая обработка в сухое утро."},
]

DEFAULT_REMINDERS: list[dict] = [
    {"kind": "watering", "title": "Полить под корень", "days": 2, "recurrence": "weekly", "channel": "web", "notes": "Обычная норма для этой культуры."},
    {"kind": "inspection", "title": "Осмотреть нижнюю сторону листьев", "days": 3, "recurrence": "weekly", "channel": "both", "notes": "Искать налёт, пятна и насекомых."},
    {"kind": "fertilizing", "title": "Плановая подкормка", "days": 10, "recurrence": "monthly", "channel": "telegram", "notes": "Только по влажному грунту."},
    {"kind": "other", "title": "Уборка растительных остатков", "done": 6, "recurrence": None, "channel": "web", "notes": "Остатки вывезены за пределы участка."},
]


def _care_for(plant_name: str) -> list[dict]:
    return CARE_SPECS.get(plant_name) or DEFAULT_CARE


def _reminders_for(plant_name: str) -> list[dict]:
    return REMINDER_SPECS.get(plant_name) or DEFAULT_REMINDERS


# Диагнозы демо-аккаунта: по одной истории на растение, с разными темами,
# вопросами, ответами и частью повреждённого органа.
DEMO_DIAGNOSES: list[dict] = [
    {
        "plant": "Яблоня «Боровица»",
        "days_ago": 24,
        "damaged_part": "leaf",
        "symptoms": (
            "Нижние листья покрываются оливково-бурыми пятнами с чётким краем, "
            "к середине лета часть листьев преждевременно опадает."
        ),
        "result": SCAB_RESULT,
        "revisions": ({"result": SCAB_RESULT, "answers": [
            "Пятна есть и на плодах: на двух яблоках буроватые пробковые участки.",
            "Первые пятна появились через 10 дней после трёх дождливых суток.",
            "Последняя подкормка — монофосфат калия в начале августа.",
        ]},),
        "answers": (1, 2, 3),
        "feedback": (4, True, "Ответы на вопросы помогли заметить пятна на плодах."),
    },
    {
        "plant": "Смородина чёрная «Ядрёная»",
        "days_ago": 3,
        "damaged_part": "leaf",
        "symptoms": (
            "Верхушки побегов скручены книзу, на них глянцевитые зелёные личинки плотными "
            "колониями. Листья на ощупь липкие, по штамбу ходят муравьи."
        ),
        "result": APHID_RESULT,
        "answers": (1, 2, 3),
        "answer_texts": {
            1: "Ходы есть у основания, муравьи поднимаются по штамбу снизу вверх.",
            2: "Скручены в основном верхушки, в середине ветви листья почти ровные.",
            3: "После цветения дал настой крапивы, возможно, с азотом переборщил.",
        },
        "feedback": (5, True, "Про выщипывание верхушек и сирфид было полезнее всего."),
    },
    {
        "plant": "Крыжовник «Слабошиповатый-2»",
        "days_ago": 5,
        "damaged_part": "fruit",
        "symptoms": (
            "Белый паутинистый налёт перешёл с листьев на ягоды, ягоды остановились в росте "
            "и покрываются ржавой сеткой. Весной обрезал сильно, сразу пошёл бурный прирост."
        ),
        "result": GOOSEBERRY_POWDERY_RESULT,
        "answers": (2,),
        "answer_texts": {2: "Обрезка была в апреле, удалил четыре старые ветви из шести."},
        "feedback": (4, True, "Не знал, что ранняя сильная обрезка провоцирует росу."),
    },
    {
        "plant": "Гортензия метельчатая «Limelight»",
        "days_ago": 8,
        "damaged_part": "leaf",
        "symptoms": (
            "Молодые листья на верхушках светлеют между жилками, сами жилки остаются "
            "тёмно-зелёными. Осенью под куст внесли известь для снижения кислотности."
        ),
        "result": IRON_CHLOROSIS_RESULT,
        "answers": (1, 2),
        "answer_texts": {
            1: "Извести примерно пригоршня на куст, золы в этом сезоне не давал.",
            2: "Хлороз появился после двух недель жары и обильных поливов.",
        },
    },
    {
        "plant": "Хеномелис «Nivalis»",
        "days_ago": 11,
        "damaged_part": "stem",
        "symptoms": (
            "Верхушки побегов побурели и засохли, будто обожжены, поражение идёт от конца "
            "побега к основанию. Кора на штамбе цела, после тёплой влажной недели."
        ),
        "result": MONILIA_RESULT,
        "revisions": ({"result": MONILIA_RESULT, "answers": [
            "Потемнение началось через неделю после заморозка минус четыре, снега уже не было.",
            "На соседней ирге таких верхушек нет, куст стоит в трёх метрах.",
        ]},),
        "answers": (1, 2),
    },
    {
        "plant": "Каштан павия",
        "days_ago": 9,
        "damaged_part": "leaf",
        "symptoms": (
            "Крупные угловатые бурые пятна между жилками, сильнее всего на нижнем ярусе кроны; "
            "у части листьев сухая коричневая кайма по краю. Листву под деревом не убирал."
        ),
        "result": CHESTNUT_BLOTCH_RESULT,
        "answers": (2,),
        "answer_texts": {2: "Не убираю, опавшие листья лежат под мульчёй с прошлого года."},
    },
    {
        "plant": "Картофель «Невский»",
        "days_ago": 2,
        "damaged_part": "leaf",
        "symptoms": (
            "На ботве расплывчатые бурые пятна с бледным краем, после холодных рос листья "
            "мокрят. Неделя была дождливой, окучивание делал в последний раз три недели назад."
        ),
        "result": BLIGHT_RESULT,
        "answers": (1, 2),
        "answer_texts": {
            1: "По утрам на низу листа есть беловатый венчик, к полудню подсыхает.",
            2: "Три недели, гряда низкая, мульчи почти нет.",
        },
        "feedback": (5, True, "Совет про окучивание перед уборкой сразу применил."),
    },
    {
        "plant": "Томат «Чёрный принц»",
        "days_ago": 6,
        "damaged_part": "leaf",
        "symptoms": (
            "На верхних листьях появился белый мучнистый налёт, к вечеру часть листьев "
            "скручивается. Теплица закрывается на ночь."
        ),
        "result": POWDERY_RESULT,
        "feedback": (5, True, "Совпало с прогнозом: проветривание исправило ситуацию."),
    },
    {
        "plant": "Огурец «Мурашка»",
        "days_ago": 1,
        "damaged_part": "leaf",
        "symptoms": (
            "По всей пластинке меловатые точки-проколы, в узлах жилок и на нижней стороне "
            "тонкая паутина. К полудню листья теряют тургор, теплица почти не проветривалась."
        ),
        "result": SPIDER_MITE_RESULT,
        "answers": (1, 3),
        "answer_texts": {
            1: "На целый день не открывал две недели, боялся сквозняка.",
            3: "Поливаю ежедневно тёплой водой, но дорожки не проливаю.",
        },
    },
    {
        "plant": "Монстера деликатесная",
        "days_ago": 4,
        "damaged_part": "stem",
        "symptoms": (
            "На черешках и стволе плоские коричневые бляшки 2–4 мм, которые снимаются ногтем "
            "целиком. Ниже колонии листья липкие, на подоконнике под горшком тёмный налёт."
        ),
        "result": SCALE_RESULT,
        "answers": (2,),
        "answer_texts": {2: "Липкие пятна есть: протираю подоконник, но налёт проступает снова."},
        "feedback": (3, False, "Про срок повторной обработки хотелось бы точнее."),
    },
    {
        "plant": "Фикус Бенджамина",
        "days_ago": 2,
        "damaged_part": "whole_plant",
        "symptoms": (
            "За неделю опало около двадцати листьев, но снять подробные кадры не удалось — "
            "фотографии получились смазанными."
        ),
        "result": BLURRY_RESULT,
    },
    {
        "plant": "Петуния ампельная",
        "days_ago": 13,
        "damaged_part": "flower",
        "symptoms": (
            "Проверяю кашпо перед выносом на балкон: листья зелёные, без пятен и насекомых, "
            "цветение ровное."
        ),
        "result": HEALTHY_RESULT,
        "feedback": (5, True, None),
    },
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
    {"kind": "weather_warning", "title": "Локальные заморозки до −3 °C", "hours": 7,
     "body": "Огород у забора: укройте кабачок и фасоль спанбондом на ночь, полив перенесите на утро."},
    {"kind": "reminder", "title": "Скоро задача по уходу", "hours": 15,
     "body": "Смородина чёрная «Ядрёная»: проверить верхушки пяти случайных побегов на тлю."},
    {"kind": "diagnosis_completed", "title": "Диагностика обновлена", "hours": 30,
     "body": "Хеномелис «Nivalis»: версия 2 анализа подтвердила монилиальный ожог после заморозка."},
    {"kind": "seasonal_task", "title": "Собрать опавшую листву под деревьями", "hours": 44,
     "body": "Лесосад у пруда: вывезите листья под каштаном и хеномелисом — на них зимует инфекция."},
    {"kind": "reminder", "title": "Задача по уходу выполнена", "hours": 70,
     "body": "Монстера деликатесная: бляшки щитовки сняты губкой по черешкам и стволу."},
    {"kind": "weather_warning", "title": "Порывы ветра до 70 км/ч", "hours": 120,
     "body": "Теплица из поликарбоната: проверьте крепление дверей и форточек, на ночь оставьте щель 5 см."},
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
    entries = _care_for(plant.name)
    for entry in entries[: min(len(entries), 3 + (plant.id % 4))]:
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
    entries = _reminders_for(plant.name)
    pending = [entry for entry in entries if "done" not in entry]
    finished = [entry for entry in entries if "done" in entry]
    for entry in pending[: 2 + (plant.id % 3)]:
        db.add(Reminder(
            plant_id=plant.id,
            kind=entry["kind"],
            title=entry["title"],
            due_at=now + timedelta(days=entry["days"]),
            notes=entry["notes"],
            recurrence=entry["recurrence"],
            timezone="Europe/Riga",
            preferred_channel=entry["channel"],
        ))
    for entry in finished:
        due = now - timedelta(days=entry["done"])
        db.add(Reminder(
            plant_id=plant.id,
            kind=entry["kind"],
            title=entry["title"],
            due_at=due,
            notes=entry["notes"],
            recurrence=entry["recurrence"],
            timezone="Europe/Riga",
            preferred_channel=entry["channel"],
            completed_at=due + timedelta(hours=5),
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
    answer_texts: dict[int, str] | None = None,
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
            text = (answer_texts or {}).get(position)
            if text is None:
                text = (
                    revisions[-1]["answers"][position - 1]
                    if revisions
                    else "Уточнено при повторном осмотре."
                )
            db.add(DiagnosisAnswer(
                question_id=question.id,
                answer=text,
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

    for spec in DEMO_DIAGNOSES:
        plant = plants.get(spec["plant"])
        if not plant:
            continue
        _seed_diagnosis(
            db, plant, user, now=now,
            days_ago=spec["days_ago"],
            damaged_part=spec["damaged_part"],
            symptoms=spec["symptoms"],
            result=spec["result"],
            revisions=spec.get("revisions", ()),
            answers=spec.get("answers", ()),
            answer_texts=spec.get("answer_texts"),
            feedback=spec.get("feedback"),
        )

    _seed_notifications(db, user, now)
