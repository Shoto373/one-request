import json
import urllib.request
import urllib.parse
import time

hotels = json.load(open('data/seed/catalog/hotels.json', encoding='utf-8'))
results = {}

city_names = {
    "AER": "Сочи",
    "KGD": "Калининград",
    "KZN": "Казань",
    "IST": "Стамбул Istanbul",
    "AYT": "Анталья Antalya",
    "SSH": "Шарм Sharm",
    "DXB": "Дубай Dubai",
    "EVN": "Ереван Yerevan",
}

for h in hotels:
    hid = h['id']
    name = h['name']
    city_id = h['city_id']
    c_name = city_names.get(city_id, city_id)
    
    # Try exact name first, then with city
    queries = [
        f"{name} {c_name}",
        name,
    ]
    found = False
    for q in queries:
        url = f"https://ostrovok.ru/api/site/multicomplete.json?query={urllib.parse.quote(q)}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=3).read().decode('utf-8'))
            htls = data.get('hotels', [])
            if htls:
                best = htls[0]
                mid = best.get('master_id') or best.get('otahotel_id')
                slug = best.get('slug')
                ota_id = best.get('otahotel_id')
                full_slug = f"{slug}/mid{mid}/{ota_id}"
                results[hid] = {
                    "name": name,
                    "city_id": city_id,
                    "full_slug": full_slug,
                    "ostrovok_name": best.get('hotel_name'),
                }
                print(f"OK {hid} ({name}) -> {full_slug}", flush=True)
                found = True
                break
        except Exception as e:
            pass
        time.sleep(0.05)
    if not found:
        print(f"FAILED {hid} ({name})", flush=True)

with open('data/seed/catalog/hotel_slugs.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"Total resolved: {len(results)} of {len(hotels)}", flush=True)
