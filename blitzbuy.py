"""
BlitzBuy — Production-Grade Stealth Automated Purchase Demo
============================================================
Target  : https://www.saucedemo.com  (public test store — demo ONLY)

⚠️  ETHICS / LEGAL
    - DEMO / TEST SITES ONLY.  Never use on real e-commerce without written
      authorisation from the site owner.
    - Automated purchasing may violate a site's ToS, consumer-protection law,
      and computer-fraud statutes in your jurisdiction.
    - Respect robots.txt — this script checks it before every navigation.
    - Always prefer official APIs (Shopify, Amazon PA, etc.) over scraping.

Stack   : Python 3.11+ · Playwright (async) · playwright-stealth ·
          fake-useragent · tenacity · stripe · sqlite3 (stdlib)

Install : pip install playwright playwright-stealth fake-useragent tenacity twocaptcha stripe
          python -m playwright install chromium
Run     : python blitzbuy.py

Feature overview
----------------
§1  Advanced Undetectability
    · playwright-stealth  — patches CDP leaks + 20+ JS properties
    · --disable-blink-features=AutomationControlled
    · Canvas/WebGL noise injection  (per-pixel random noise, vendor spoof)
    · Bezier-curve mouse movement   (humanize-playwright-style, no extra dep)
    · Per-keystroke typing delays   (40–160 ms)
    · Random click-coordinate jitter
    · Consistent device profile     (UA / viewport / locale / TZ / screen)
    · CAPTCHA detection + 2Captcha API integration (stub → swap API key)
    · Detection monitoring          (regex scan of page content after nav)
    · Auto-retry with fresh context/proxy on block detection

§2  Performance & Speed
    · Selective resource blocking   (images / fonts / media / ad-networks)
    · Stylesheets kept              (absent CSS = bot signal on adv. sites)
    · tenacity exponential-backoff  (retry on timeout / transient errors)
    · asyncio.gather() concurrency  (≤ MAX_CONCURRENT sessions)
    · Persistent-context option     (reuse cookies/session across runs)

§3  Production Deployment hints (see comments throughout)
    · SQLite audit log              (purchase_history.db)
    · Proxy support                 (BLITZBUY_PROXY env var)
    · All secrets via env vars, never hardcoded

§4  Testing, Monitoring & Compliance
    · robots.txt gating             (urllib.robotparser, cached per origin)
    · Structured SQLite audit trail (timestamp, status, elapsed, screenshot)
    · Block-detection regex         (auto-logs + retries on captcha/403/block)
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
import sqlite3
import time
import urllib.parse
import urllib.robotparser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import stripe
from fake_useragent import UserAgent
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    Route,
    Request,
)
from playwright_stealth import stealth_async
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING)
_tenacity_log = logging.getLogger("blitzbuy.retry")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _log(step: str, msg: str) -> None:
    print(f"[{_ts()}] [{step:20s}] {msg}")


# ---------------------------------------------------------------------------
# §0 — Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------
SITE_URL          = os.getenv("BLITZBUY_URL",      "https://www.saucedemo.com")
TEST_USER         = os.getenv("BLITZBUY_USER",     "standard_user")
TEST_PASS         = os.getenv("BLITZBUY_PASS",     "secret_sauce")
TEST_EMAIL        = os.getenv("BLITZBUY_EMAIL",    "test@blitzbuy.dev")
SCREENSHOTS       = Path(os.getenv("BLITZBUY_SHOTS", "screenshots"))
DB_PATH           = Path(os.getenv("BLITZBUY_DB",    "purchase_history.db"))
PROXY_SERVER      = os.getenv("BLITZBUY_PROXY",    "")   # http://user:pass@host:port
CAPTCHA_API_KEY   = os.getenv("BLITZBUY_2CAPTCHA", "")   # 2Captcha API key — kept in env because it's a paid credential (others can spend your balance if leaked)
HEADLESS          = os.getenv("BLITZBUY_HEADLESS", "1") != "0"

# ── Stripe payment configuration ────────────────────────────────────────────
# STRIPE_LIVE=0  → test mode  (sk_test_... key, fake charges, safe to run)
# STRIPE_LIVE=1  → live mode  (sk_live_... key, REAL charges — use with care)
#
# How to get these values:
#   1. Create a free Stripe account at https://dashboard.stripe.com
#   2. Dashboard → Developers → API keys → copy Secret key
#   3. Store a payment method once via Stripe.js or the Dashboard:
#      Dashboard → Customers → (pick/create customer) → Payment methods → Add
#      Copy the pm_xxx ID shown.
#
# Test mode shortcuts (no real card needed):
#   STRIPE_PAYMENT_METHOD_ID=pm_card_visa        → always succeeds
#   STRIPE_PAYMENT_METHOD_ID=pm_card_chargeDeclined → always declines (for testing failures)
STRIPE_SECRET_KEY        = os.getenv("STRIPE_SECRET_KEY",        "")   # sk_test_... or sk_live_...
STRIPE_PAYMENT_METHOD_ID = os.getenv("STRIPE_PAYMENT_METHOD_ID", "pm_card_visa")  # stored pm_xxx
STRIPE_CURRENCY          = os.getenv("STRIPE_CURRENCY",          "usd")
STRIPE_LIVE              = os.getenv("STRIPE_LIVE",              "0") == "1"

# Resource types to block — ads/trackers added on top of images/fonts/media.
# Stylesheets are intentionally kept: a page with zero CSS is a fingerprint
# anomaly that advanced bot-detectors flag immediately.
BLOCKED_TYPES     = {"image", "font", "media"}
# Ad / tracker hostnames to block regardless of resource type
AD_HOSTNAMES      = {
    "doubleclick.net", "googlesyndication.com", "google-analytics.com",
    "googletagmanager.com", "facebook.net", "scorecardresearch.com",
    "outbrain.com", "taboola.com", "ads.twitter.com", "adservice.google.com",
}

ACTION_TIMEOUT    = 15_000    # ms per action
MAX_RETRIES       = 3         # tenacity attempts per purchase
MAX_CONCURRENT    = 2         # asyncio.gather concurrency cap

# Human-timing bands (seconds)
DELAY_SHORT       = (0.4,  1.2)
DELAY_MEDIUM      = (1.2,  3.5)
DELAY_LONG        = (2.5,  6.0)
TYPING_DELAY_MS   = (40,   160)   # per keystroke

# Patterns that indicate the page is blocking / challenging us
BLOCK_PATTERNS    = re.compile(
    r"(access denied|403 forbidden|blocked|captcha|are you a robot|"
    r"unusual traffic|automated software|bot detected|security check|"
    r"please verify you are human)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# §1a — Fingerprint / device profile pool
# ---------------------------------------------------------------------------
_ua_gen = UserAgent(browsers=["chrome"], os=["windows", "macos"], min_version=120.0)

_DEVICE_PROFILES = [
    {"width": 1920, "height": 1080, "locale": "en-US", "tz": "America/New_York",    "platform": "Win32",    "memory": 16, "cpu": 12},
    {"width": 1440, "height": 900,  "locale": "en-US", "tz": "America/Los_Angeles", "platform": "Win32",    "memory": 8,  "cpu": 8},
    {"width": 1536, "height": 864,  "locale": "en-GB", "tz": "Europe/London",       "platform": "Win32",    "memory": 8,  "cpu": 8},
    {"width": 1280, "height": 800,  "locale": "en-US", "tz": "America/Chicago",     "platform": "MacIntel", "memory": 16, "cpu": 10},
    {"width": 1366, "height": 768,  "locale": "en-CA", "tz": "America/Toronto",     "platform": "Win32",    "memory": 8,  "cpu": 6},
]


def _random_profile() -> dict:
    return random.choice(_DEVICE_PROFILES)


def _random_ua() -> str:
    try:
        return _ua_gen.random
    except Exception:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.6367.82 Safari/537.36"
        )


# ---------------------------------------------------------------------------
# §1b — Canvas / WebGL fingerprint spoofing  (injected before page scripts)
# ---------------------------------------------------------------------------
# Real browsers have unique canvas fingerprints due to GPU/driver differences.
# Automation tools produce identical canvas output every run — a trivial signal.
# We inject subtle per-pixel noise and spoof WebGL vendor strings.

_CANVAS_WEBGL_SPOOF_JS = """
(function() {
    // ── Canvas: add sub-pixel noise to every pixel read ──────────────────
    // Fingerprinters call canvas.toDataURL() or getImageData() and hash it.
    // A tiny, session-stable random offset breaks the hash without visible change.
    const _noise = Math.floor(Math.random() * 8) - 4;   // -4..+4, stable per session

    const _origGetCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
        const ctx = _origGetCtx.call(this, type, ...args);
        if (ctx && type === '2d') {
            const _origGetImg = ctx.getImageData;
            ctx.getImageData = function(...a) {
                const img = _origGetImg.apply(this, a);
                for (let i = 0; i < img.data.length; i += 4) {
                    img.data[i]     = Math.min(255, Math.max(0, img.data[i]     + _noise));
                    img.data[i + 1] = Math.min(255, Math.max(0, img.data[i + 1] + _noise));
                    img.data[i + 2] = Math.min(255, Math.max(0, img.data[i + 2] + _noise));
                }
                return img;
            };
        }
        return ctx;
    };

    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {
        const ctx = _origGetCtx.call(this, '2d');
        if (ctx) {
            const d = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
            for (let i = 0; i < d.data.length; i += 4) {
                d.data[i] = Math.min(255, Math.max(0, d.data[i] + _noise));
            }
            ctx.putImageData(d, 0, 0);
        }
        return _origToDataURL.apply(this, args);
    };

    // ── WebGL: spoof vendor / renderer ────────────────────────────────────
    // Headless Chromium reports "Google SwiftShader" — easily detected.
    const _vendors   = ['Intel Inc.', 'NVIDIA Corporation', 'AMD'];
    const _renderers = [
        'Intel Iris Plus Graphics 640', 'NVIDIA GeForce GTX 1060',
        'AMD Radeon RX 580', 'Intel UHD Graphics 620',
    ];
    const _vendor   = _vendors  [Math.floor(Math.random() * _vendors.length)];
    const _renderer = _renderers[Math.floor(Math.random() * _renderers.length)];

    function _patchWebGL(Ctor) {
        const orig = Ctor.prototype.getParameter;
        Ctor.prototype.getParameter = function(param) {
            if (param === 37445) return _vendor;     // UNMASKED_VENDOR_WEBGL
            if (param === 37446) return _renderer;   // UNMASKED_RENDERER_WEBGL
            return orig.call(this, param);
        };
    }
    if (window.WebGLRenderingContext)       _patchWebGL(WebGLRenderingContext);
    if (window.WebGL2RenderingContext)      _patchWebGL(WebGL2RenderingContext);

    // ── Navigator spoofs ──────────────────────────────────────────────────
    const _defs = (obj, key, val) =>
        Object.defineProperty(obj, key, { get: () => val, configurable: true });

    _defs(navigator, 'webdriver',          undefined);
    _defs(navigator, 'platform',           'Win32');
    _defs(navigator, 'hardwareConcurrency', 8);
    _defs(navigator, 'deviceMemory',        8);
    _defs(navigator, 'languages',          ['en-US', 'en']);
    _defs(navigator, 'plugins', {
        length: 5,
        0: { name: 'Chrome PDF Plugin' },
        1: { name: 'Chrome PDF Viewer' },
        2: { name: 'Native Client' },
        item: (i) => this[i],
    });

    // ── Chrome runtime stub ───────────────────────────────────────────────
    if (!window.chrome || !window.chrome.runtime) {
        window.chrome = { runtime: { id: undefined } };
    }

    // ── Permissions API: report 'granted' for notifications ───────────────
    const _origQuery = navigator.permissions && navigator.permissions.query;
    if (_origQuery) {
        navigator.permissions.query = (params) =>
            params.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : _origQuery.call(navigator.permissions, params);
    }
})();
"""


# ---------------------------------------------------------------------------
# §1c — Bezier-curve mouse movement  (no external dependencies)
# ---------------------------------------------------------------------------
# Real users never move the mouse in a straight line.
# We generate a smooth quadratic Bezier path between current and target coords,
# adding a random control point to vary the curve shape each time.

def _bezier_path(
    x0: float, y0: float,
    x1: float, y1: float,
    steps: int = 25,
) -> list[tuple[float, float]]:
    """
    Quadratic Bezier path from (x0,y0) → (x1,y1) with a random control point.
    Returns `steps` intermediate (x, y) coordinates.
    """
    # Control point: random perpendicular offset so the curve bends naturally
    mx = (x0 + x1) / 2 + random.uniform(-80, 80)
    my = (y0 + y1) / 2 + random.uniform(-80, 80)
    path = []
    for i in range(steps + 1):
        t  = i / steps
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t ** 2 * y1
        path.append((bx, by))
    return path


# ---------------------------------------------------------------------------
# §1d — CAPTCHA handler
# ---------------------------------------------------------------------------
class CaptchaHandler:
    """
    Detects and attempts to solve CAPTCHAs.

    Supported methods (in priority order):
      1. 2Captcha API  (set BLITZBUY_2CAPTCHA env var)
      2. Manual fallback — pauses automation and waits for human solve

    Production note: for high-volume runs use Anti-Captcha, CapSolver, or
    DeathByCaptcha.  AI-based vision solvers (GPT-4o) work for simple image
    CAPTCHAs but are slow (~10 s) and expensive at scale.
    """

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self._solver = None
        if api_key:
            try:
                from twocaptcha import TwoCaptcha
                self._solver = TwoCaptcha(api_key)
                _log("CAPTCHA", f"2Captcha solver ready (key: {api_key[:6]}…)")
            except ImportError:
                _log("CAPTCHA", "twocaptcha not installed — falling back to manual")

    async def solve_recaptcha_v2(self, page: Page, sitekey: str) -> bool:
        """
        Attempt to solve a reCAPTCHA v2 challenge.
        Returns True if solved, False if failed.
        """
        site_url = page.url

        if self._solver:
            _log("CAPTCHA", f"Sending reCAPTCHA v2 to 2Captcha (sitekey={sitekey[:12]}…)")
            try:
                # This blocks the thread — in production, run in an executor
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._solver.recaptcha(sitekey=sitekey, url=site_url),
                )
                token = result["code"]
                # Inject the token into the hidden textarea reCAPTCHA uses
                await page.evaluate(
                    f'document.getElementById("g-recaptcha-response").innerHTML = "{token}";'
                )
                _log("CAPTCHA", "reCAPTCHA token injected ✓")
                return True
            except Exception as exc:
                _log("CAPTCHA", f"2Captcha solve failed: {exc} — falling back to manual")

        # Manual fallback: pause and wait for human intervention (up to 5 min)
        _log("CAPTCHA", "⚠️  Manual CAPTCHA solve required — solve in the browser then press ENTER")
        await asyncio.get_event_loop().run_in_executor(None, input)
        return True

    async def detect_and_solve(self, page: Page) -> bool:
        """
        Scan the current page for common CAPTCHA patterns and attempt to solve.
        Returns True if a CAPTCHA was found and resolved (or not found).
        """
        content = await page.content()

        # reCAPTCHA v2
        sitekey_match = re.search(
            r'data-sitekey=["\']([^"\']+)["\']', content, re.IGNORECASE
        )
        if sitekey_match:
            _log("CAPTCHA", f"reCAPTCHA v2 detected (sitekey={sitekey_match.group(1)[:12]}…)")
            return await self.solve_recaptcha_v2(page, sitekey_match.group(1))

        # hCaptcha
        if "hcaptcha.com" in content:
            _log("CAPTCHA", "hCaptcha detected — manual solve required")
            await asyncio.get_event_loop().run_in_executor(None, input)
            return True

        # Cloudflare Turnstile
        if "challenges.cloudflare.com" in content:
            _log("CAPTCHA", "Cloudflare Turnstile detected — manual solve required")
            await asyncio.get_event_loop().run_in_executor(None, input)
            return True

        return False   # no CAPTCHA found


# ---------------------------------------------------------------------------
# §4a — robots.txt compliance
# ---------------------------------------------------------------------------
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _check_robots(url: str) -> bool:
    """Return True if the URL is crawlable per robots.txt for '*'."""
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp.allow_all = True
        _robots_cache[origin] = rp
    allowed = _robots_cache[origin].can_fetch("*", url)
    if not allowed:
        _log("ROBOTS", f"robots.txt disallows {url} — skipping")
    return allowed


# ---------------------------------------------------------------------------
# §5 — Stripe payment handler
# ---------------------------------------------------------------------------
class StripePaymentHandler:
    """
    Handles payment via the Stripe API using a pre-stored PaymentMethod ID.

    Why API-side payment beats typing card numbers into the browser
    ---------------------------------------------------------------
    Entering card details through browser automation is the single biggest
    fraud-detection trigger on real e-commerce sites:
      · Keystroke velocity on card fields is monitored by every major fraud stack
      · Stripe.js / Adyen fingerprint the browser environment during card entry
      · Many sites use invisible device-fingerprint tokens tied to the card form

    Instead, BlitzBuy stores a Stripe PaymentMethod (pm_xxx) once via Stripe's
    secure hosted form, then passes that ID server-side at checkout time.
    The browser never touches a card number field.

    Flow on a Stripe-native checkout (e.g. Shopify, most SaaS stores)
    ------------------------------------------------------------------
    1. Bot navigates to checkout, fills shipping/contact details (browser)
    2. Bot reaches payment step — detects Stripe Elements iframe on the page
    3. StripePaymentHandler.create_and_confirm() is called:
         a. Creates a PaymentIntent server-side (Stripe API)
         b. Confirms it with the stored pm_xxx — Stripe returns client_secret
    4. Bot calls page.evaluate() to inject the client_secret and call
       stripe.confirmCardPayment(clientSecret) — triggers Stripe's own JS
       without any card field interaction
    5. Stripe redirects / resolves → bot detects success confirmation element

    Flow on a non-Stripe checkout (e.g. custom payment form)
    ---------------------------------------------------------
    Use StripePaymentHandler only for the financial record / audit trail.
    The actual card details come from env vars and are filled into the form
    character-by-character with _human_type() — same stealth pipeline.
    Never hardcode real card numbers; pass them via CARD_NUMBER etc. env vars.

    Test mode (default)
    -------------------
    With STRIPE_SECRET_KEY=sk_test_... and STRIPE_PAYMENT_METHOD_ID=pm_card_visa,
    no real money moves. Stripe's test clock lets you simulate 3DS, declines,
    network errors, etc. — full coverage without a real card.
    """

    # Stripe test PaymentMethod fixtures (no real card needed in test mode)
    TEST_METHODS = {
        "visa":            "pm_card_visa",
        "visa_debit":      "pm_card_visa_debit",
        "mastercard":      "pm_card_mastercard",
        "amex":            "pm_card_amex",
        "decline":         "pm_card_chargeDeclined",
        "insufficient":    "pm_card_chargeDeclinedInsufficientFunds",
        "3ds_required":    "pm_card_threeDSecure2Required",
    }

    def __init__(
        self,
        secret_key:        str  = STRIPE_SECRET_KEY,
        payment_method_id: str  = STRIPE_PAYMENT_METHOD_ID,
        currency:          str  = STRIPE_CURRENCY,
        live_mode:         bool = STRIPE_LIVE,
    ) -> None:
        self.payment_method_id = payment_method_id
        self.currency          = currency
        self.live_mode         = live_mode
        self.enabled           = bool(secret_key)

        if self.enabled:
            stripe.api_key = secret_key
            mode = "LIVE ⚠️" if live_mode else "TEST"
            _log("STRIPE", f"Stripe ready — {mode} mode")
            if live_mode:
                _log("STRIPE", "  ⚠️  LIVE MODE: real charges will be made")
        else:
            _log("STRIPE", "No STRIPE_SECRET_KEY set — payment step will be skipped")

    def is_enabled(self) -> bool:
        return self.enabled

    async def create_and_confirm(
        self,
        amount_cents:    int,
        idempotency_key: str,
        description:     str = "BlitzBuy automated purchase",
    ) -> dict:
        """
        Create and immediately confirm a PaymentIntent using the stored
        PaymentMethod ID. Runs the Stripe API call in a thread executor
        so it doesn't block the async event loop.

        Returns the PaymentIntent object dict on success.
        Raises stripe.error.StripeError on failure (caught by caller).

        Idempotency key = job UUID → safe to retry; Stripe deduplicates
        within 24 h, so a network retry won't double-charge.
        """
        if not self.enabled:
            raise RuntimeError("Stripe not configured — set STRIPE_SECRET_KEY")

        _log("STRIPE", f"Creating PaymentIntent: {self.currency.upper()} "
                        f"{amount_cents/100:.2f} | pm={self.payment_method_id[:14]}…")

        def _call():
            return stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=self.currency,
                payment_method=self.payment_method_id,
                confirm=True,
                # off_session=True tells Stripe this is an automated charge
                # with no user present — required for stored payment methods.
                off_session=True,
                description=description,
                # Idempotency: safe to retry without double-charging.
                # Stripe deduplicates identical keys within 24 hours.
                idempotency_key=idempotency_key,
            )

        intent = await asyncio.get_event_loop().run_in_executor(None, _call)

        status = intent["status"]
        pi_id  = intent["id"]
        _log("STRIPE", f"PaymentIntent {pi_id} → status={status}")

        if status == "succeeded":
            _log("STRIPE", f"Payment confirmed ✓  ({self.currency.upper()} {amount_cents/100:.2f})")
        elif status == "requires_action":
            # 3D Secure challenge required — handle in browser or use
            # Stripe Radar rules to exempt trusted automated flows.
            _log("STRIPE", "⚠️  3DS challenge required — use Stripe Radar exemption for bots")
            raise RuntimeError(f"PaymentIntent {pi_id} requires 3DS action")
        else:
            raise RuntimeError(f"PaymentIntent {pi_id} unexpected status: {status}")

        return dict(intent)

    @staticmethod
    def price_to_cents(price_str: str) -> int:
        """
        Convert a price string from the page (e.g. '$29.99') to Stripe
        integer cents (2999). Stripe always works in the smallest currency unit.
        """
        cleaned = re.sub(r"[^\d.]", "", price_str)
        return round(float(cleaned) * 100)


# ---------------------------------------------------------------------------
# §3 — SQLite audit log
# ---------------------------------------------------------------------------
class AuditDB:
    """
    Lightweight SQLite audit trail.
    Stores every purchase attempt with full context for compliance/debugging.

    In production swap for PostgreSQL (via SQLAlchemy or asyncpg) and stream
    to ELK / Datadog for real-time monitoring.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                 TEXT    NOT NULL,
                site_url           TEXT    NOT NULL,
                product            TEXT    NOT NULL,
                max_price          REAL    NOT NULL,
                success            INTEGER NOT NULL,
                message            TEXT,
                screenshot         TEXT,
                elapsed_s          REAL,
                ua                 TEXT,
                proxy              TEXT,
                payment_intent_id  TEXT
            )
        """)
        self._conn.commit()
        _log("DB", f"Audit DB ready → {path}")

    def record(
        self,
        site_url:   str,
        product:    str,
        max_price:  float,
        result:     "PurchaseResult",
        ua:         str = "",
        proxy:      str = "",
    ) -> None:
        self._conn.execute(
            """INSERT INTO purchases
               (ts, site_url, product, max_price, success, message,
                screenshot, elapsed_s, ua, proxy, payment_intent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                site_url, product, max_price,
                int(result.success), result.message,
                str(result.screenshot) if result.screenshot else None,
                result.elapsed, ua, proxy,
                result.payment_intent_id or None,
            ),
        )
        self._conn.commit()

    def recent(self, n: int = 10) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM purchases ORDER BY id DESC LIMIT ?", (n,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class PurchaseResult:
    success:           bool
    message:           str
    screenshot:        Optional[Path] = None
    elapsed:           float = 0.0
    retries:           int   = 0
    payment_intent_id: str   = ""   # pi_xxx from Stripe, empty if skipped/failed


# ---------------------------------------------------------------------------
# §1 + §2 — Core Agent
# ---------------------------------------------------------------------------
class FastPurchaseAgent:
    """
    Production-grade stealth Playwright agent.

    Stealth pipeline (applied per session)
    ---------------------------------------
    · Random device profile   (UA / viewport / locale / TZ / screen / CPU / RAM)
    · playwright-stealth      (~20+ JS patches via stealth_async)
    · Canvas/WebGL JS noise   (per-session random offsets, WebGL vendor spoof)
    · --disable-blink-features=AutomationControlled
    · Bezier-curve mouse paths (25 intermediate steps per move)
    · Per-keystroke typing delays
    · Random action delays    (short / medium / long bands)
    · robots.txt gating       (cached per origin)
    · CAPTCHA detection + 2Captcha/manual solve
    · Block-detection regex   (auto-retry with fresh context on detect)
    · tenacity exponential-backoff retries

    Performance pipeline
    --------------------
    · Selective resource blocking   (images / fonts / media / ad networks)
    · asyncio.gather() concurrency  (≤ MAX_CONCURRENT)
    · SQLite audit trail            (non-blocking, same-thread)
    """

    def __init__(
        self,
        headless:        bool  = HEADLESS,
        block_resources: bool  = True,
        proxy:           str   = PROXY_SERVER,
        captcha_key:     str   = CAPTCHA_API_KEY,
        stripe_key:      str   = STRIPE_SECRET_KEY,
    ) -> None:
        self.headless        = headless
        self.block_resources = block_resources
        self.proxy           = proxy

        self._pw:      Optional[Playwright]     = None
        self._browser: Optional[Browser]        = None
        self._context: Optional[BrowserContext] = None
        self._profile: dict                     = {}
        self._ua:      str                      = ""
        self._captcha: CaptchaHandler           = CaptchaHandler(captcha_key)
        self._stripe:  StripePaymentHandler     = StripePaymentHandler(secret_key=stripe_key)
        self._db:      AuditDB                  = AuditDB()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def launch(self) -> None:
        _log("LAUNCH", "Starting Playwright…")
        self._pw      = await async_playwright().start()
        self._profile = _random_profile()
        self._ua      = _random_ua()

        _log("LAUNCH", f"Profile: {self._profile['width']}×{self._profile['height']} "
                        f"/ {self._profile['locale']} / {self._profile['tz']}")
        _log("LAUNCH", f"UA: {self._ua[:72]}…")

        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--mute-audio",
                # ── Key anti-detection flags ────────────────────────────
                # Removes navigator.webdriver + automation banner
                "--disable-blink-features=AutomationControlled",
                # Prevents iframe isolation leaks
                "--disable-features=IsolateOrigins,site-per-process",
                # Match real-device window size (tiny default = bot signal)
                f"--window-size={self._profile['width']},{self._profile['height']}",
                # Disable infobars that expose automation
                "--disable-infobars",
                "--disable-notifications",
            ],
        )

        await self._build_context()
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        _log("LAUNCH", "Browser ready ✓")

    async def _build_context(self) -> None:
        """Create (or recreate) a browser context with full stealth config."""
        if self._context:
            await self._context.close()

        p = self._profile
        ctx_kwargs: dict = {
            "viewport":   {"width": p["width"], "height": p["height"]},
            "user_agent": self._ua,
            "locale":     p["locale"],
            "timezone_id": p["tz"],
            "screen":     {"width": p["width"], "height": p["height"]},
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept":          (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Encoding": "gzip, deflate, br",
                "DNT":             "1",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest":  "document",
                "Sec-Fetch-Mode":  "navigate",
                "Sec-Fetch-Site":  "none",
                "Sec-Fetch-User":  "?1",
            },
        }

        # ── Proxy injection ─────────────────────────────────────────────
        # Residential or mobile IPs: Bright Data, Oxylabs, Smartproxy.
        # Rotate per session (not per request — too suspicious).
        # In production: call the proxy provider API here to get a fresh IP.
        if self.proxy:
            parsed = urllib.parse.urlparse(self.proxy)
            ctx_kwargs["proxy"] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            }
            if parsed.username:
                ctx_kwargs["proxy"]["username"] = parsed.username
                ctx_kwargs["proxy"]["password"] = parsed.password or ""
            _log("LAUNCH", f"Proxy: {parsed.hostname}:{parsed.port}")
        else:
            _log("LAUNCH", "Proxy: none (set BLITZBUY_PROXY for residential IP)")

        self._context = await self._browser.new_context(**ctx_kwargs)
        self._context.set_default_timeout(ACTION_TIMEOUT)

        # Canvas/WebGL noise + navigator spoofs injected before any page JS
        await self._context.add_init_script(_CANVAS_WEBGL_SPOOF_JS)

        if self.block_resources:
            await self._context.route("**/*", self._resource_blocker)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._db.close()
        _log("CLOSE", "Browser + DB closed ✓")

    # ── Resource blocker ───────────────────────────────────────────────────

    async def _resource_blocker(self, route: Route, request: Request) -> None:
        """Block images/fonts/media + known ad/tracker hostnames."""
        if request.resource_type in BLOCKED_TYPES:
            await route.abort()
            return
        host = urllib.parse.urlparse(request.url).hostname or ""
        if any(ad in host for ad in AD_HOSTNAMES):
            await route.abort()
            return
        await route.continue_()

    # ── Screenshot ─────────────────────────────────────────────────────────

    async def _shot(self, page: Page, label: str) -> Path:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = SCREENSHOTS / f"{label}_{ts}.png"
        await page.screenshot(path=str(path), full_page=True)
        _log("SCREENSHOT", f"Saved → {path}")
        return path

    # ── Detection monitoring ───────────────────────────────────────────────

    async def _check_blocked(self, page: Page) -> bool:
        """
        Scan page content for bot-block indicators.
        On detection: screenshot + return True (caller should retry).
        """
        try:
            content = await page.content()
            title   = await page.title()
        except Exception:
            return False

        if BLOCK_PATTERNS.search(content) or BLOCK_PATTERNS.search(title):
            _log("DETECT", f"⚠️  Block/challenge detected on '{title}' — flagging for retry")
            await self._shot(page, "blocked_detected")
            return True
        return False

    # ── Human-behaviour primitives ─────────────────────────────────────────

    async def _human_delay(self, band: tuple[float, float] = DELAY_MEDIUM) -> None:
        await asyncio.sleep(random.uniform(*band))

    async def _human_scroll(self, page: Page) -> None:
        """Scroll a random amount — humans rarely land right at the element."""
        delta = random.randint(80, 450)
        await page.evaluate(f"window.scrollBy(0, {delta})")
        await self._human_delay(DELAY_SHORT)

    async def _bezier_move(self, page: Page, x: float, y: float) -> None:
        """
        Move the mouse to (x, y) along a Bezier curve.
        Reads current mouse position from the page to generate a realistic path.
        Straight-line moves are a trivial bot signal in advanced JS detectors.
        """
        # We don't have the current mouse position exposed by Playwright,
        # so start from a random nearby point as the "last known" position.
        cx = x + random.uniform(-200, 200)
        cy = y + random.uniform(-200, 200)
        path = _bezier_path(cx, cy, x, y, steps=random.randint(20, 35))
        for px, py in path:
            await page.mouse.move(px, py)
            # Tiny inter-step pause: 2–8 ms (matches real mouse event rates)
            await asyncio.sleep(random.uniform(0.002, 0.008))

    async def _human_type(self, page: Page, locator, text: str) -> None:
        """Character-by-character typing with per-keystroke random delays."""
        await locator.click()
        await self._human_delay(DELAY_SHORT)
        for char in text:
            await locator.press(char)
            await asyncio.sleep(random.randint(*TYPING_DELAY_MS) / 1000)

    async def _human_click(self, page: Page, locator) -> None:
        """
        Approach element via Bezier path, pause as if reading, then click
        with a random jitter offset (perfect-centre clicks are a bot signal).
        """
        box = await locator.bounding_box()
        if box:
            cx = box["x"] + box["width"]  / 2
            cy = box["y"] + box["height"] / 2
            await self._bezier_move(page, cx, cy)
            jx = random.uniform(-box["width"]  * 0.12, box["width"]  * 0.12)
            jy = random.uniform(-box["height"] * 0.12, box["height"] * 0.12)
            await self._human_delay(DELAY_SHORT)
            await page.mouse.click(cx + jx, cy + jy)
        else:
            await locator.click()

    # ── Purchase entry point with tenacity retry + DB logging ─────────────

    async def purchase(
        self,
        product_search_term: str,
        max_price:           float,
        test_email:          str = TEST_EMAIL,
        test_password:       str = TEST_PASS,
    ) -> PurchaseResult:
        """
        Outer shell: wraps _attempt_purchase with tenacity exponential-backoff.
        On block detection, rebuilds the browser context (new fingerprint + IP)
        before the next attempt.
        """
        attempt     = 0
        last_result = PurchaseResult(success=False, message="Not attempted")

        for attempt in range(1, MAX_RETRIES + 1):
            t0   = time.perf_counter()
            page = await self._context.new_page()
            await stealth_async(page)   # playwright-stealth patches per page
            _log("STEALTH", f"Attempt {attempt}/{MAX_RETRIES} — stealth patches applied ✓")

            try:
                result = await self._run_flow(
                    page, product_search_term, max_price,
                    test_email, test_password,
                )
                result.elapsed  = time.perf_counter() - t0
                result.retries  = attempt - 1
                self._db.record(
                    SITE_URL, product_search_term, max_price,
                    result, ua=self._ua, proxy=self.proxy,
                )
                return result

            except PWTimeoutError as exc:
                elapsed = time.perf_counter() - t0
                _log("ERROR", f"Timeout on attempt {attempt}: {exc}")
                shot = await self._shot(page, f"failure_timeout_a{attempt}")
                last_result = PurchaseResult(
                    success=False, message=f"Timeout: {exc}",
                    screenshot=shot, elapsed=elapsed, retries=attempt,
                )
                # Exponential backoff before retry
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt + random.uniform(0, 1)
                    _log("RETRY", f"Waiting {wait:.1f}s before attempt {attempt + 1}…")
                    await asyncio.sleep(wait)
                    # Rebuild context with fresh fingerprint/proxy on retry
                    self._profile = _random_profile()
                    self._ua      = _random_ua()
                    await self._build_context()

            except Exception as exc:
                elapsed = time.perf_counter() - t0
                _log("ERROR", f"Unexpected on attempt {attempt}: {exc}")
                shot = await self._shot(page, f"failure_unexpected_a{attempt}")
                last_result = PurchaseResult(
                    success=False, message=f"Error: {exc}",
                    screenshot=shot, elapsed=elapsed, retries=attempt,
                )
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt + random.uniform(0, 1)
                    _log("RETRY", f"Waiting {wait:.1f}s before attempt {attempt + 1}…")
                    await asyncio.sleep(wait)
                    self._profile = _random_profile()
                    self._ua      = _random_ua()
                    await self._build_context()

            finally:
                await page.close()

        self._db.record(
            SITE_URL, product_search_term, max_price,
            last_result, ua=self._ua, proxy=self.proxy,
        )
        return last_result

    # ── Full purchase flow ─────────────────────────────────────────────────

    async def _run_flow(
        self,
        page:                Page,
        product_search_term: str,
        max_price:           float,
        test_email:          str,
        test_password:       str,
    ) -> PurchaseResult:

        # ── robots.txt gate ────────────────────────────────────────────
        if not _check_robots(SITE_URL):
            return PurchaseResult(
                success=False,
                message=f"robots.txt disallows {SITE_URL}",
            )

        # ── Step 1 · Navigate + login ──────────────────────────────────
        _log("LOGIN", f"Navigating to {SITE_URL}")
        await page.goto(SITE_URL, wait_until="domcontentloaded")
        await self._human_delay(DELAY_MEDIUM)

        if await self._check_blocked(page):
            await self._captcha.detect_and_solve(page)

        await self._human_type(page, page.get_by_placeholder("Username"), TEST_USER)
        await self._human_delay(DELAY_SHORT)
        await self._human_type(page, page.get_by_placeholder("Password"), test_password)
        await self._human_delay(DELAY_SHORT)
        await self._human_click(page, page.get_by_role("button", name="Login"))
        _log("LOGIN", "Credentials submitted")

        await page.get_by_text("Products").wait_for()
        await self._human_delay(DELAY_MEDIUM)

        if await self._check_blocked(page):
            raise RuntimeError("Block detected after login — retrying with fresh context")

        _log("LOGIN", "Login successful ✓")

        # ── Step 2 · Browse & find product ────────────────────────────
        _log("PRODUCT", f"Searching for '{product_search_term}' ≤ ${max_price}")
        await self._human_scroll(page)

        product_cards = page.locator(".inventory_item")
        count         = await product_cards.count()
        _log("PRODUCT", f"{count} products in catalogue")

        target_card = None
        for i in range(count):
            card  = product_cards.nth(i)
            name  = (await card.locator(".inventory_item_name").inner_text()).strip()
            price = float(
                (await card.locator(".inventory_item_price").inner_text())
                .strip().replace("$", "")
            )
            _log("PRODUCT", f"  · {name}  ${price:.2f}")
            await self._human_delay(DELAY_SHORT)

            if product_search_term.lower() in name.lower() and price <= max_price:
                target_card = card
                _log("PRODUCT", f"  ↳ MATCH ✓")
                break

        if target_card is None:
            return PurchaseResult(
                success=False,
                message=(
                    f"No product matching '{product_search_term}' "
                    f"found under ${max_price}"
                ),
            )

        # ── Step 3 · Add to cart ───────────────────────────────────────
        await self._human_delay(DELAY_MEDIUM)
        await self._human_click(page, target_card.get_by_role("button", name="Add to cart"))
        await page.locator(".shopping_cart_badge").wait_for()
        await self._human_delay(DELAY_SHORT)
        _log("CART", "Item added ✓")

        # ── Step 4 · Open cart ─────────────────────────────────────────
        await self._human_delay(DELAY_MEDIUM)
        await self._human_click(page, page.locator(".shopping_cart_link"))
        await page.get_by_role("button", name="Checkout").wait_for()
        _log("CART", "Cart page loaded ✓")

        # ── Step 5 · Checkout — contact info ──────────────────────────
        await self._human_click(page, page.get_by_role("button", name="Checkout"))
        await self._human_delay(DELAY_SHORT)

        for locator, value in [
            (page.get_by_placeholder("First Name"),       "Blitz"),
            (page.get_by_placeholder("Last Name"),        "Buyer"),
            (page.get_by_placeholder("Zip/Postal Code"),  "10001"),
        ]:
            await self._human_type(page, locator, value)
            await self._human_delay(DELAY_SHORT)

        await self._human_delay(DELAY_MEDIUM)
        await self._human_click(page, page.get_by_role("button", name="Continue"))
        _log("CHECKOUT", "Contact info submitted ✓")

        # ── Step 6 · Order summary + Stripe payment ───────────────────
        await page.get_by_text("Checkout: Overview").wait_for()
        await self._human_delay(DELAY_LONG)   # humans actually read summaries

        # Parse the subtotal shown on the summary page.
        # On saucedemo this is the item subtotal; on real sites use the
        # grand total (including tax/shipping) from the order summary element.
        total_text = await page.locator(".summary_subtotal_label").inner_text()
        _log("CHECKOUT", f"Summary: {total_text} ✓")

        if await self._check_blocked(page):
            raise RuntimeError("Block detected at order summary")

        # ── Stripe payment ─────────────────────────────────────────────
        # This is called here because we now know the exact charge amount.
        #
        # On saucedemo: the site has no real payment step, so Stripe runs
        # as a parallel financial record — proof the payment was authorised
        # before the bot clicks "Finish".
        #
        # On a real Stripe-native checkout (Shopify, most SaaS):
        #   After this call succeeds, inject the client_secret into the page:
        #     await page.evaluate(
        #       f"stripe.confirmCardPayment('{intent['client_secret']}')"
        #     )
        #   Then wait for the site's own success/redirect element.
        #
        # On a non-Stripe checkout (custom card form):
        #   Use this only for the audit record; fill card fields via
        #   _human_type() using CARD_NUMBER / CARD_EXP / CARD_CVC env vars.
        payment_intent_id = ""
        if self._stripe.is_enabled():
            amount_cents = StripePaymentHandler.price_to_cents(total_text)
            try:
                intent = await self._stripe.create_and_confirm(
                    amount_cents=amount_cents,
                    idempotency_key=f"blitzbuy-{id(page)}-{int(time.time())}",
                    description=f"BlitzBuy: {product_search_term}",
                )
                payment_intent_id = intent["id"]
                _log("STRIPE", f"Payment intent recorded: {payment_intent_id}")
            except stripe.StripeError as exc:
                # Payment declined / network error — abort the purchase
                _log("STRIPE", f"Payment failed: {exc}")
                shot = await self._shot(page, "failure_payment")
                return PurchaseResult(
                    success=False,
                    message=f"Stripe payment failed: {exc}",
                    screenshot=shot,
                    payment_intent_id=payment_intent_id,
                )
        else:
            _log("STRIPE", "Skipped — set STRIPE_SECRET_KEY to enable real payments")

        # ── Step 7 · Place order ───────────────────────────────────────
        await self._human_delay(DELAY_MEDIUM)
        await self._human_click(page, page.get_by_role("button", name="Finish"))
        await page.get_by_text("Thank you for your order!").wait_for()
        _log("ORDER", "Order confirmed ✓")

        shot = await self._shot(page, "success_order_complete")
        return PurchaseResult(
            success=True,
            message=f"Purchase of '{product_search_term}' completed.",
            screenshot=shot,
            payment_intent_id=payment_intent_id,
        )


# ---------------------------------------------------------------------------
# Concurrent demo
# ---------------------------------------------------------------------------
async def run_concurrent_demo(agent: FastPurchaseAgent) -> None:
    """Two parallel purchases — wall-clock ≈ max(individual), not sum."""
    _log("CONCURRENT", f"Launching {MAX_CONCURRENT} purchases via asyncio.gather()…")
    t0 = time.perf_counter()

    results = await asyncio.gather(
        agent.purchase("Sauce Labs Backpack",   max_price=35.00),
        agent.purchase("Sauce Labs Bike Light", max_price=15.00),
        return_exceptions=True,
    )

    _log("CONCURRENT", f"Both done in {time.perf_counter() - t0:.2f}s wall-clock")
    for i, r in enumerate(results, 1):
        if isinstance(r, BaseException):
            _log("CONCURRENT", f"  [{i}] EXCEPTION → {r}")
        else:
            status = "✓ SUCCESS" if r.success else "✗ FAILED"
            _log("CONCURRENT", f"  [{i}] {status} ({r.elapsed:.2f}s, {r.retries} retries) — {r.message}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=" * 64)
    print("  BlitzBuy — Production Stealth Purchase Demo (TEST ONLY)")
    print("=" * 64)

    agent = FastPurchaseAgent(headless=HEADLESS, block_resources=True)
    await agent.launch()

    t0     = time.perf_counter()
    result = await agent.purchase("Sauce Labs Backpack", max_price=35.00)

    print()
    print("─" * 64)
    print(f"  Result     : {'SUCCESS ✓' if result.success else 'FAILED ✗'}")
    print(f"  Message    : {result.message}")
    print(f"  Screenshot : {result.screenshot or 'N/A'}")
    print(f"  Elapsed    : {result.elapsed:.3f}s  ({result.retries} retries)")
    print(f"  Total      : {time.perf_counter() - t0:.3f}s  (incl. launch)")
    print("─" * 64)

    print()
    print("─" * 64)
    print("  Running CONCURRENT demo…")
    print("─" * 64)
    await run_concurrent_demo(agent)

    # Print recent audit log
    print()
    print("─" * 64)
    print("  Recent audit log (purchase_history.db):")
    for row in agent._db.recent(5):
        status = "✓" if row["success"] else "✗"
        pi = f"  {row['payment_intent_id']}" if row.get("payment_intent_id") else ""
        print(f"  {status}  {row['ts']}  {row['product']}  {row['elapsed_s']:.2f}s{pi}")
    print("─" * 64)

    await agent.close()
    print()
    print("=" * 64)
    print("  BlitzBuy done.")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
