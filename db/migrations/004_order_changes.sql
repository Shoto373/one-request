-- Работа после покупки: поправки к заказу (сдвиг дат, ранний заезд).
-- Снимок заказа неизменяем (триггер orders_snapshot_immutable), поэтому изменение — отдельная запись
-- с полным новым составом и расчётом доплаты; действующий план заказа = последняя поправка.
CREATE TABLE IF NOT EXISTS order_changes (
    id uuid PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES orders(id),
    kind text NOT NULL,                      -- date_shift | early_checkin
    idempotency_key text NOT NULL UNIQUE,
    quote jsonb NOT NULL,                    -- что было подтверждено: новый состав, штрафы, доплата
    status text NOT NULL DEFAULT 'confirmed',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_changes_order_idx ON order_changes (order_id, created_at);
