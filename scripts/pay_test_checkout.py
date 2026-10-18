"""Pay a Stripe TEST hosted Checkout link with the 4242 test card in headless Chrome (dev tool, no real money).

Usage: uv run --with playwright python scripts/pay_test_checkout.py <checkout-url>
Refuses anything that is not a checkout.stripe.com URL for a cs_test_ session. Uses the installed Google
Chrome (channel="chrome"); nothing is downloaded. This exercises the real Stripe test-mode checkout, so the
Exchange's server-side verification sees a genuinely paid session.
"""

import re
import sys

from playwright.sync_api import sync_playwright


def pay(url: str, headless: bool = True) -> str:
    if not re.match(r"^https://checkout\.stripe\.com/c/pay/cs_test_", url):
        raise SystemExit("refusing: not a Stripe TEST checkout URL")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        accordion = page.locator('[data-testid="card-accordion-item-button"]')
        if accordion.count() and accordion.first.is_visible():
            accordion.first.click()
        page.fill("#email", "buyer@example.com")
        page.fill("#cardNumber", "4242 4242 4242 4242")
        page.fill("#cardExpiry", "12 / 34")
        page.fill("#cardCvc", "123")
        page.fill("#billingName", "Demo Buyer")
        if page.locator("#billingPostalCode").count() and page.locator("#billingPostalCode").is_visible():
            page.fill("#billingPostalCode", "90024")
        page.locator('[data-testid="hosted-payment-submit-button"], button.SubmitButton').first.click()
        page.wait_for_url(lambda u: "checkout.stripe.com" not in u, timeout=60_000)
        landed = page.url
        browser.close()
        return landed


if __name__ == "__main__":
    print("paid; redirected to", pay(sys.argv[1]).split("?")[0])
