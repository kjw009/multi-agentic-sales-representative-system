import asyncio
import httpx
import sys

from packages.config import settings
from packages.platform_adapters.ebay.oauth import _token_url, _basic_auth

async def get_app_token():
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _token_url(),
            headers={
                "Authorization": f"Basic {_basic_auth()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]

async def main():
    print(f"Obtaining application token for env: {settings.ebay_env}...")
    token = await get_app_token()
    
    base_url = "https://api.sandbox.ebay.com" if settings.ebay_env == "sandbox" else "https://api.ebay.com"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    print("Updating notification config...")
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{base_url}/commerce/notification/v1/config",
            headers=headers,
            json={
                "alertEmail": "dev@salesrep.com"
            }
        )
        if r.status_code not in (200, 201, 204):
            print(f"Failed to update config: {r.status_code} {r.text}")
    
    print("Creating destination...")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{base_url}/commerce/notification/v1/destination",
            headers=headers,
            json={
                "name": "SalesRep_Messages",
                "status": "ENABLED",
                "deliveryConfig": {
                    "endpoint": settings.ebay_webhook_endpoint,
                    "verificationToken": settings.ebay_verification_token
                }
            }
        )
        if r.status_code in (201, 204, 200):
            print(f"Destination created or updated successfully! Status: {r.status_code}")
            # Fetch the ID
            r2 = await client.get(f"{base_url}/commerce/notification/v1/destination", headers=headers)
            dest_id = None
            for d in r2.json().get("destinations", []):
                if d.get("deliveryConfig", {}).get("endpoint") == settings.ebay_webhook_endpoint:
                    dest_id = d["destinationId"]
                    break
            if not dest_id:
                print("Failed to fetch the created destination ID.")
                return
            print(f"Destination ID: {dest_id}")
        elif r.status_code == 409:
            print(f"409 error: {r.text}")
            print("Destination URL already exists. Fetching existing destinations...")
            r2 = await client.get(f"{base_url}/commerce/notification/v1/destination", headers=headers)
            dest_id = None
            for d in r2.json().get("destinations", []):
                if d.get("deliveryConfig", {}).get("endpoint") == settings.ebay_webhook_endpoint:
                    dest_id = d["destinationId"]
                    break
            if not dest_id:
                print("Failed to find existing destination ID.")
                return
            print(f"Using existing destination ID: {dest_id}")
        else:
            print(f"Failed to create destination: {r.status_code} {r.text}")
            return

    print("Creating subscription...")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{base_url}/commerce/notification/v1/subscription",
            headers=headers,
            json={
                "destinationId": dest_id,
                "status": "ENABLED",
                "topicId": "MARKETPLACE.MESSAGING.MESSAGE.RECEIVED",
                "payload": {
                    "format": "JSON",
                    "schemaVersion": "1.0",
                    "deliveryProtocol": "HTTPS"
                }
            }
        )
        if r.status_code in (201, 204, 200):
            print(f"Subscription created! ID: {r.json().get('subscriptionId', 'success')}")
        elif r.status_code == 409:
            print("Already subscribed!")
        else:
            print(f"Failed to subscribe: {r.status_code} {r.text}")

if __name__ == "__main__":
    asyncio.run(main())
