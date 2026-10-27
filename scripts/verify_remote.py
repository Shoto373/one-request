import urllib.request
import json
import uuid

def test_query(text):
    print(f"=== Query: {text} ===")
    req = urllib.request.Request("http://localhost:8080/api/sessions", data=b"{}", headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    sid, ver = res["session_id"], res["version"]
    
    payload = {"request_id": str(uuid.uuid4()), "text": text, "expected_version": ver}
    req2 = urllib.request.Request(f"http://localhost:8080/api/sessions/{sid}/messages", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req2).read()
    
    req3 = urllib.request.Request(f"http://localhost:8080/api/sessions/{sid}")
    state = json.loads(urllib.request.urlopen(req3).read().decode("utf-8")).get("state", {})
    bundles = state.get("bundles", [])
    open_questions = state.get("open_questions", [])
    if open_questions:
        print(f"Open questions: {[q.get('question') for q in open_questions]}")
    print(f"Bundles found: {len(bundles)}")
    for b in bundles:
        hotel = next((i for i in b.get("items", []) if i.get("kind") == "hotel"), None)
        h_name = hotel.get("variant", {}).get("hotel_name") if hotel else None
        h_stars = hotel.get("variant", {}).get("stars") if hotel else None
        tariff = hotel.get("variant", {}).get("tariff") if hotel else None
        url = hotel.get("source_url") if hotel else None
        print(f"  [{b.get('label')}] Total: {b.get('total_rub')} RUB | Hotel: {h_name} ({h_stars}*) | Tariff: {tariff} | Provider: {hotel.get('provider')}")
        print(f"    URL: {url}")

if __name__ == "__main__":
    test_query("хочу из Санкт-Петербурга в Москву с 10 по 15 ноября в 5 звезд с собакой 5 кг и завтраком, 1 взрослый")
    test_query("хочу из Москвы в Париж с 10 по 15 ноября недорого, 2 взрослых")
