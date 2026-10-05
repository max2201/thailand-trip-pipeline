# Данные для сайтов поездки по Таиланду

Собирает с trip.com отели, цены на наши даты и отзывы, анализирует отзывы и готовит данные для сайтов
[`thailand-trip-vue`](https://github.com/max2201/thailand-trip-vue) и [`thailand-trip-react`](https://github.com/max2201/thailand-trip-react).

## Как это работает

```
trip.com ──► run.py prices   ──► cache/lists/<остановка>.json.gz   выдача и цены на даты остановки
         └─► run.py reviews  ──► cache/hotels/<город>.jsonl         анализ отзывов (сами тексты не храним)
content/ ──► run.py build    ──► site-data/                         index.json, cities/, prices/
```

- **Каждый день в 08:00 по Бангкоку** GitHub Actions запускает `run.py all`: обновляет цены, докачивает отзывы для новых отелей, пересобирает `site-data/` и коммитит изменения.
- **Сайты каждый день забирают `site-data/`** из этого репозитория при сборке. Токены и секреты не нужны: репозиторий публичный.
- Запустить вручную: **Actions → Update data → Run workflow** (режим `prices`, `reviews`, `build` или `all`).

## Локально

```bash
pip install -r requirements.txt
python run.py all               # всё: цены, отзывы новых отелей, сборка
python run.py prices --stop s4  # цены одной остановки
python run.py reviews --refresh # перекачать отзывы всех отелей (долго)
python run.py build             # только пересобрать site-data/ из кеша
```

## Что где

| Файл | Что это |
|---|---|
| `content/stops.json` | маршрут: даты, отели из плана, приоритетные районы, события |
| `content/guides.json` | гид: районы, места, поездки до 2 часов |
| `content/saved.json` | сохранённые отели на trip.com |
| `content/settings.json` | лимиты цены за ночь по городам, сколько страниц отзывов брать |
| `content/*_source.py` | исходники stops/guides в виде Python — удобно править и перегенерировать JSON |
| `tripdata/tripcom.py` | запросы к trip.com |
| `tripdata/analyze.py` | темы в отзывах: насекомые, запах, сырость, шум и т. д. |
| `tripdata/notes.py` | «Чем известен» и «Осторожно» |
| `tripdata/build.py` | моя оценка, плюсы и минусы, расположение, экспорт для сайтов |

Если trip.com ограничит запросы с серверов GitHub, шаг `prices` не перезапишет старые цены (есть защита от пустой выдачи), а сайты продолжат работать на последних данных. Тогда можно запускать `python run.py all` локально и пушить.
