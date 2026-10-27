import urllib.request
import json
import uuid

BASE_URL = "http://35.253.32.159:8080"

def run_test():
    # 1. Create session
    req = urllib.request.Request(f"{BASE_URL}/api/sessions", data=b"{}", headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    sid, ver = res["session_id"], res["version"]
    print(f"Session: {sid}, version: {ver}", flush=True)

    # 2. Send query
    query = "хочу в казань из рязани с 30 декабря по 6 января на 3 человек"
    payload = {"request_id": str(uuid.uuid4()), "text": query, "expected_version": ver}
    req2 = urllib.request.Request(f"{BASE_URL}/api/sessions/{sid}/messages", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(req2, timeout=40).read().decode("utf-8")
    print("Stream response received", flush=True)

    # 3. Read state
    req3 = urllib.request.Request(f"{BASE_URL}/api/sessions/{sid}")
    st = json.loads(urllib.request.urlopen(req3, timeout=10).read().decode("utf-8")).get("state", {})
    bundles = st.get("bundles", [])
    print(f"Bundles returned: {len(bundles)}", flush=True)
    for b in bundles:
        print(f"\nBundle: {b.get('label')} - {b.get('total_rub')} RUB", flush=True)
        for it in b.get("items", []):
            kind = it.get("kind")
            name = it.get("title") or (it.get("variant") or {}).get("name")
            src = it.get("source_url")
            print(f"  [{kind}] {name} -> {src}", flush=True)

if __name__ == "__main__":
    run_test()
