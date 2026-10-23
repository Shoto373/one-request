# Трек A — каталог и расчёт плана

Маленький согласованный каталог (3 города отправления × 8 направлений) и детерминированные
инструменты поверх него: поиск предложений (`Offer`), сборка комплекта, проверки состыковок.

## Что лежит в репозитории

| Путь | Что это |
|---|---|
| `app/catalog/models.py` | Pydantic-модели каталога и `Offer` (контракт п.4 архитектуры) |
| `app/catalog/store.py` | `Catalog.load()` / `get_catalog()` — каталог в памяти с индексами |
| `app/tools/search.py` | `search_transport`, `search_hotels`, `search_transfers` → `list[Offer]` |
| `app/planning/models.py` | `Bundle`, `Check`, `CheckOption`, `Claim` (п.6) — TripState импортирует их отсюда |
| `app/planning/validate.py` | `validate_plan` v0 (п.7) |
| `app/planning/build.py` | `build_bundles`: до трёх проверенных комплектов — дешевле, удобнее, гибче отмена |
| `app/planning/scoring.py` | штрафы удобства и гибкости, фразы компромиссов |
| `app/planning/replan.py` | `replan`: перепроверка всех позиций, замена только непрошедших |
| `app/planning/destinations.py` | `suggest_destinations`: только выполнимые направления, скоринг с разложением |
| `app/planning/alternatives.py` | минимальные изменения для невыполнимого запроса |
| `app/planning/suggest.py` | `suggest_destinations(requirements, state)` — контракт с оркестратором B |
| `data/seed/catalog/*.json` | каталог (JSON-массив, один объект на строку) + `meta.json`; в подпапке, чтобы `app.db.load_seed` (трек B) его не трогал |
| `data/examples/*.json` | реальные ответы инструментов для UI и моков (в БД не сидятся) |
| `pipeline/` | офлайн-сбор (`fetch`) и генерация (`build`, `examples`) |
| `pipeline/snapshots/` | компактные выгрузки реальных данных; генерация читает только их |
| `tests/test_catalog.py` | проверки сида, контракта Offer и сценариев |

## Как пересобрать

```bash
pip install -r pipeline/requirements.txt
python -m pipeline.build        # snapshots -> data/seed/catalog (офлайн, детерминированно, ~2 с)
python -m pipeline.examples     # data/examples
python -m pytest tests
python -m pipeline.fetch        # только если нужно обновить реальные данные (сеть)
```

`random seed = 42`, у каждой сущности свой поток случайных чисел: добавление отеля не меняет рейсы.
Два запуска `pipeline.build` дают побайтово одинаковые файлы. При `docker compose up` ничего не скачивается.

Overpass по HTTPS из некоторых сетей сбрасывается: `pipeline/sources.py` ходит на него по HTTP
и с ретраями (сервер часто перегружен, сбор OSM занимает 10–20 минут).

## Каталог (`data/seed/catalog`)

| Файл | Объект | Кол-во | Источник |
|---|---|---|---|
| `cities.json` | `City` | 11 | Wikidata (координаты, население, описание) + справочник виз для граждан РФ |
| `hubs.json` | `Hub` | 21 | OurAirports (14 аэропортов) + Wikidata (7 вокзалов) |
| `climate.json` | `CityClimate` | 8 | Open-Meteo: воздух 2021–2025, море (Marine) 2023–2025 |
| `hotels.json` | `Hotel` | 56 | **гибрид**: название и координаты — OSM, всё остальное сгенерировано |
| `flights.json` | `Flight` | ~11.6k | **синтетика** по реальной геометрии аэропортов |
| `trains.json` | `Train` | ~1.5k | **синтетика** на реальных вокзалах |
| `transfers.json` | `TransferRoute` | 283 | **синтетика** по реальному расстоянию хаб ↔ отель |
| `meta.json` | `SeedMeta` | 1 | окно дат, счётчики, реестр крайних случаев, источники |

Города отправления: `MOW`, `LED`, `SVX`. Направления: `AER` Сочи, `KGD` Калининград, `KZN` Казань,
`IST` Стамбул, `AYT` Анталья, `SSH` Шарм-эль-Шейх, `DXB` Дубай, `EVN` Ереван. id города = код IATA города.

Транспорт сгенерирован на окно **2026-10-26 … 2027-01-15** (`meta.window`, конец не включён).
Время полёта = расстояние / 750 км/ч + 40 мин. Поезда: Москва↔Казань, Москва↔Сочи/Адлер,
СПб↔Москва, Екатеринбург↔Казань.

Синтетику обязательно маркируем на карточках («сгенерированные условия»): у каждого объекта и
`Offer` есть `synthetic: true` и `source_refs` (`osm:node/…`, `gen:hotels@seed42`, `flight:<id>`).

### Лицензии и атрибуция (нужно показать в UI/README)

- OpenStreetMap — ODbL: «© участники OpenStreetMap».
- Open-Meteo — CC BY 4.0: «Weather data by Open-Meteo.com».
- Wikidata — CC0, OurAirports — public domain.

## Контракт `Offer` (п.4)

- `total_rub == unit_price_rub × units × quantity + Σ variant.extras[].price_rub` — проверяется валидатором.
- Отель: `price_unit=per_room_night`, `units` = ночи, `quantity` = номера, `unit_price_rub` = средняя цена ночи
  (по ночам — `variant.nightly_rub`). Цена номера покрывает до 2 гостей; гости сверх двух — в `extras`
  (дети младше `child_free_age` бесплатно), животное — в `extras`.
- Рейс / поезд: `per_person`, `units` = платные места (младенцы до 2 лет и дети до 5 лет в поезде без места бесплатно).
- Трансфер: такси/минивэн `per_vehicle` (`quantity` = машины), шаттл/автобус `per_person`.
- `cancel_policy.free_until = null` — бесплатной отмены нет; `penalty_rub` — сколько теряется при отмене.
  Для тарифов с возвратом (рейс «Гибкий», поезд) `penalty_rub` = сбор за возврат.
- `times` — ISO-время с часовым поясом. Дата заезда отеля — локальная дата города.
- `capacity` — на какой состав рассчитано предложение; вместимость номеров/машин проверена при поиске.
- `title` — подпись карточки, `baggage` — строка («10 кг ручной клади + 1×23 кг»).
- `variant` — описательные поля по виду (`hotel_name`, `room_type`, `tariff`, `fare`, `car_class`, `mode`,
  `from_hub`/`to_hub`, `direction`, `baggage`, `extras` …).

## Интеграция с треками B и C

Точки входа, которые уже вызывает код из `main`:

| Кто вызывает | Функция | Что возвращает |
|---|---|---|
| оркестратор B (`app/agent/orchestrator.py`) | `app.planning.build.build_bundles(requirements: dict, state)` | `list[dict]` — комплекты в формате TripState (`[]`, если нечего предложить) |
| корзина C (`app/cart/service.py`) | `app.planning.validate.validate_bundle(bundle: dict, state: dict)` | `list[dict]` — проверки; любой `block` = платить нельзя |

- `requirements` — то, что достал LLM: `origin`/`destination` текстом («Москва», «из Казани», «SVO»),
  `dates` как `{"start","end"}` или ISO-строки, `adults`, `children`/`children_ages` (возраст неизвестен → 7 лет),
  `pets`, `budget_total_rub`, `transport` («поездом» → только поезда). Жёсткость бюджета берётся из `kind` требования в `state`.
- Город не из каталога или нераспознанные даты → `[]`. Причины для честного ответа:
  `app.planning.build.explain_no_bundles(requirements, state) -> list[str]`
  (в т.ч. «Самый дешёвый вариант — N ₽, это на M ₽ больше бюджета»).
- `validate_bundle` повторяет `validate_plan` и заново оценивает каждое предложение по каталогу:
  пропало или изменилась цена → `availability: block`.
- На проводе `Check.options` — строки (как в `app/state/models.py` и `web/src/api/types.ts`);
  внутри трека A это `CheckOption` с ценой и гарантией. Дополнительно в проверке есть `offer_ids` и `refs`.
- У `Offer` есть готовые к показу `title` и `baggage` (строка), структурный багаж — в `variant.baggage`.

Каталог лежит в `data/seed/catalog/`, а не в `data/seed/`: `app.db.load_seed` (трек B) читает только
верхний уровень `data/seed/*.json` в таблицу `catalog_entries` и требует у каждой записи `id` и `kind`.
Поиск работает по каталогу в памяти, БД ему не нужна.

## API трека A

```python
from datetime import date
from app.catalog.models import Party, Pet
from app.catalog.store import get_catalog
from app.planning.build import build_trip_bundles
from app.planning.query import TripQuery
from app.tools.search import search_hotels, search_transfers, search_transport

cat = get_catalog()                       # CATALOG_DIR или <repo>/data/seed/catalog
party = Party(adults=2, children_ages=[5], pets=[Pet(kind="dog", weight_kg=7)])
search_transport(cat, "MOW", "AYT", date(2026, 11, 12), party)            # рейсы + поезда, дешевле первыми
search_hotels(cat, "AYT", date(2026, 11, 12), date(2026, 11, 19), party, cheapest_per_hotel=True)
search_transfers(cat, "AYT", "h_AYT_06", party, at=arrival_dt, direction="to_hotel")
res = build_trip_bundles(cat, TripQuery("MOW", "AER", date(2026, 11, 10), date(2026, 11, 17), Party(adults=2)))
res.bundles      # [Bundle] — 0 или 1 комплект в v0
res.notes        # почему вариантов нет (для честного ответа)
res.cheapest_total_rub  # минимум без учёта бюджета («+N ₽ к бюджету»)
```

`TripQuery.kinds=("train",)` — только поезда («поездом в Казань»).

## Направления и невыполнимые запросы (спринт 2)

| Функция | Для чего |
|---|---|
| `app.planning.destinations.suggest_destinations(requirements, state)` | `list[dict]` для `TripState.destinations`: топ-3 **выполнимых** направления |
| `app.planning.destinations.suggest_destinations_report(requirements, state)` | `{options, rejected, assumptions, changes, problems}` — всё сразу, для честного ответа |
| `app.planning.alternatives.trip_alternatives(requirements, state)` | два минимальных изменения для конкретной поездки без подходящего комплекта |
| `app.tools.climate.get_climate(cat, city_id, month)` | климат месяца из seed (Open-Meteo); ссылка — `climate:AYT:11.sea_temp` |

**`suggest_destinations`.** Для каждого из 8 направлений реально запускается `build_trip_bundles` на 4 стартовых
датах месяца (10, 17, 3, 24), берётся самый дешёвый выполнимый комплект. Направления без комплекта уходят в
`rejected` с причиной. Скоринг 0…1 по факторам `weather`, `price`, `travel`, `visa`, `match`. Веса смещаются
от пожеланий: «недорого» или бюджет — цена важнее, «без долгих перелётов» — дорога, «море/тепло» — погода.
Направления с тёплым пляжным морем (≥ 21°C) при запросе «море» всегда выше остальных.

Опция направления: `id`, `name`, `country`, `feasible`, `est_total_rub`, `dates`, `nights`, `travel`
(`kind`, `duration_min`, `stops`, `title`), `climate`, `visa`, `score`, `score_breakdown`
(`factor`, `weight`, `value`, `contribution`, `text`, `refs`), `why` и `risks` (Claim со ссылками),
`bundle` (готовый комплект, `id = b_<город>`), `rank`, `assumptions`.

Что понимается из требований: `origin`; `month` или `dates` («ноябрь», «2026-11», `{start, end}`); `nights`
(«неделя», «10 дней», «выходные»; по умолчанию 7 — это видно в `assumptions`); состав (без `adults` — 2 взрослых,
предположение); `budget` (число или «недорого»); `transport`; «без долгих перелётов» → до 5 ч в одну сторону;
«без пересадок» → `max_stops=0`; ключевые слова о море, тепле, культуре, еде, горах, дайвинге, «всё включено».
Жёсткое «море» (`kind: hard`) отбрасывает направления без тёплого пляжного моря.

**Мягкие и жёсткие ограничения дороги.** Мягкие «без пересадок» и «до N часов» сначала применяются. Если с
ними ничего нет, комплект собирается без них, а в `risks` пишется «Не выполнено пожелание…». Жёсткие не снимаются.

**Минимальные изменения.** Если комплекта нет или он дороже бюджета, по одному ослабляется: даты ±1…3 дня,
поездка на 1–3 ночи короче, снять жёсткое ограничение дороги, разрешить другой транспорт, другое направление,
бюджет. Каждый вариант собирается по-настоящему. Возвращаются 2 лучших изменения разных видов:
сначала те, что не трогают hard-требования, затем меньший перерасход бюджета, затем меньшее изменение.
Поля: `kind`, `text` («Даты на 3 дня позже: 23.11–26.11 — 50 620 ₽»), `total_rub`, `over_budget_rub`,
`needs_consent` (затрагивает hard), `change` (`key`, `from`, `to` — под команду правки требования), `bundle`.
Сами изменения не применяются: их предлагают пользователю.

### Контракт с оркестратором B

B вызывает `app.planning.suggest.suggest_destinations(requirements, state)` (см. `app/agent/destination_search.py`) и получает:

```json
{"destinations": [...], "alternatives": [...], "notes": ["..."], "required_inputs": ["origin", "travel_window"]}
```

- `destinations` — опции направления (поля выше) с `feasible: true` и `evidence`. Климатические факты передаются как
  `{ref: "climate:AYT:11.sea_temp", value, role}`, B перечитывает их из каталога сам. Остальные факты несут происхождение:
  `trip:<город>:total_rub` (итог комплекта), `trip:<город>:travel_min` (дорога), `city:<город>:visa` —
  с `label`, `unit`, `kind`, `source`, `source_refs`, `synthetic`, `role` (`fit` / `risk`).
- `alternatives` — минимальные изменения, когда ничего не подходит: `id`, `label`, `confirmed: true`,
  `changes` в ключах B (`budget_total_rub`, `dates`, `destination`, `transport`, `max_stops`), `refs` и `evidence`
  с итогом пересобранного комплекта. Если изменение всё ещё дороже бюджета, в `changes` добавляется новый
  `budget_total_rub`: без него комплект после принятия не соберётся. Дополнительно: `kind`, `total_rub`,
  `over_budget_rub`, `needs_consent`, `bundle`.
- Указанное, но отсутствующее в каталоге направление → `notes` со списком доступных, без альтернатив.
- Направление указано и даты известны → только `alternatives` для этой поездки (B зовёт это после пустого `build_bundles`).

Ключи требований B, которые понимает трек A: `travel_month`, `travel_year` (предположение B о годе),
`duration_nights`, `children_count`, `children_ages`, `pet_weight_kg`, `budget_per_person_rub`,
`budget_preference: low_cost`, `travel_time_preference: short`, `max_duration_min`, `max_stops`, `trip_style`,
`date_flex_days` (сдвиг дат в пределах гибкости не требует согласия). Транспорт фильтрует только `kind: hard`.

Примеры: `data/examples/destinations_vague_sea.json`, `data/examples/changes_dxb_30000.json`, `data/examples/suggest_contract_vague.json`.

## Комплекты, проверки, пересборка (спринт 3)

**Три комплекта.** `build_bundles` / `build_trip_bundles` возвращают до трёх проверенных комплектов из одного перебора:

| id | label | Что оптимизируется |
|---|---|---|
| `b1` | `дешевле` | итоговая цена |
| `b2` | `удобнее с ребёнком` / `удобнее с животным` / `удобнее: без пересадок` / `удобнее` | цена + штраф в рублях за неудобства этого состава: пересадки, долгая дорога, ночные вылеты и прилёты, ожидание заселения, рейтинг и звёзды отеля, для детей — детский клуб и питание, общественный транспорт |
| `b3` | `гибче отмена` | цена + 0,8 × сумма, которую потеряете при отмене сегодня; показывается, только если реально гибче `b1` |

Комплекты не совпадают. Каждый проходит все проверки, включая жёсткий бюджет. Первый `tradeoffs[0]` — компромисс одной фразой:
«Самый дешёвый — 152 000 ₽; чем жертвуем: без багажа, с пересадкой», «+21 690 ₽ к самому дешёвому: без пересадок,
в дороге на 4 ч меньше, детский клуб в отеле…», «+51 300 ₽: бесплатная отмена отеля до 09.11, при отмене теряете
64 100 ₽ вместо 123 860 ₽». Подбор направлений и альтернативы используют быстрый режим `variants=1`.

**validate_plan (п.7).** Дата заезда с учётом часовых поясов и прилёта на следующий день; раннее прибытие (ожидание,
хранение багажа, ранний заезд только если он есть в условиях отеля, «по наличию» ≠ гарантия, ночь накануне);
прибытие при закрытой стойке (ключница → warn, иначе block); обратная дорога (трансфер + запас 2 ч / 1,5 ч / 40 мин,
поздний выезд или хранение багажа, нехватка ночи → block); вместимость — по номеру из каталога × количество
и по машине для людей и багажа; животное — правила перевозчика (салон/багаж по весу), отеля (разрешение и вес)
и трансфера, все три; бюджет; виза и документы; при оплате — актуальность каждой позиции.

**replan** — `app.planning.replan.replan(requirements, state, bundle_id=None)`:

```json
{"bundle": {...}, "kept": [{"offer_id","kind","title","start","total_rub","note"}],
 "replaced": [{"leg","from":{...},"to":{...},"why"}], "price_delta_rub": 18439,
 "change": "даты на 2 дн. позже, с животным", "notes": [],
 "history_entry": {"change","kept":[...],"replaced":["было → стало (почему)"],"price_delta_rub"},
 "alternatives": []}
```

- Берётся выбранный комплект `state` (`selected_bundle_id`, иначе `bundle_id`, иначе первый). Новые условия —
  из `requirements`, старые — из самих позиций комплекта. Поэтому неважно, обновлены ли уже требования в `state`.
- **Все** позиции перепроверяются под новые условия: цена, места, вместимость, животное, время, состыковки.
  Предпочтение у прежнего выбора: тот же рейс или поезд на новую дату, тот же отель, номер и тариф, тот же вид
  трансфера. Выбирается выполнимый план с наименьшим числом замен, затем самый дешёвый.
- `kept[].note`: «тот же вариант на новую дату 14.11», «цена 33 270 ₽ → 42 989 ₽», «2 номера вместо 1».
  `replaced[].why`: «SU 1130 не летает 14.11», «отель не принимает животных», «номер не вмещает новый состав»,
  «отель заменён», «нет мест или закрыт на …».
- `bundle` сохраняет `id` и `label` старого комплекта. `history_entry` — готовая запись для `TripState.history`
  (`version` ставит B).
- Если план не собирается: `bundle: null`, причина в `notes`, а в `alternatives` — подтверждённые минимальные
  изменения в формате `suggest_destinations`.

## Крайние случаи (A3)

Полный реестр с id объектов — `data/seed/catalog/meta.json` → `edge_cases`.

| id | Что | Где |
|---|---|---|
| `ec_night_arrival` | ночной прилёт на следующий день | SVX→AYT 22:30 → 01:20, SVO→AER 23:55 → 02:25, DME→KGD 23:40 → 00:50, поезд 104В в Адлер 03:44 |
| `ec_aer_no_night_reception`, `ec_ayt_no_night_reception` | нет круглосуточной стойки, ночью не заселиться → `block` | самый дешёвый отель Сочи и Антальи |
| `ec_kgd_keybox` | стойка закрывается, но есть ключница → `warn` | Калининград |
| `ec_no_pets_hotels` | отели без животных | в каждом направлении |
| `ec_no_pets_transfer` | ни один трансфер не берёт животных | Шарм-эль-Шейх, Дубай |
| `ec_dxb_pets`, `ec_n4_pets` | перевозчик не берёт животных | все рейсы в ОАЭ, чартер Nordwind |
| `ec_ayt_closed_season` | отели закрыты с 1 ноября | 2 курорта Антальи |
| `ec_evn_sold_out` | направление без вариантов на часть дат | Ереван, ночи 20–23.11.2026 |
| `ec_svx_ayt_program`, `ec_ssh_svx_days`, `ec_window_end` | даты вне расписания | Екб→Анталья после 15.11 только ср/вс, Екб→Шарм вт/сб, вне окна каталога |
| `ec_night_public` | ночью нет автобуса и шаттла | все города |
| `ec_budget_dxb` | бюджет невыполним | «Дубай на неделю на троих за 30 000» |

## Отклонения от текста архитектуры (на согласование)

- `Check` дополнен необязательным `refs: list[str]` — ссылки на данные, как у `Claim`.
- В `early_checkin` поле цены называется `price_rub` (единый суффикс `_rub`), ключ `from` сохранён.
- У B свои `Bundle` / `Check` / `Claim` в `app/state/models.py`; трек A отдаёт им совместимые dict (`bundle_to_wire`).
- `Offer.title` (строка для карточки) добавлен по `web/src/api/types.ts`; `Offer.baggage` — строка.
