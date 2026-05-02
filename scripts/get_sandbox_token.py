"""
eBay Sandbox Token Helper.

Gets a sandbox user token by guiding through the OAuth flow
and exchanging the code automatically.

Usage:
  1. Run this script
  2. It opens the eBay sandbox consent page in your browser
  3. After you consent, paste the full redirect URL back here
  4. The script exchanges the code for tokens and saves them to the DB

Prerequisites:
  - Sandbox test account: https://developer.ebay.com/sandbox/register
  - RuName configured for sandbox in eBay developer portal
"""

import asyncio
import base64
import hashlib
import os
import sys
import urllib.parse
import uuid

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from packages.config import settings
    from packages.crypto import encrypt_token
    from packages.db.models import Platform, PlatformCredential, Seller
    from packages.db.session import SessionLocal
    from packages.platform_adapters.ebay.oauth import token_expiry
    from sqlalchemy import select

    print("=" * 60)
    print(f"eBay Sandbox OAuth Token Helper")
    print(f"  Environment: {settings.ebay_env}")
    print(f"  Client ID: {settings.ebay_client_id}")
    print(f"  Redirect URI: {settings.ebay_redirect_uri}")
    print(f"  RuName: {settings.ebay_ru_name}" if hasattr(settings, 'ebay_ru_name') else "")
    print("=" * 60)

    if settings.ebay_env != "sandbox":
        print("⚠ WARNING: EBAY_ENV is not 'sandbox'. Set EBAY_ENV=sandbox in .env")
        return

    # Step 1: Generate PKCE
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    # Step 2: Build auth URL
    scopes = [
        "https://api.ebay.com/oauth/api_scope/sell.inventory",
        "https://api.ebay.com/oauth/api_scope/sell.account",
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
        "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
    ]

    ru_name = getattr(settings, 'ebay_ru_name', settings.ebay_redirect_uri)

    params = {
        "client_id": settings.ebay_client_id,
        "response_type": "code",
        "redirect_uri": ru_name,
        "scope": " ".join(scopes),
        "state": "sandbox-test",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"https://auth.sandbox.ebay.com/oauth2/authorize?{urllib.parse.urlencode(params)}"

    print("\n📋 Step 1: Open this URL in your browser:")
    print(f"\n{auth_url}\n")
    print("📋 Step 2: Log in with your eBay SANDBOX test account")
    print("   (Not your real eBay account!)")
    print("   Test accounts: https://developer.ebay.com/sandbox/register")
    print()
    print("📋 Step 3: After consent, you'll be redirected.")
    print("   Paste the FULL redirect URL below (it contains the auth code):")
    print()

    redirect_url = input("Redirect URL: ").strip()

    if not redirect_url:
        print("No URL provided — aborting")
        return

    # Extract code from URL
    parsed = urllib.parse.urlparse(redirect_url)
    query_params = urllib.parse.parse_qs(parsed.query)

    code = query_params.get("code", [None])[0]
    if not code:
        print(f"❌ No 'code' parameter found in URL")
        print(f"   Parsed params: {query_params}")
        return

    print(f"\n✅ Got auth code: {code[:20]}...")

    # Step 3: Exchange code for tokens
    print("\nExchanging code for tokens...")
    creds = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
    basic = base64.b64encode(creds.encode()).decode()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ru_name,
                "code_verifier": code_verifier,
            },
        )

    if r.status_code != 200:
        print(f"❌ Token exchange failed: {r.status_code}")
        print(r.text)
        return

    token_data = r.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_at = token_expiry(token_data.get("expires_in", 7200))

    print(f"✅ Access token obtained (expires {expires_at})")
    if refresh_token:
        print(f"✅ Refresh token obtained")

    # Step 4: Save to DB
    print("\nSaving tokens to database...")

    async with SessionLocal() as session:
        # Find the primary seller (traceropt@gmail.com)
        seller = await session.scalar(
            select(Seller).where(Seller.email == "traceropt@gmail.com")
        )
        if seller is None:
            # Use the first seller
            seller = await session.scalar(select(Seller).limit(1))

        if seller is None:
            print("❌ No sellers found in DB")
            return

        print(f"  Seller: {seller.email} (ID: {seller.id})")

        # Upsert credentials
        cred = await session.scalar(
            select(PlatformCredential).where(
                PlatformCredential.seller_id == seller.id,
                PlatformCredential.platform == Platform.ebay,
            )
        )
        if cred is None:
            cred = PlatformCredential(seller_id=seller.id, platform=Platform.ebay)
            session.add(cred)

        cred.oauth_token_enc = encrypt_token(access_token)
        cred.refresh_token_enc = encrypt_token(refresh_token) if refresh_token else None
        cred.expires_at = expires_at
        cred.key_version = 1

        await session.commit()
        print(f"✅ Tokens saved for seller {seller.id}")

    # Step 5: Quick API test
    print("\nTesting sandbox Sell API access...")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
            },
            params={"marketplace_id": "EBAY_GB"},
        )
        print(f"  Fulfillment policies: {r.status_code}")
        if r.status_code == 200:
            policies = r.json().get("fulfillmentPolicies", [])
            print(f"  ✅ {len(policies)} policies found")
        else:
            print(f"  Response: {r.text[:200]}")

    print("\n" + "=" * 60)
    print("SETUP COMPLETE!")
    print("Now run: uv run python scripts/test_publisher_sandbox.py --live")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
