import json, urllib.request, uuid

payload = json.dumps({
    "notification": {
        "itemId": "110589528217",
        "messageId": str(uuid.uuid4()),
        "buyerUsername": "testbuyer123",
        "text": "2gb ram ? that doesn't sound right. are you sure that right and not a mistake?"
    }
}).encode()

req = urllib.request.Request(
    "https://devopslearn.store/ebay/webhook",
    data=payload,
    headers={"Content-Type": "application/json", "X-EBAY-SIGNATURE": "test"},
    method="POST",
)
try:
    resp = urllib.request.urlopen(req)
    print(resp.status, resp.read())
except urllib.error.HTTPError as e:
    print(e.status, e.read())
