-- Память предпочтений между сессиями: анонимный профиль браузера (id хранится у клиента).
-- prefs — устойчивые предпочтения из оплаченных поездок; агент подставляет их догадками (source=memory).
CREATE TABLE IF NOT EXISTS profiles (
    id uuid PRIMARY KEY,
    prefs jsonb NOT NULL DEFAULT '{}'::jsonb,
    trips integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
