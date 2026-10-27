import os
import urllib.request
import json
import uuid

BASE_URL = os.getenv("BASE_URL", "http://35.253.32.159:8080")

def test_query(text):
    print(f"=== Query: {text} ===")
    req = urllib.request.Request(f"{BASE_URL}/api/sessions", data=b"{}", headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    sid, ver = res["session_id"], res["version"]
    
    payload = {"request_id": str(uuid.uuid4()), "text": text, "expected_version": ver}
    req2 = urllib.request.Request(f"{BASE_URL}/api/sessions/{sid}/messages", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    raw_stream = urllib.request.urlopen(req2, timeout=40).read().decode("utf-8")
    
    # print agent message from SSE stream if present
    for line in raw_stream.splitlines():
        if line.startswith("data: ") and '"message":' in line:
            try:
                d = json.loads(line[6:])
                if "message" in d:
                    print("Agent reply:", d["message"])
            except Exception:
                pass

    req3 = urllib.request.Request(f"{BASE_URL}/api/sessions/{sid}")
    state = json.loads(urllib.request.urlopen(req3, timeout=10).read().decode("utf-8")).get("state", {})
    bundles = state.get("bundles", [])
    open_questions = state.get("open_questions", [])
    if open_questions:
        print(f"Open questions: {[q.get('question') for q in open_questions]}")
    print(f"Bundles found: {len(bundles)}")
    for b in bundles:
        print(f"  [{b.get('label')}] Total: {b.get('total_rub')} RUB")
        for item in b.get("items", []):
            kind = item.get("kind")
            prov = item.get("provider")
            src_url = item.get("source_url")
            legs = item.get("variant", {}).get("legs", [])
            title = item.get('title', '').encode('ascii', 'replace').decode('ascii')
            print(f"    Item ({kind}, {prov}): {title} -> {item.get('total_rub')} RUB")
            print(f"      source_url: {src_url}")
            if legs:
                print(f"      Legs ({len(legs)}):")
                for leg in legs:
                    f_name = str(leg.get('from')).encode('ascii', 'replace').decode('ascii')
                    t_name = str(leg.get('to')).encode('ascii', 'replace').decode('ascii')
                    print(f"        - {leg.get('mode') or 'leg'}: {f_name} -> {t_name} | {leg.get('duration_min')} min | {leg.get('price_rub')} RUB | url: {leg.get('source_url')}")

if __name__ == "__main__":
    test_query("хочу из Рязани в Париж с 10 по 15 ноября, 1 взрослый")

