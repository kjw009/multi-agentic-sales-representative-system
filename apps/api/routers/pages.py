"""Static legal pages required by eBay's RuName configuration.

Serves HTML directly so these URLs can be set in the eBay Developer Portal
without depending on the frontend being deployed.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["pages"])

_STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 40px auto; padding: 0 20px; color: #1a1a1a;
         line-height: 1.7; }
  h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.2rem; margin-top: 2rem; }
  p, li { font-size: 0.95rem; color: #333; }
  a { color: #2563eb; }
  .updated { font-size: 0.85rem; color: #888; margin-bottom: 2rem; }
</style>
"""


@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy — SalesRep</title>{_STYLE}</head>
<body>
<h1>Privacy Policy</h1>
<p class="updated">Last updated: 2 May 2025</p>

<p>SalesRep ("we", "our", "us") is a multi-agent AI selling assistant that helps
sellers list and manage items on online marketplaces. This policy explains how we
handle your data.</p>

<h2>1. Information We Collect</h2>
<ul>
  <li><strong>Account information</strong> — email address and a hashed password when
  you sign up.</li>
  <li><strong>Item data</strong> — item names, descriptions, images, and pricing
  information you provide through the intake chat.</li>
  <li><strong>Platform tokens</strong> — when you connect an eBay account, we store
  encrypted OAuth tokens so we can list items and manage messages on your behalf.
  We never store your eBay password.</li>
  <li><strong>Usage data</strong> — server logs that include IP addresses and request
  timestamps for security and debugging purposes.</li>
</ul>

<h2>2. How We Use Your Information</h2>
<ul>
  <li>To provide the selling assistant service (listing items, pricing, buyer communication).</li>
  <li>To authenticate you and maintain your session.</li>
  <li>To interact with eBay APIs on your behalf using your authorised tokens.</li>
  <li>To improve the service and fix bugs.</li>
</ul>

<h2>3. Data Sharing</h2>
<p>We do not sell your personal data. We share data only with:</p>
<ul>
  <li><strong>eBay</strong> — item listings and messages, via their official APIs, as
  authorised by you.</li>
  <li><strong>AI providers</strong> — item descriptions and chat messages are sent to
  our AI model provider (OpenAI / Azure OpenAI) for processing. No eBay credentials
  are included in AI requests.</li>
</ul>

<h2>4. Data Security</h2>
<p>OAuth tokens are encrypted at rest using AES-256-GCM. Passwords are hashed with
bcrypt. All communication uses HTTPS.</p>

<h2>5. Data Retention</h2>
<p>We retain your account data and item data for as long as your account is active.
You can request deletion by contacting us.</p>

<h2>6. Your Rights</h2>
<p>You may request access to, correction of, or deletion of your personal data at any
time by emailing us.</p>

<h2>7. Contact</h2>
<p>For privacy questions, contact us at
<a href="mailto:privacy@devopslearn.store">privacy@devopslearn.store</a>.</p>
</body></html>"""


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Service — SalesRep</title>{_STYLE}</head>
<body>
<h1>Terms of Service</h1>
<p class="updated">Last updated: 2 May 2025</p>

<p>By using SalesRep you agree to the following terms.</p>

<h2>1. Service Description</h2>
<p>SalesRep is an AI-powered tool that assists with listing and selling items on
eBay and other marketplaces. The service provides pricing recommendations and
automated listing creation based on information you provide.</p>

<h2>2. Your Responsibilities</h2>
<ul>
  <li>You are responsible for the accuracy of item descriptions and images you provide.</li>
  <li>You must have the legal right to sell items listed through the service.</li>
  <li>You are responsible for complying with eBay's terms of service and applicable laws.</li>
</ul>

<h2>3. Pricing Recommendations</h2>
<p>Pricing suggestions are based on comparable listings and machine learning models.
They are recommendations only — you are free to set any price you choose. We do not
guarantee the accuracy of pricing estimates.</p>

<h2>4. Platform Access</h2>
<p>When you connect your eBay account, you authorise SalesRep to create listings, manage
inventory, and respond to buyer messages on your behalf. You can revoke this access at
any time through your eBay account settings.</p>

<h2>5. Limitation of Liability</h2>
<p>SalesRep is provided "as is". We are not liable for any losses resulting from
pricing recommendations, listing errors, or platform outages.</p>

<h2>6. Contact</h2>
<p>Questions? Email <a href="mailto:support@devopslearn.store">support@devopslearn.store</a>.</p>
</body></html>"""
