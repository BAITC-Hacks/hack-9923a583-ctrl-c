# hack-9923a583-ctrl-c
Hackathon team repository for ctrl+C

## Backend: каталог подрядчиков

Первый этап бэкенда использует SQLite. Схема хранит профили в `vendors`, а списки категорий, форматов, языков и занятых дат — в JSON-столбцах.

Когда Python 3.10+ установлен, из корня проекта создайте и заполните локальную базу:

```powershell
python backend/import_dataset.py
```

По умолчанию скрипт читает `hackathon dataset anonymized .csv` в корне проекта и создаёт `backend/data/vendors.sqlite3`. Повторный импорт обновляет профили с теми же ID. Путь к CSV или базе можно переопределить флагами `--csv` и `--db`.
