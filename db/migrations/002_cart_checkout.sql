-- Трек C: поля для оформления поверх carts/orders из 001_init.sql.

ALTER TABLE carts ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE carts ADD COLUMN IF NOT EXISTS total_rub bigint NOT NULL DEFAULT 0 CHECK (total_rub >= 0);
ALTER TABLE carts ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'ordered'));

ALTER TABLE orders ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE orders ADD COLUMN IF NOT EXISTS total_rub bigint NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payer jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Одна корзина — максимум один заказ (второй клик «Оплатить» с новым ключом не создаст дубль).
CREATE UNIQUE INDEX IF NOT EXISTS orders_cart_id_uniq ON orders (cart_id);

-- Сумма заказа неизменяема так же, как снимок (триггер из 001 проверяет snapshot и идентичность).
CREATE OR REPLACE FUNCTION reject_order_total_change() RETURNS trigger AS $$
BEGIN
    IF NEW.total_rub IS DISTINCT FROM OLD.total_rub OR NEW.cart_id IS DISTINCT FROM OLD.cart_id THEN
        RAISE EXCEPTION 'order total and cart are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS orders_total_immutable ON orders;
CREATE TRIGGER orders_total_immutable BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION reject_order_total_change();

-- Снимок корзины тоже неизменяем: меняется только статус.
CREATE OR REPLACE FUNCTION reject_cart_snapshot_change() RETURNS trigger AS $$
BEGIN
    IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.total_rub IS DISTINCT FROM OLD.total_rub
       OR NEW.trip_version IS DISTINCT FROM OLD.trip_version OR NEW.bundle_id IS DISTINCT FROM OLD.bundle_id THEN
        RAISE EXCEPTION 'cart snapshot is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS carts_snapshot_immutable ON carts;
CREATE TRIGGER carts_snapshot_immutable BEFORE UPDATE ON carts
FOR EACH ROW EXECUTE FUNCTION reject_cart_snapshot_change();
