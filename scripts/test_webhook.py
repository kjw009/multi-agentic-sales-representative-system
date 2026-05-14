import json, urllib.request, uuid

payload = json.dumps({
    "notification": {
        "itemId": "110589527549",
        "messageId": str(uuid.uuid4()),
        "buyerUsername": "testbuyer",
        "text": "Hi is this still available would you take 1325 pounds"
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
