import sys
sys.path.insert(0, ".")
from datetime import date
from app.catalog.store import get_catalog
from app.catalog.models import Party
from app.tools.search import search_hotels

cat = get_catalog()
party = Party(adults=2, children_ages=[], pets=[])
d1 = date(2026, 12, 30)
d2 = date(2027, 1, 6)

cities = ['KZN', 'AER', 'DXB', 'DPS', 'MLE', 'PAR', 'MOW', 'LED']
for city in cities:
    offers = search_hotels(cat, city, d1, d2, party, cheapest_per_hotel=True)
    print(f"\n=== {city} ({len(offers)} hotels) ===")
    for o in offers[:4]:
        hotel_name = o.variant.get("name") if o.variant else o.title
        print(f"  [{hotel_name}] -> {o.source_url}")
        assert o.source_url and "ostrovok.ru/hotel/" in o.source_url
        assert "?q=" not in o.source_url, f"Error: found ?q= in {o.source_url}"
        assert "/mid" in o.source_url, f"Error: missing /mid in {o.source_url}"

print("\nALL HOTEL URL CHECKS PASSED PERFECTLY!")
