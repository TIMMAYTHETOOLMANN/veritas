import urllib.request, json, hashlib, hmac, time
ts = str(int(time.time()*1000))
    print("Success:", resp.read().decode())
req = urllib.request.Request("https://api.hyperliquid.xyz/exchange", data=json.dumps(payload).encode(), headers=headers)
headers = {"Content-Type": "application/json", "X-API-Key": "0xf938ea550f06b772f3d21d27af1aade7032fbc45", "X-API-Signature": sig, "X-API-Timestamp": ts}
    resp = urllib.request.urlopen(req, timeout=10)
