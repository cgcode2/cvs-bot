"""
CVS Coupon & Price Optimizer Discord Bot
==========================================

A discord.py 2.3.2 bot that helps optimize CVS shopping trips by finding the
best combination of items + coupons/deals using combinatorics (this is where
you'll paste your exact pricing/coupon-matching logic).

A tiny background Flask server is included so the bot can be pinged by an
uptime monitor (e.g. UptimeRobot) to help keep it running 24/7.

Setup:
- Requires the DISCORD_BOT_TOKEN secret to be set (see Replit Secrets).
- Uses discord.py==2.3.2 and Flask (already installed).
"""

import os
import threading
import logging
from itertools import combinations

import discord
from discord.ext import commands
from flask import Flask

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cvs-bot")

# ---------------------------------------------------------------------------
# Keep-alive Flask server
# ---------------------------------------------------------------------------
# Runs in a background thread on the PORT Replit assigns. An external uptime
# monitor can ping "/" to keep the container awake.

keep_alive_app = Flask(__name__)


@keep_alive_app.route("/")
def home():
    return "CVS Optimizer Bot is alive!"


@keep_alive_app.route("/health")
def health():
    return {"status": "ok"}


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    # use_reloader must be False since this runs in a background thread
    keep_alive_app.run(host="0.0.0.0", port=port, use_reloader=False)


def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id if bot.user else "?")


# ---------------------------------------------------------------------------
# CVS combinatorics logic
# ---------------------------------------------------------------------------
# This is a placeholder implementation. Paste your exact pricing/coupon
# optimization logic here (or replace this section entirely). The general
# shape: given a list of items (with price + eligible coupons/deals), find
# the combination that minimizes out-of-pocket cost / maximizes savings,
# typically by brute-forcing subsets of applicable coupons since CVS deals
# are usually small in number per trip.


def optimize_cart(items: list[dict], coupons: list[dict]) -> dict:
    """
    Placeholder optimizer.

    items:   [{"name": str, "price": float}, ...]
    coupons: [{"name": str, "discount": float, "applies_to": list[str] | None}, ...]

    Returns the best combination of coupons (by total savings) found by
    brute-forcing all subsets of coupons -- replace with your real logic.
    """
    subtotal = sum(i["price"] for i in items)

    best = {"coupons": [], "savings": 0.0, "total": subtotal}

    for r in range(len(coupons) + 1):
        for combo in combinations(coupons, r):
            savings = sum(c["discount"] for c in combo)
            savings = min(savings, subtotal)  # can't discount below $0
            if savings > best["savings"]:
                best = {
                    "coupons": [c["name"] for c in combo],
                    "savings": savings,
                    "total": subtotal - savings,
                }

    return best


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong! Bot is running.")


@bot.command(name="optimize")
async def optimize(ctx: commands.Context):
    """
    Example command wiring for the optimizer. Replace the sample items/coupons
    below with real parsing of user input once you paste your logic.
    """
    sample_items = [
        {"name": "Toothpaste", "price": 4.99},
        {"name": "Shampoo", "price": 6.49},
    ]
    sample_coupons = [
        {"name": "$2 off Toothpaste", "discount": 2.0, "applies_to": ["Toothpaste"]},
        {"name": "$1 off Shampoo", "discount": 1.0, "applies_to": ["Shampoo"]},
    ]

    result = optimize_cart(sample_items, sample_coupons)

    lines = [
        "**CVS Trip Optimizer (sample data)**",
        f"Best coupon combo: {', '.join(result['coupons']) or 'none'}",
        f"Total savings: ${result['savings']:.2f}",
        f"Final total: ${result['total']:.2f}",
    ]
    await ctx.send("\n".join(lines))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. Add it via Replit Secrets before running the bot."
        )

    keep_alive()
    bot.run(token)


if __name__ == "__main__":
    main()
