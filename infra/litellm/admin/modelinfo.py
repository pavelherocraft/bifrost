import json, urllib.request

mk = ""
with open("/opt/litellm/.env", encoding="utf-8") as f:
    for line in f:
        if line.startswith("LITELLM_MASTER_KEY="):
            mk = line.split("=", 1)[1].strip().strip("\r\n")
            break

req = urllib.request.Request(
    "http://127.0.0.1:4001/v1/model/info",
    headers={"Authorization": "Bearer " + mk},
)
d = json.load(urllib.request.urlopen(req, timeout=30))
data = d.get("data", [])
print("models in /model/info:", len(data))
priced = 0
for m in data:
    mi = m.get("model_info", {})
    name = mi.get("model_name", "?")
    it = mi.get("input_cost_per_token")
    ot = mi.get("output_cost_per_token")
    if it is not None or ot is not None:
        priced += 1
        print(f"{name:44s} in=${(it or 0)*1e6:8.4f}/M out=${(ot or 0)*1e6:8.4f}/M")
print(f"--- models with cost fields: {priced} of {len(data)}")
