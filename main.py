import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal, Optional, Dict, Any, List, Tuple
import asyncio
import itertools
import copy
import re
from flask import Flask, jsonify
from threading import Thread
import os
import sys
import json
import time
import random
from datetime import datetime, timedelta, timezone

# Configure UTF-8 encoding for Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def load_env_file(filepath: str = ".env") -> None:
    """Lightweight .env file loader into os.environ without third-party dependencies."""
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except Exception as e:
            print(f"⚠️ Notice: Error reading .env file: {e}", file=sys.stderr)

load_env_file()

# 1. BACKGROUND WEB SERVER
app = Flask(__name__)

@app.route('/')
def home():
    return "AIO Bot is running 24/7!"

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "AIO Bot", "timestamp": datetime.now(timezone.utc).isoformat()})

def run_server():
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

# 2. DISCORD BOT ENGINE
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Multi-user session storage: user_id -> {"items": [], "coupons": [], "cart_message": None}
user_sessions: Dict[int, Dict[str, Any]] = {}
user_test_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int, test: bool = False) -> Dict[str, Any]:
    target_dict = user_test_sessions if test else user_sessions
    if user_id not in target_dict:
        target_dict[user_id] = {"items": [], "coupons": [], "cart_message": None}
    return target_dict[user_id]

def reset_session(user_id: int, test: bool = False) -> None:
    target_dict = user_test_sessions if test else user_sessions
    target_dict[user_id] = {"items": [], "coupons": [], "cart_message": None}

SESSION_CHANNELS_FILE = "session_channels.json"
WARNINGS_FILE = "warnings_data.json"
STAFF_ROLE_NAME = "Staff"

def load_json_file(filename: str, default: Any) -> Any:
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return copy.deepcopy(default)
    return copy.deepcopy(default)

def save_json_file(filename: str, data: Any) -> None:
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save {filename}: {e}", file=sys.stderr)

session_channels: Dict[str, int] = load_json_file(SESSION_CHANNELS_FILE, {})
warnings_db: Dict[str, List[Dict[str, Any]]] = load_json_file(WARNINGS_FILE, {})

def save_session_channels(data: Dict[str, int]) -> None:
    save_json_file(SESSION_CHANNELS_FILE, data)

def save_warnings(data: Dict[str, List[Dict[str, Any]]]) -> None:
    save_json_file(WARNINGS_FILE, data)

# 3. COLOR CONSTANTS
COLOR_PRIMARY = 0xcc0000   # AIO Bot red
COLOR_SUCCESS = 0x2ecc71   # Green  — positive results
COLOR_ERROR   = 0xe74c3c   # Red    — errors / warnings
COLOR_INFO    = 0x3498db   # Blue   — neutral information
COLOR_WARN    = 0xf1c40f   # Yellow — caution
COLOR_TEST    = 0x9b59b6   # Purple — test/sandbox mode

# Extended named-color palette for the /embed command
COLOR_NAMES = {
    "red": COLOR_ERROR, "dark red": 0x992d22, "orange": 0xe67e22,
    "yellow": COLOR_WARN, "gold": COLOR_WARN, "green": COLOR_SUCCESS,
    "dark green": 0x1f8b4c, "teal": 0x1abc9c, "cyan": 0x00ffff,
    "blue": COLOR_INFO, "dark blue": 0x206694, "navy": 0x2c3e50,
    "purple": COLOR_TEST, "dark purple": 0x71368a, "magenta": 0xe91e63,
    "pink": 0xff69b4, "brown": 0x795548, "black": 0x23272a,
    "white": 0xffffff, "gray": 0x95a5a6, "grey": 0x95a5a6,
    "dark gray": 0x2c2f33, "dark grey": 0x2c2f33, "lime": 0x32cd32,
    "blurple": 0x5865f2, "fuchsia": 0xff00ff, "indigo": 0x4b0082, "maroon": 0x800000,
}

COUPON_COSTS = {
    2.00: 0.10, 3.00: 0.20, 4.00: 0.35, 5.00: 0.50, 6.00: 0.65,
    7.00: 0.80, 8.00: 1.00, 9.00: 1.15, 10.00: 1.30, 11.00: 1.50, 12.00: 1.75,
}
HALF_OFF_COST = 0.01

SAVINGS_FILE = "savings_data.json"
DEFAULT_SAVINGS = {
    "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
    "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": [],
}

def load_savings() -> Dict[str, Any]:
    data = load_json_file(SAVINGS_FILE, DEFAULT_SAVINGS)
    merged = copy.deepcopy(DEFAULT_SAVINGS)
    merged.update(data)
    return merged

def save_savings(data: Dict[str, Any]) -> None:
    save_json_file(SAVINGS_FILE, data)

savings_tracker: Dict[str, Any] = load_savings()

def coupon_cost(coupon_val) -> float:
    if coupon_val == "half":
        return HALF_OFF_COST
    try:
        return COUPON_COSTS.get(round(float(coupon_val), 2), 0.0)
    except (ValueError, TypeError):
        return 0.0

def coupon_label(coupon_val) -> str:
    if coupon_val == "half":
        return "50% Off One Item"
    try:
        return f"${float(coupon_val):.2f} Off"
    except (ValueError, TypeError):
        return str(coupon_val)

def parse_items_input(raw_text: str) -> List[Dict[str, Any]]:
    """
    Intelligently parses item and price pairs from multi-word names, commas, newlines, or space-separated inputs.
    Examples:
    - 'Fairlife Whole Milk 4.49 Pantene Shampoo 6.59'
    - 'Fairlife Milk: $4.49, Crest 3D White 3.99'
    - 'soap 2.99'
    """
    items = []
    if not raw_text or not raw_text.strip():
        return items

    # Pattern: Name (letters/words) followed by a price ($X.XX or $X)
    pattern = re.compile(r'([A-Za-z0-9\s\-_&\.\'\"]+?)(?::|\s+)?\$?(\d+(?:\.\d{1,2})?)(?:,|$|\n)', re.IGNORECASE)
    matches = pattern.findall(raw_text)

    if matches:
        for name, price_str in matches:
            cleaned_name = name.strip(" ,:\n\t")
            if cleaned_name and not re.match(r'^\d+$', cleaned_name):
                try:
                    price = float(price_str)
                    if price >= 0:
                        items.append({"name": cleaned_name, "price": price})
                except ValueError:
                    continue
    else:
        # Fallback to token splitting if regex didn't catch (e.g. simple 'shampoo 5.00')
        tokens = raw_text.split()
        if len(tokens) >= 2 and len(tokens) % 2 == 0:
            for i in range(0, len(tokens), 2):
                try:
                    price = float(tokens[i+1].lstrip('$'))
                    items.append({"name": tokens[i], "price": price})
                except ValueError:
                    pass
    return items

def parse_coupons_input(raw_text: str) -> List[Any]:
    """
    Extracts coupon values (numbers or 50%/half) from arbitrary text inputs.
    Examples: '8 8 5 half', '$8, $5.00, 50% off', '10 off, 5 off'
    """
    coupons = []
    if not raw_text or not raw_text.strip():
        return coupons

    HALF_OFF_ALIASES = {"half", "50%", "50%off", "0.5x", "50-off", "half-off"}
    # Tokenize by comma, semicolon, space, or newline
    tokens = re.split(r'[,;\s\n]+', raw_text.strip())
    for token in tokens:
        raw = token.strip().lower()
        if not raw:
            continue
        if raw in HALF_OFF_ALIASES or "half" in raw or "50%" in raw:
            coupons.append("half")
        else:
            # Extract number
            cleaned = re.sub(r'[^\d\.]', '', raw)
            if cleaned:
                try:
                    val = float(cleaned)
                    if val > 0:
                        coupons.append(val)
                except ValueError:
                    continue
    return coupons

def resolve_color(color_input: Optional[str]) -> Optional[discord.Color]:
    if not color_input:
        return discord.Color.blurple()
    key = color_input.strip().lower()
    if key in COLOR_NAMES:
        return discord.Color(COLOR_NAMES[key])
    try:
        return discord.Color(int(key.lstrip('#'), 16))
    except ValueError:
        return None

def parse_duration(duration_str: Optional[str]) -> Optional[timedelta]:
    """Parses duration strings like 30s, 5m, 2h, 1d, 7d into a timedelta object."""
    if not duration_str:
        return None
    match = re.match(r"^(\d+)\s*([smhdw])$", duration_str.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 's':
        return timedelta(seconds=value)
    elif unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    elif unit == 'w':
        return timedelta(weeks=value)
    return None

async def safely_delete_message(ctx) -> None:
    if ctx.guild is None or ctx.interaction is not None:
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

def group_due(group_items: List[Dict[str, Any]], coupon_val) -> float:
    if not group_items:
        return 0.0
    group_sum = sum(item['price'] for item in group_items)
    if coupon_val == "half":
        max_item_price = max(item['price'] for item in group_items)
        return max(0.0, group_sum - 0.5 * max_item_price)
    try:
        c_val = float(coupon_val)
        return max(0.0, group_sum - c_val)
    except (ValueError, TypeError):
        return group_sum

def calculate_best_bundles(items: List[Dict[str, Any]], coupons: List[Any]) -> Tuple[float, Dict[int, List[Dict[str, Any]]]]:
    num_groups = len(coupons)
    if num_groups == 0 or len(items) == 0:
        return sum(item['price'] for item in items), {0: list(items)}

    total_price = sum(item['price'] for item in items)
    num_items = len(items)
    sorted_items = sorted(items, key=lambda x: x['price'], reverse=True)

    best_discount = [0.0]
    best_distribution = [{i: [] for i in range(num_groups)}]

    coupon_caps = []
    for c in coupons:
        if c == "half":
            coupon_caps.append(0.5 * sorted_items[0]['price'] if sorted_items else 0.0)
        else:
            try:
                coupon_caps.append(float(c))
            except (ValueError, TypeError):
                coupon_caps.append(0.0)

    current_groups = [[] for _ in range(num_groups)]
    current_sums = [0.0] * num_groups
    current_max_prices = [0.0] * num_groups

    def backtrack(item_idx: int, current_discount_val: float):
        if item_idx == num_items:
            if current_discount_val > best_discount[0]:
                best_discount[0] = current_discount_val
                best_distribution[0] = {i: list(current_groups[i]) for i in range(num_groups)}
            return

        rem_items = sorted_items[item_idx:]
        rem_sum = sum(it['price'] for it in rem_items)
        max_possible_extra = 0.0

        for g_idx, c_val in enumerate(coupons):
            if c_val == "half":
                curr_max = current_max_prices[g_idx]
                pot_max = max(curr_max, rem_items[0]['price'])
                max_possible_extra += max(0.0, 0.5 * pot_max - 0.5 * curr_max)
            else:
                cap = coupon_caps[g_idx]
                curr_s = current_sums[g_idx]
                max_possible_extra += max(0.0, min(curr_s + rem_sum, cap) - min(curr_s, cap))

        if current_discount_val + max_possible_extra <= best_discount[0] + 1e-9:
            return

        item = sorted_items[item_idx]
        price = item['price']

        seen_empty = False
        for g_idx in range(num_groups):
            is_empty = (len(current_groups[g_idx]) == 0)
            if is_empty:
                if seen_empty:
                    continue
                seen_empty = True

            c_val = coupons[g_idx]
            prev_sum = current_sums[g_idx]
            prev_max = current_max_prices[g_idx]

            if c_val == "half":
                old_disc = 0.5 * prev_max if len(current_groups[g_idx]) > 0 else 0.0
                new_max = max(prev_max, price)
                new_disc = 0.5 * new_max
            else:
                cap = coupon_caps[g_idx]
                old_disc = min(prev_sum, cap) if len(current_groups[g_idx]) > 0 else 0.0
                new_disc = min(prev_sum + price, cap)

            delta = new_disc - old_disc

            current_groups[g_idx].append(item)
            current_sums[g_idx] += price
            current_max_prices[g_idx] = max(prev_max, price)

            backtrack(item_idx + 1, current_discount_val + delta)

            current_groups[g_idx].pop()
            current_sums[g_idx] = prev_sum
            current_max_prices[g_idx] = prev_max

    backtrack(0, 0.0)
    total_due = max(0.0, total_price - best_discount[0])
    return total_due, best_distribution[0]

# 4. DISCORD UI MODALS & INTERACTIVE VIEWS

class AddItemModal(discord.ui.Modal, title="🛒 Add Item(s) to Cart"):
    item_input = discord.ui.TextInput(
        label="Items & Prices (One or multiple)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g.\nFairlife Milk 4.49\nPantene Shampoo 6.59\nCrest 3D White 3.99",
        required=True,
        max_length=1000
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        session = get_session(self.user_id)
        parsed = parse_items_input(self.item_input.value)
        if not parsed:
            await interaction.response.send_message(
                "❌ Could not recognize item/price pairs.\n*Example format:* `Fairlife Milk 4.49, Shampoo 6.59`",
                ephemeral=True
            )
            return

        session["items"].extend(parsed)
        subtotal = sum(i['price'] for i in session["items"])
        added_list = ", ".join(f"**{i['name']}** (${i['price']:.2f})" for i in parsed)
        await interaction.response.send_message(
            f"✅ Added {len(parsed)} item(s): {added_list}\n🛒 Cart Subtotal: **${subtotal:.2f}**",
            ephemeral=True
        )

class LoadCouponsModal(discord.ui.Modal, title="🎟️ Load Coupons"):
    coupon_values = discord.ui.TextInput(
        label="Coupon Values ($ amounts or 'half')",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. 8 8 5 half 3\nor $8, $5, 50% off",
        required=True,
        max_length=500
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        session = get_session(self.user_id)
        parsed = parse_coupons_input(self.coupon_values.value)
        if not parsed:
            await interaction.response.send_message(
                "❌ Could not recognize any coupon values.\n*Example:* `8 8 5 half` or `$8, $5, 50%`",
                ephemeral=True
            )
            return

        session["coupons"].extend(parsed)
        session["coupons"].sort(key=lambda c: -1 if c == "half" else float(c), reverse=True)
        loaded_str = ", ".join(coupon_label(c) for c in session["coupons"])
        await interaction.response.send_message(
            f"🎟️ Loaded **{len(parsed)}** coupon(s)!\nAll Active Coupons: {loaded_str}",
            ephemeral=True
        )

def _do_checkout(items: List[Dict[str, Any]], coupons: List[Any]) -> tuple:
    """
    Shared checkout helper — records the trip in savings_tracker and returns
    (embed, subtotal, total_due, coupon_spend, net_saved, datetime_now).
    """
    subtotal     = sum(i['price'] for i in items)
    total_due, _ = calculate_best_bundles(items, coupons)
    coupon_spend = sum(coupon_cost(c) for c in coupons)
    net_saved    = (subtotal - total_due) - coupon_spend
    now = datetime.now()

    savings_tracker["trip_count"]        += 1
    savings_tracker["total_full_price"]  += subtotal
    savings_tracker["total_paid"]        += total_due
    savings_tracker["total_coupon_cost"] += coupon_spend
    savings_tracker["total_net_saved"]   += net_saved
    savings_tracker.setdefault("trips", []).append({
        "date":         now.strftime("%Y-%m-%d"),
        "time":         now.strftime("%H:%M:%S"),
        "items":        [{"name": i["name"], "price": i["price"]} for i in items],
        "coupons":      coupons,
        "subtotal":     subtotal,
        "total_due":    total_due,
        "coupon_spend": coupon_spend,
        "net_saved":    net_saved,
    })
    save_savings(savings_tracker)

    embed = discord.Embed(title="✅ Trip Checked Out!", color=COLOR_SUCCESS)
    embed.add_field(name="🗓️ Date Logged",            value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
    embed.add_field(name="Full Price (No Coupons)",   value=f"${subtotal:.2f}",    inline=True)
    embed.add_field(name="Register Total Paid",       value=f"${total_due:.2f}",   inline=True)
    embed.add_field(name="Spent on Coupons",          value=f"${coupon_spend:.2f}", inline=True)
    embed.add_field(name="💰 Net Money Saved",        value=f"## **${net_saved:.2f}**", inline=False)
    embed.add_field(
        name="📈 Lifetime Total Saved",
        value=f"**${savings_tracker['total_net_saved']:.2f}** across {savings_tracker['trip_count']} trip(s)",
        inline=False
    )
    return embed, subtotal, total_due, coupon_spend, net_saved, now


class QuickCartActionView(discord.ui.View):
    """Interactive quick-action buttons attached to cart and optimizer embeds."""
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Add Items", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def btn_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddItemModal(interaction.user.id))

    @discord.ui.button(label="Load Coupons", style=discord.ButtonStyle.primary, emoji="🎟️", row=0)
    async def btn_coupons(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LoadCouponsModal(interaction.user.id))

    @discord.ui.button(label="View Cart", style=discord.ButtonStyle.secondary, emoji="🛒", row=0)
    async def btn_view_cart(self, interaction: discord.Interaction, button: discord.ui.Button):
        session    = get_session(interaction.user.id)
        items      = session["items"]
        coupons    = session["coupons"]
        subtotal   = sum(i['price'] for i in items)
        item_str   = "\n".join(f"• **{i['name']}**: ${i['price']:.2f}" for i in items) or "No items added yet."
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None loaded yet."
        embed = discord.Embed(title="🛒 AIO Shopping Cart", color=COLOR_PRIMARY)
        embed.add_field(name="Scanned Items",    value=item_str,               inline=False)
        embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=True)
        embed.add_field(name="🎟️ Active Coupons", value=coupon_str,           inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Calculate Best Plan", style=discord.ButtonStyle.primary, emoji="📊", row=1)
    async def btn_opt(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Cart is empty! Click **Add Items** first.", ephemeral=True)
            return
        embed = build_strategy_embed(session["items"], session["coupons"])
        await interaction.response.send_message(embed=embed, view=QuickCartActionView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Undo Last Item", style=discord.ButtonStyle.secondary, emoji="↩️", row=1)
    async def btn_undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Nothing to undo — your cart is empty!", ephemeral=True)
            return
        removed  = session["items"].pop()
        subtotal = sum(i['price'] for i in session["items"])
        await interaction.response.send_message(
            f"↩️ Removed **{removed['name']}** (${removed['price']:.2f}). Updated subtotal: **${subtotal:.2f}**",
            ephemeral=True
        )

    @discord.ui.button(label="Checkout", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def btn_checkout(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Cart is empty — nothing to check out!", ephemeral=True)
            return
        items   = list(session["items"])
        coupons = list(session["coupons"])
        embed   = _do_checkout(items, coupons)[0]
        reset_session(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Clear Cart", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        reset_session(interaction.user.id)
        await interaction.response.send_message("🧹 Your cart and coupons have been cleared!", ephemeral=True)


class EmbedBuilderModal(discord.ui.Modal, title="🎨 Create Custom Rich Embed"):
    embed_title = discord.ui.TextInput(
        label="Embed Title",
        placeholder="e.g. 📢 Server Announcement",
        required=True,
        max_length=256
    )
    embed_desc = discord.ui.TextInput(
        label="Description / Body Text",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your announcement or message here...",
        required=True,
        max_length=4000
    )
    embed_color = discord.ui.TextInput(
        label="Color (Hex or Name)",
        placeholder="e.g. blue, green, red, #ffaa00",
        default="blurple",
        required=False,
        max_length=30
    )
    embed_image = discord.ui.TextInput(
        label="Image URL (Optional)",
        placeholder="https://example.com/image.png",
        required=False,
        max_length=500
    )
    embed_footer = discord.ui.TextInput(
        label="Footer Text (Optional)",
        placeholder="e.g. Posted by Moderation Team",
        required=False,
        max_length=200
    )

    def __init__(self, target_channel: discord.TextChannel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        color = resolve_color(self.embed_color.value) or discord.Color.blurple()
        embed = discord.Embed(
            title=self.embed_title.value,
            description=self.embed_desc.value,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        if self.embed_image.value and self.embed_image.value.strip().startswith(('http://', 'https://')):
            embed.set_image(url=self.embed_image.value.strip())
        if self.embed_footer.value:
            embed.set_footer(text=self.embed_footer.value.strip())
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        try:
            await self.target_channel.send(embed=embed)
            await interaction.response.send_message(f"✅ Embed successfully sent to {self.target_channel.mention}!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to send embed: {e}", ephemeral=True)

class ModerationSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Lock Channel", value="lock", description="Prevent members from sending messages here", emoji="🔒"),
            discord.SelectOption(label="Unlock Channel", value="unlock", description="Restore message permissions for members", emoji="🔓"),
            discord.SelectOption(label="Nuke & Clone Channel", value="nuke", description="Delete and recreate this channel cleanly", emoji="💣"),
            discord.SelectOption(label="Set 5s Slowmode", value="slow_5s", description="Set a 5-second cooldown on messages", emoji="⏳"),
            discord.SelectOption(label="Disable Slowmode", value="slow_off", description="Turn off message cooldown", emoji="⚡"),
            discord.SelectOption(label="Purge 25 Messages", value="purge_25", description="Bulk delete the last 25 messages", emoji="🧹"),
            discord.SelectOption(label="Server Information", value="serverinfo", description="View server stats, members, and roles", emoji="📊"),
        ]
        super().__init__(placeholder="🛡️ Select a moderation action to execute...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        channel = interaction.channel
        guild = interaction.guild
        user = interaction.user

        # Channel management actions require Manage Channels or Administrator
        if choice in ("lock", "unlock", "slow_5s", "slow_off", "nuke"):
            if not (user.guild_permissions.manage_channels or user.guild_permissions.administrator):
                await interaction.response.send_message("⛔ You need the **Manage Channels** permission to perform that action.", ephemeral=True)
                return

        # Message management actions require Manage Messages or Administrator
        elif choice == "purge_25":
            if not (user.guild_permissions.manage_messages or user.guild_permissions.administrator):
                await interaction.response.send_message("⛔ You need the **Manage Messages** permission to purge messages.", ephemeral=True)
                return

        if choice == "lock":
            await channel.set_permissions(guild.default_role, send_messages=False, reason=f"Locked by {interaction.user}")
            await interaction.response.send_message(f"🔒 {channel.mention} has been **locked**.", ephemeral=False)
        elif choice == "unlock":
            await channel.set_permissions(guild.default_role, send_messages=None, reason=f"Unlocked by {interaction.user}")
            await interaction.response.send_message(f"🔓 {channel.mention} has been **unlocked**.", ephemeral=False)
        elif choice == "slow_5s":
            await channel.edit(slowmode_delay=5, reason=f"Set by {interaction.user}")
            await interaction.response.send_message(f"⏳ Slowmode set to **5 seconds** for {channel.mention}.", ephemeral=True)
        elif choice == "slow_off":
            await channel.edit(slowmode_delay=0, reason=f"Removed by {interaction.user}")
            await interaction.response.send_message(f"⚡ Slowmode disabled for {channel.mention}.", ephemeral=True)
        elif choice == "purge_25":
            deleted = await channel.purge(limit=25)
            await interaction.response.send_message(f"🧹 Purged **{len(deleted)}** messages!", ephemeral=True)
        elif choice == "nuke":
            await interaction.response.send_message("💣 Nuking and recreating channel in 2 seconds...", ephemeral=True)
            await asyncio.sleep(2)
            pos = channel.position
            new_channel = await channel.clone(reason=f"Nuked by {interaction.user}")
            await new_channel.edit(position=pos)
            await channel.delete(reason=f"Nuked by {interaction.user}")
            ts = int(datetime.now(timezone.utc).timestamp())
            embed = discord.Embed(
                title="💣 Channel Nuked",
                description=f"This channel was completely nuked and recreated. All previous messages have been cleared.",
                color=COLOR_ERROR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="🛡️ Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="💬 Channel", value=f"#{new_channel.name}", inline=True)
            embed.add_field(name="⏰ Time", value=f"<t:{ts}:R>", inline=True)
            embed.set_image(url="https://media.giphy.com/media/HhTXt43zEJbNYTX32f/giphy.gif")
            embed.set_footer(text="AIO Bot • Channel Cleanup Complete")
            await new_channel.send(embed=embed)
        elif choice == "serverinfo":
            embed = build_serverinfo_embed(guild)
            await interaction.response.send_message(embed=embed, ephemeral=True)

class ModerationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ModerationSelect())

class HelpCategorySelect(discord.ui.Select):
    def __init__(self, author_perms: discord.Permissions, is_owner: bool):
        self.author_perms = author_perms
        self.is_owner = is_owner
        options = [
            discord.SelectOption(label="Coupon Optimizer", value="coupons", description="Smart cart calculation & coupon bundling", emoji="🛍️"),
            discord.SelectOption(label="Server Moderation", value="mod", description="Server control, nuking, timeouts & locks", emoji="🛡️"),
            discord.SelectOption(label="Embeds & Utilities", value="utils", description="Custom rich embeds, latency & diagnostics", emoji="🎨"),
        ]
        if is_owner:
            options.append(discord.SelectOption(label="Owner Commands", value="owner", description="Private room setup and owner tools", emoji="👑"))
        super().__init__(placeholder="📖 Select a command category to view...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        embed = discord.Embed(color=COLOR_PRIMARY)
        if cat == "coupons":
            embed.title = "🛍️ AIO Bot — Coupon Optimizer Guide"
            embed.description = "Save the maximum out-of-pocket money at register by splitting items into optimal coupon bundles. All commands work with either `!` or `/`!"
            embed.add_field(name="Interactive Panel", value="`/panel` or `!panel` — open the interactive button & modal shopping interface", inline=False)
            embed.add_field(name="Load Coupons", value="`/coupons` or `!coupons [val1] [val2] ...` (e.g. `!coupons 8 8 5 half` or `/coupons 8 8 5 half`)", inline=False)
            embed.add_field(name="Add Items", value="`/add` or `!add [item] [price] ...` (e.g. `!add Fairlife 4.49 Shampoo 6.59` or `/add ...`)", inline=False)
            embed.add_field(name="Calculate Strategy", value="`/optimize` or `!optimize` — displays the best transaction bundles & advice", inline=False)
            embed.add_field(name="Checkout & History", value="`/checkout` or `!checkout` — save trip & get receipt\n`/savings` or `!savings` — lifetime stats\n`/history` or `!history` — view past trips\n`/delete-last-trip` or `!delete-last-trip` — undo & remove last saved trip", inline=False)
            embed.add_field(name="Instant Calculator", value="`/calc` or `!calc [items] | [coupons]` (e.g. `!calc Fairlife 4.49, Shampoo 6.59 | 8 5` or `/calc ...`)", inline=False)
            embed.add_field(name="Performance Stress Test", value="`/run-stress-test` or `!stresstest` — benchmark algorithm latency & CPU execution (aliases: `!stress`, `!benchmark`)", inline=False)
            embed.add_field(name="Test Mode (Simulated)", value="Use `!` or `/` with `test` commands (`/testadd` / `!testadd`, `/testcoupons` / `!testcoupons`, `/testoptimize` / `!testoptimize`, `/testcheckout` / `!testcheckout`, `/testclear` / `!testclear`) to practice without affecting lifetime savings.", inline=False)
        elif cat == "mod":
            embed.title = "🛡️ AIO Bot — Server Moderation Tools"
            embed.description = "Complete administrative control suite for your Discord server. Run using either `!` or `/`."
            embed.add_field(name="Mod Control Panel", value="`/modpanel` or `!modpanel` — interactive menu to lock, slowmode, purge & nuke", inline=False)
            embed.add_field(name="Channel Nuking", value="`/nukechannel` or `!nukechannel` (alias `!nuke`) — clones the channel identically and wipes previous messages", inline=False)
            embed.add_field(name="Purge / Bulk Delete", value="`/purge` or `!purge [amount] [user]` — delete messages cleanly", inline=False)
            embed.add_field(name="Member Discipline", value="`/kick` or `!kick [@member] [reason]`\n`/ban` or `!ban [@member] [reason]`\n`/unban` or `!unban [user_id_or_name]`\n`/timeout` or `!timeout [@member] [duration]` (e.g. `10m`, `1h`, `1d`)\n`/untimeout` or `!untimeout [@member]`", inline=False)
            embed.add_field(name="Warnings System", value="`/warn` or `!warn [@member] [reason]` — log a warning\n`/warnings` or `!warnings [@member]` — view warning record\n`/clearwarnings` or `!clearwarnings [@member]` — wipe records", inline=False)
            embed.add_field(name="Channel & Role Management", value="`/lock` or `!lock` / `/unlock` or `!unlock` — lockdown channel\n`/slowmode` or `!slowmode [seconds]` — set chat cooldown\n`/createchannel` or `!createchannel [name] [visibility]`\n`/blockrole` or `!blockrole` / `/unblockrole` or `!unblockrole`\n`/renamerole [@role] [new_name]` — rename server role", inline=False)
        elif cat == "utils":
            embed.title = "🎨 AIO Bot — Embeds & Utilities"
            embed.description = "Creative and diagnostic server tools. Run using either `!` or `/`."
            embed.add_field(name="Custom Embed Creator", value="`/embed` or `!embed` — open interactive modal to design & publish rich embeds with titles, images, colors, and footers", inline=False)
            embed.add_field(name="Server & Member Info", value="`/serverinfo` or `!serverinfo` — server stats, boosts, channels, and roles\n`/userinfo` or `!userinfo [@member]` — member details, account age, join date, permissions", inline=False)
            embed.add_field(name="Bot Status", value="`/ping` or `!ping` — bot latency\n`/about` or `!about` — system info", inline=False)
        elif cat == "owner":
            embed.title = "👑 AIO Bot — Owner Commands"
            embed.add_field(name="Private Optimizer Channel", value="`/setup` or `!setup` — create private `#aio-coupon-optimizer` room\n`/permit` or `!permit [@member]` — grant access to user", inline=False)

        embed.set_footer(text="Tip: You can use ! or / for any command (e.g. !help or /help).")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpMenuView(discord.ui.View):
    def __init__(self, author_perms: discord.Permissions, is_owner: bool):
        super().__init__(timeout=None)
        self.add_item(HelpCategorySelect(author_perms, is_owner))

def build_serverinfo_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title=f"📊 {guild.name} — Server Information", color=COLOR_INFO)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    owner = guild.owner or f"<@{guild.owner_id}>"
    embed.add_field(name="👑 Server Owner", value=str(owner), inline=True)
    embed.add_field(name="🆔 Server ID", value=str(guild.id), inline=True)
    embed.add_field(name="📅 Created On", value=guild.created_at.strftime("%B %d, %Y"), inline=True)
    embed.add_field(name="👥 Total Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 Channels", value=f"Text: {len(guild.text_channels)} | Voice: {len(guild.voice_channels)}", inline=True)
    embed.add_field(name="🛡️ Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="🚀 Boost Level", value=f"Tier {guild.premium_tier} ({guild.premium_subscription_count} Boosts)", inline=True)
    embed.set_footer(text="AIO Bot Server Diagnostics")
    return embed

def build_userinfo_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title=f"👤 {member.display_name} — Member Information", color=member.color or 0x3498db)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Tag", value=str(member), inline=True)
    embed.add_field(name="User ID", value=str(member.id), inline=True)
    embed.add_field(name="Bot Account?", value="Yes 🤖" if member.bot else "No 👤", inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%B %d, %Y"), inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%B %d, %Y") if member.joined_at else "Unknown", inline=True)
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:15]) if roles else "None", inline=False)
    return embed

# 5. CORE EVENTS & COMMANDS

@bot.event
async def on_ready():
    print(f'🤖 AIO Bot is officially online! Logged in as {bot.user}')
    try:
        if bot.user and bot.user.name != "AIO Bot":
            await bot.user.edit(username="AIO Bot")
            print('✅ Successfully updated Discord username to AIO Bot')
    except Exception as e:
        print(f'ℹ️ Note on bot username update: {e}', file=sys.stderr)
    try:
        if os.path.exists("avatar.png"):
            with open("avatar.png", "rb") as f:
                avatar_data = f.read()
            await bot.user.edit(avatar=avatar_data)
            print('✅ Successfully updated Discord bot profile picture (avatar.png)')
    except Exception as e:
        print(f'ℹ️ Note on bot avatar update: {e}', file=sys.stderr)

    # Automatically rename CVS Coupon Optimizer role to AIO Bot across all connected guilds
    for guild in bot.guilds:
        try:
            for role in guild.roles:
                if role.name.strip().lower() in ("cvs coupon optimizer", "cvs coupon optimizer bot", "cvs optimizer", "cvs optimizer bot"):
                    await role.edit(name="AIO Bot", reason="Update role name from CVS Coupon Optimizer to AIO Bot")
                    print(f"✅ Successfully renamed role '{role.name}' to 'AIO Bot' in guild '{guild.name}' ({guild.id})")
        except Exception as e:
            print(f"ℹ️ Note on auto role rename in guild '{guild.name}': {e}", file=sys.stderr)

    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} global slash command(s).')
    except Exception as e:
        print(f'⚠️ Slash command sync notice: {e}', file=sys.stderr)



# --- INTERACTIVE PANELS ---

@bot.hybrid_command(name="panel", description="Open the interactive Shopping Cart & Coupon Optimizer panel")
async def open_panel(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="🛒 AIO Bot — Shopping & Optimizer Panel",
        description=(
            "Use the buttons below to manage your cart in real-time!\n\n"
            "• **Add Items** — enter item names and prices\n"
            "• **Load Coupons** — enter your coupon values\n"
            "• **View Cart** — see everything in your current session\n"
            "• **Calculate Best Plan** — get the optimal register strategy\n"
            "• **Checkout** — lock in savings and record your trip"
        ),
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed, view=QuickCartActionView(ctx.author.id))


@bot.hybrid_command(name="modpanel", description="Open the interactive server moderation control panel")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def open_modpanel(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="🛡️ AIO Bot — Moderation Control Center",
        description="Select an action from the dropdown below to manage channels, slowmode, message cleanup, and server health.",
        color=COLOR_INFO
    )
    view = ModerationPanelView()
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="help", description="Show the AIO Bot interactive help menu")
async def help_command(ctx):
    await safely_delete_message(ctx)
    author_perms = ctx.channel.permissions_for(ctx.author) if ctx.guild else discord.Permissions.none()
    is_owner = await bot.is_owner(ctx.author)
    embed = discord.Embed(
        title="📖 AIO Bot — Command Center",
        description="Welcome to **AIO Bot**! Select a category from the menu below to explore features.",
        color=COLOR_PRIMARY
    )
    embed.add_field(name="🛍️ Coupon Optimizer", value="Calculates the most profitable checkout bundles for CVS and retail coupons.", inline=False)
    embed.add_field(name="🛡️ Server Moderation", value="Advanced channel nuking, lock/unlock, member timeouts, kicks, bans, and purges.", inline=False)
    embed.add_field(name="🎨 Custom Embeds & Tools", value="Build custom rich announcement embeds and access real-time diagnostics.", inline=False)
    view = HelpMenuView(author_perms, is_owner)
    await ctx.send(embed=embed, view=view)

# --- EMBED CREATOR ---

@bot.hybrid_command(name="embed", description="Open the interactive rich embed designer")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def create_embed_cmd(ctx, channel: Optional[discord.TextChannel] = None):
    target_channel = channel or ctx.channel
    if ctx.interaction:
        modal = EmbedBuilderModal(target_channel)
        await ctx.interaction.response.send_modal(modal)
    else:
        await ctx.send(f"🎨 Use `/embed` to open the interactive Embed Creator modal, or provide text directly.", delete_after=10)

# --- MODERATION & CHANNEL CONTROLS ---

@bot.hybrid_command(name="nukechannel", aliases=["nuke", "nuke-channel", "clonewipe"], description="Duplicates and replaces this channel to completely wipe it clean")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def nuke_channel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    if not isinstance(target, discord.TextChannel):
        await ctx.send("❌ Can only nuke standard text channels.", delete_after=6)
        return

    pos = target.position
    await ctx.send(f"💣 Nuking #{target.name}... Cloning channel configuration.", delete_after=3)
    await asyncio.sleep(1)

    new_channel = await target.clone(reason=f"Channel nuked by {ctx.author}")
    await new_channel.edit(position=pos)
    await target.delete(reason=f"Nuked by {ctx.author}")

    ts = int(datetime.now(timezone.utc).timestamp())
    embed = discord.Embed(
        title="💣 Channel Nuked",
        description=f"This channel was completely nuked and recreated by {ctx.author.mention}. All previous messages have been cleared.",
        color=COLOR_ERROR,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="💬 Channel", value=f"#{new_channel.name}", inline=True)
    embed.add_field(name="⏰ Time", value=f"<t:{ts}:R>", inline=True)
    embed.set_image(url="https://media.giphy.com/media/HhTXt43zEJbNYTX32f/giphy.gif")
    embed.set_footer(text="AIO Bot • Channel Cleanup Complete")
    await new_channel.send(embed=embed)

@bot.hybrid_command(name="purge", aliases=["clean", "clear_messages", "prune"], description="Bulk delete messages in this channel (optional member filter)")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def purge_messages(ctx, amount: int, member: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if amount <= 0:
        await ctx.send("❌ Provide a number greater than 0.", delete_after=6)
        return
    limit = min(amount, 100)

    if member:
        def check(m):
            return m.author.id == member.id
        deleted = await ctx.channel.purge(limit=limit, check=check)
        await ctx.send(f"🧹 Purged **{len(deleted)}** message(s) from **{member.display_name}**.", delete_after=6)
    else:
        deleted = await ctx.channel.purge(limit=limit)
        await ctx.send(f"🧹 Purged **{len(deleted)}** message(s).", delete_after=6)

@bot.hybrid_command(name="createchannel", description="Create a new text channel in the server")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def create_channel_cmd(ctx, name: str, private: bool = False, category: Optional[discord.CategoryChannel] = None):
    await safely_delete_message(ctx)
    guild = ctx.guild
    overwrites = {}
    if private:
        overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)
        overwrites[ctx.author] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    clean_name = name.lower().replace(" ", "-").strip("#")
    new_ch = await guild.create_text_channel(
        name=clean_name,
        category=category,
        overwrites=overwrites if private else None,
        reason=f"Created by {ctx.author}"
    )
    status_str = "🔒 Private" if private else "🌐 Public"
    await ctx.send(f"✅ Created channel {new_ch.mention} ({status_str})!", delete_after=8)

@bot.hybrid_command(name="blockrole", description="Block a role from viewing or sending messages in a channel")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def block_role_cmd(ctx, role: discord.Role, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    await target.set_permissions(role, read_messages=False, send_messages=False, reason=f"Blocked role by {ctx.author}")
    await ctx.send(f"🚫 Blocked **{role.name}** from {target.mention}.", delete_after=8)

@bot.hybrid_command(name="unblockrole", description="Restore channel permissions for a role")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def unblock_role_cmd(ctx, role: discord.Role, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    await target.set_permissions(role, overwrite=None, reason=f"Unblocked role by {ctx.author}")
    await ctx.send(f"✅ Restored permissions for **{role.name}** in {target.mention}.", delete_after=8)

@bot.hybrid_command(name="renamerole", description="Rename an existing server role")
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
async def rename_role_cmd(ctx, role: discord.Role, *, new_name: str):
    await safely_delete_message(ctx)
    old_name = role.name

    if role.managed:
        embed = discord.Embed(
            title="ℹ️ Discord Managed Role",
            description=(
                f"**{old_name}** is a Discord **Managed Bot Integration Role**.\n\n"
                "Discord does not allow bots or moderators to edit managed integration role names directly via API or Discord client.\n\n"
                "### 🔧 How to change it in 10 seconds:\n"
                "1. Go to the **[Discord Developer Portal](https://discord.com/developers/applications)**\n"
                "2. Select your Bot Application\n"
                "3. In **General Information**, change the **NAME** to **`AIO Bot`** (or your desired name)\n"
                "4. Click **Save Changes**\n\n"
                "Discord will immediately update this integration role name across all your servers!"
            ),
            color=COLOR_INFO
        )
        await ctx.send(embed=embed)
        return

    try:
        await role.edit(name=new_name.strip(), reason=f"Renamed by {ctx.author}")
        await ctx.send(f"✅ Renamed role **{old_name}** to **{new_name.strip()}**!", delete_after=8)
    except discord.Forbidden:
        await ctx.send(f"❌ Failed to rename **{old_name}**. Ensure the bot's role is positioned above the target role in Server Settings > Roles.", delete_after=10)
    except Exception as e:
        await ctx.send(f"❌ Error renaming role: `{e}`", delete_after=8)

@bot.hybrid_command(name="kick", description="Kick a member from the server")
@commands.guild_only()
@commands.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
async def kick_member(ctx, member: discord.Member, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("⛔ You cannot kick a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.kick(reason=f"{reason} (by {ctx.author})")
        await ctx.send(f"👢 Kicked **{member}** | Reason: `{reason}`", delete_after=8)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to kick this user.", delete_after=6)

@bot.hybrid_command(name="ban", description="Ban a member from the server")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def ban_member(ctx, member: discord.Member, delete_message_days: Optional[int] = 0, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("⛔ You cannot ban a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.ban(delete_message_days=min(delete_message_days or 0, 7), reason=f"{reason} (by {ctx.author})")
        await ctx.send(f"🔨 Banned **{member}** | Reason: `{reason}`", delete_after=8)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to ban this user.", delete_after=6)

@bot.hybrid_command(name="unban", description="Unban a user by their user ID or Username#1234")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def unban_user(ctx, *, user_query: str):
    await safely_delete_message(ctx)
    bans = [entry async for entry in ctx.guild.bans()]
    target_user = None

    for ban_entry in bans:
        u = ban_entry.user
        if str(u.id) == user_query.strip() or u.name.lower() == user_query.strip().lower() or f"{u.name}#{u.discriminator}" == user_query.strip():
            target_user = u
            break

    if not target_user:
        await ctx.send(f"❌ Could not find a banned user matching `{user_query}`.", delete_after=6)
        return

    await ctx.guild.unban(target_user, reason=f"Unbanned by {ctx.author}")
    await ctx.send(f"🕊️ Unbanned **{target_user}**!", delete_after=6)

@bot.hybrid_command(name="timeout", aliases=["mute"], description="Timeout/mute a member for a set duration (e.g. 5m, 1h, 1d)")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def timeout_member(ctx, member: discord.Member, duration: str, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("⛔ You cannot timeout a member with an equal or higher role than you.", delete_after=6)
        return
    td = parse_duration(duration)
    if not td:
        await ctx.send("❌ Invalid duration. Examples: `10m` (10 mins), `2h` (2 hours), `1d` (1 day), `1w` (1 week)", delete_after=8)
        return
    if td > timedelta(days=28):
        await ctx.send("❌ Discord timeouts cannot exceed 28 days.", delete_after=6)
        return

    try:
        await member.timeout(td, reason=f"{reason} (by {ctx.author})")
        await ctx.send(f"🔇 Timed out **{member.mention}** for **{duration}** | Reason: `{reason}`", delete_after=8)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to timeout this user.", delete_after=6)

@bot.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove timeout from a member")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def untimeout_member(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("⛔ You cannot untimeout a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        await ctx.send(f"🔊 Removed timeout for **{member.mention}**.", delete_after=6)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to untimeout this user.", delete_after=6)

@bot.hybrid_command(name="warn", description="Issue an official warning to a member")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def warn_member(ctx, member: discord.Member, *, reason: str):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("⛔ You cannot warn a member with an equal or higher role than you.", delete_after=6)
        return
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)
    key = f"{guild_id}_{user_id}"

    record = {
        "reason": reason,
        "moderator": str(ctx.author),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    }
    warnings_db.setdefault(key, []).append(record)
    save_warnings(warnings_db)

    count = len(warnings_db[key])
    await ctx.send(f"⚠️ Warned **{member.mention}** | Warning #{count} | Reason: `{reason}`", delete_after=10)

@bot.hybrid_command(name="warnings", description="View warnings logged for a member")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def view_warnings(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    key = f"{ctx.guild.id}_{member.id}"
    user_warns = warnings_db.get(key, [])

    if not user_warns:
        await ctx.send(f"✨ **{member.display_name}** has 0 active warnings on record.", delete_after=8)
        return

    embed = discord.Embed(title=f"⚠️ Warnings for {member.display_name} ({len(user_warns)})", color=COLOR_WARN)
    for idx, w in enumerate(user_warns[-10:], 1):
        embed.add_field(
            name=f"Warning #{idx} — {w['timestamp']}",
            value=f"**Reason:** {w['reason']}\n**Mod:** {w['moderator']}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.hybrid_command(name="clearwarnings", description="Clear all warnings for a member")
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def clear_warnings(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    key = f"{ctx.guild.id}_{member.id}"
    warnings_db.pop(key, None)
    save_warnings(warnings_db)
    await ctx.send(f"🧹 Cleared all warnings for **{member.mention}**.", delete_after=6)

@bot.hybrid_command(name="lock", description="Lock a channel to prevent regular members from sending messages")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def lock_channel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    await target.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Locked by {ctx.author}")
    await ctx.send(f"🔒 **{target.mention}** is now locked.", delete_after=6)

@bot.hybrid_command(name="unlock", description="Unlock a channel to allow regular members to send messages")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def unlock_channel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    await target.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Unlocked by {ctx.author}")
    await ctx.send(f"🔓 **{target.mention}** is now unlocked.", delete_after=6)

@bot.hybrid_command(name="slowmode", description="Set channel slowmode cooldown (e.g. 5s, 1m, 0)")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def set_slowmode(ctx, duration: str, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    if duration.strip() in ("0", "off", "none"):
        seconds = 0
    else:
        td = parse_duration(duration)
        if not td:
            try:
                seconds = int(duration)
            except ValueError:
                await ctx.send("❌ Invalid duration. Example: `5s`, `30s`, `2m`, `0`", delete_after=6)
                return
        else:
            seconds = int(td.total_seconds())

    if seconds < 0 or seconds > 21600:
        await ctx.send("❌ Slowmode must be between 0 seconds and 6 hours (21600s).", delete_after=6)
        return

    await target.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author}")
    if seconds == 0:
        await ctx.send(f"⚡ Slowmode disabled for {target.mention}.", delete_after=6)
    else:
        await ctx.send(f"⏳ Set slowmode for {target.mention} to **{seconds}s**.", delete_after=6)

@bot.hybrid_command(name="serverinfo", description="Display detailed server stats and information")
@commands.guild_only()
async def server_info(ctx):
    await safely_delete_message(ctx)
    embed = build_serverinfo_embed(ctx.guild)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo", description="Display detailed member information")
@commands.guild_only()
async def user_info(ctx, member: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    target = member or ctx.author
    embed = build_userinfo_embed(target)
    await ctx.send(embed=embed)

def build_strategy_embed(items: List[Dict[str, Any]], coupons: List[Any]) -> discord.Embed:
    if not items:
        embed = discord.Embed(
            title="🛒 Shopping Cart is Empty",
            description="Add items using `/add` or the **Add Items** button below.",
            color=COLOR_ERROR
        )
        return embed

    total_due, bundling = calculate_best_bundles(items, coupons)
    full_subtotal = sum(i['price'] for i in items)
    total_savings = full_subtotal - total_due
    savings_pct = (total_savings / full_subtotal * 100) if full_subtotal > 0 else 0

    embed = discord.Embed(
        title="🧾 Cashier Step-by-Step Checkout Strategy",
        description=(
            f"Here is your optimal register plan to pay the absolute minimum!\n"
            f"Tell the cashier you are doing **{len(coupons) if coupons else 1} transaction(s)**."
        ),
        color=COLOR_SUCCESS if total_savings > 0 else 0x3498db
    )

    if not coupons:
        item_lines = "\n".join([f"• **{i['name']}**: ${i['price']:.2f}" for i in items])
        embed.add_field(
            name="Single Transaction (No Coupons Loaded)",
            value=f"{item_lines}\n\n**Total Due at Register: ${total_due:.2f}**",
            inline=False
        )
    else:
        for idx, coupon_val in enumerate(coupons):
            group_items = bundling.get(idx, [])
            if group_items:
                item_lines = "\n".join([f"• **{i['name']}**: ${i['price']:.2f}" for i in group_items])
                group_sub = sum(i['price'] for i in group_items)
                due = group_due(group_items, coupon_val)
                saved_amt = group_sub - due
                embed.add_field(
                    name=f"🛒 Step {idx+1}: Ring Up {len(group_items)} Item(s)",
                    value=(
                        f"{item_lines}\n"
                        f"── Subtotal: **${group_sub:.2f}**\n"
                        f"🎟️ Scan: **{coupon_label(coupon_val)}**\n"
                        f"💵 **Cashier Price Due: ${due:.2f}** *(Saved ${saved_amt:.2f}!)*"
                    ),
                    inline=False
                )

    # Cart Summary
    embed.add_field(
        name="📊 Checkout Summary",
        value=(
            f"🏷️ **Full Retail Value:** ${full_subtotal:.2f}\n"
            f"🎟️ **Total Discounts:** -${total_savings:.2f} ({savings_pct:.0f}% OFF)\n"
            f"💵 **Final Out-of-Pocket Total:** ## **${total_due:.2f}**"
        ),
        inline=False
    )

    # Smart upgrade recommendations
    if total_due > 0.0:
        candidate_values = sorted(COUPON_COSTS.keys()) + ["half"]
        working_coupons = list(coupons)
        working_due = total_due
        suggestions = []

        for _ in range(2):
            best_candidate = None
            best_net_benefit = 1e-9
            best_trial = None
            for cp in candidate_values:
                trial_coupons = working_coupons + [cp]
                trial_due, trial_bundling = calculate_best_bundles(items, trial_coupons)
                register_savings = working_due - trial_due
                net_benefit = register_savings - coupon_cost(cp)
                if net_benefit > best_net_benefit:
                    best_net_benefit = net_benefit
                    best_candidate = cp
                    best_trial = (trial_due, trial_bundling, register_savings)

            if best_candidate is None:
                break

            trial_due, trial_bundling, register_savings = best_trial
            suggestions.append(
                f"• Buy a **{coupon_label(best_candidate)}** coupon (costs ${coupon_cost(best_candidate):.2f}) "
                f"➔ Drops register price to **${trial_due:.2f}** (saves ${register_savings:.2f}, **${best_net_benefit:.2f} net profit** in your pocket!)"
            )
            working_coupons.append(best_candidate)
            working_due = trial_due

        if suggestions:
            embed.add_field(
                name="💡 Extra Savings Opportunities",
                value="You can save even more money by buying these coupons:\n" + "\n".join(suggestions),
                inline=False
            )

    embed.set_footer(text="Click 'Checkout' below once purchased to record your lifetime savings!")
    return embed

# --- COUPON OPTIMIZER COMMANDS ---

@bot.hybrid_command(name="add", description="Add items to your cart (e.g. Fairlife Milk 4.49, Pantene Shampoo 6.59)")
async def add_item(ctx, *, items: str):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    parsed = parse_items_input(items)
    if not parsed:
        await ctx.send("❌ Could not recognize item/price pairs.\n*Example:* `!add Fairlife Milk 4.49, Shampoo 6.59` or `/add ...`", delete_after=8)
        return

    session["items"].extend(parsed)
    subtotal = sum(item['price'] for item in session["items"])
    embed = discord.Embed(title="🛒 AIO Shopping Cart", color=COLOR_PRIMARY)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    coupon_str = ", ".join([coupon_label(c) for c in session["coupons"]]) or "None loaded yet."
    added_str = ", ".join(f"**{i['name']}** (${i['price']:.2f})" for i in parsed)

    embed.add_field(name=f"Added {len(parsed)} Item(s)", value=added_str, inline=False)
    embed.add_field(name="Scanned Items", value=item_str, inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=True)
    embed.add_field(name="Active Coupons", value=coupon_str, inline=True)
    view = QuickCartActionView(ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="coupons", description="Add coupon values to your session (e.g. 8 8 5 half)")
async def set_coupons(ctx, *, values: str):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    parsed = parse_coupons_input(values)
    if not parsed:
        await ctx.send("❌ Could not recognize coupon values.\n*Example:* `!coupons 8 8 5 half` or `/coupons 8 8 5 half`", delete_after=8)
        return

    session["coupons"].extend(parsed)
    session["coupons"].sort(key=lambda c: -1 if c == "half" else float(c), reverse=True)
    added_str = ", ".join([coupon_label(c) for c in parsed])
    all_str = ", ".join([coupon_label(c) for c in session["coupons"]])

    embed = discord.Embed(title="🎟️ Coupons Loaded", color=COLOR_SUCCESS)
    embed.add_field(name="Just Added", value=added_str, inline=False)
    embed.add_field(name="All Active Coupons", value=all_str, inline=False)
    view = QuickCartActionView(ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="optimize", description="Calculate the step-by-step cashier checkout plan for your cart")
async def optimize_cart(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    items = session["items"]
    coupons = session["coupons"]
    if not items:
        await ctx.send("❌ Your cart is empty! Add items with `/add`, `!add`, or `/panel` first.", delete_after=8)
        return

    embed = build_strategy_embed(items, coupons)
    view = QuickCartActionView(ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="calc", aliases=["quickcalc"], description="Instant 1-step calculation without saving a cart (e.g. Fairlife 4.49, Shampoo 6.59 | 8 5)")
async def quick_calc(ctx, *, query: str):
    await safely_delete_message(ctx)
    parts = query.split("|", 1)
    items_raw = parts[0].strip()
    coupons_raw = parts[1].strip() if len(parts) > 1 else ""

    items = parse_items_input(items_raw)
    coupons = parse_coupons_input(coupons_raw)

    if not items:
        await ctx.send("❌ Could not parse items.\n*Format:* `/calc Item1 Price1, Item2 Price2 | Coupon1 Coupon2` (or `!calc ...`)\n*Example:* `!calc Fairlife 4.49, Shampoo 6.59 | 8 5` or `/calc ...`", delete_after=10)
        return

    embed = build_strategy_embed(items, coupons)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="cart", description="View your current shopping cart")
async def view_cart(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    items = session["items"]
    coupons = session["coupons"]
    embed = discord.Embed(title="🛒 AIO Shopping Cart", color=COLOR_PRIMARY)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in items]) or "No items added yet."
    subtotal = sum(item['price'] for item in items)
    coupon_str = ", ".join([coupon_label(c) for c in coupons]) or "None loaded yet."
    embed.add_field(name="Scanned Items", value=item_str, inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=True)
    embed.add_field(name="Active Coupons", value=coupon_str, inline=True)
    view = QuickCartActionView(ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="undo", description="Undo the last item you added to your cart")
async def undo_item(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    if not session["items"]:
        await ctx.send("❌ Nothing to undo — your cart is empty!", delete_after=5)
        return
    removed_item = session["items"].pop()
    subtotal = sum(item['price'] for item in session["items"])
    await ctx.send(f"↩️ Removed **{removed_item['name']}** (${removed_item['price']:.2f}). Updated subtotal: **${subtotal:.2f}**")

@bot.hybrid_command(name="remove", description="Remove an item from your cart by name")
async def remove_item(ctx, *, item_name: str):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    found = False
    for item in reversed(session["items"]):
        if item["name"].lower() == item_name.lower() or item_name.lower() in item["name"].lower():
            session["items"].remove(item)
            found = True
            break
    if found:
        subtotal = sum(item['price'] for item in session["items"])
        await ctx.send(f"❌ Removed **{item_name}** from your cart. Updated subtotal: **${subtotal:.2f}**")
    else:
        await ctx.send(f"⚠️ Could not find an item matching '**{item_name}**' inside your cart.", delete_after=5)

@bot.hybrid_command(name="clear", description="Clear your cart and coupons")
async def clear_cart(ctx):
    await safely_delete_message(ctx)
    reset_session(ctx.author.id)
    await ctx.send("🧹 Cart and coupons cleared!")

@bot.hybrid_command(name="checkout", description="Finalize your trip, log savings, and clear your cart")
async def checkout(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id)
    items   = list(session["items"])
    coupons = list(session["coupons"])
    if not items:
        await ctx.send("❌ Your cart is empty — nothing to check out!", delete_after=5)
        return

    embed, subtotal, total_due, coupon_spend, net_saved, now = _do_checkout(items, coupons)
    await ctx.send(embed=embed)

    # Send DM receipt
    try:
        item_str   = "\n".join(f"• **{i['name']}**: ${i['price']:.2f}" for i in items) or "No items."
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None"
        dm = discord.Embed(title="🧾 Your AIO Trip Receipt", color=COLOR_SUCCESS)
        dm.add_field(name="🗓️ Date",            value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
        dm.add_field(name="🛒 Items Purchased",  value=item_str,                                  inline=False)
        dm.add_field(name="🎟️ Coupons Used",    value=coupon_str,                                 inline=False)
        dm.add_field(name="Full Price",          value=f"${subtotal:.2f}",                        inline=True)
        dm.add_field(name="Paid at Register",    value=f"${total_due:.2f}",                       inline=True)
        dm.add_field(name="Coupon Cost",         value=f"${coupon_spend:.2f}",                    inline=True)
        dm.add_field(name="💰 Net Money Saved",  value=f"## **${net_saved:.2f}**",                inline=False)
        await ctx.author.send(embed=dm)
    except (discord.Forbidden, discord.HTTPException):
        pass  # DMs disabled — silently skip

    reset_session(ctx.author.id)

# --- STRESS TEST & BENCHMARK ---

@bot.hybrid_command(
    name="run-stress-test",
    aliases=["stresstest", "stress-test", "run_stress_test", "stress_test", "stress", "benchmark", "runstresstest"],
    description="Run a real-time CPU & bundling performance stress test"
)
async def run_stress_test_cmd(ctx, num_items: Optional[int] = 16, num_coupons: Optional[int] = 5):
    """Simulates a large shopping cart to benchmark algorithm execution latency."""
    await safely_delete_message(ctx)

    n_items = min(max(num_items or 16, 4), 30)
    n_coups = min(max(num_coupons or 5, 1), 10)

    sample_names = [
        "Fairlife Milk", "Tide Pods", "Pantene Shampoo", "Crest 3D White", "Dove Body Wash",
        "Dawn Dish Soap", "Bounty Paper Towels", "Charmin Toilet Paper", "Colgate Toothpaste",
        "Neutrogena Sunscreen", "L'Oreal Face Wash", "Old Spice Deodorant", "Gillette Razors",
        "Head & Shoulders", "Oral-B Toothbrush", "Secret Deodorant", "Gain Flings", "Febreze Spray"
    ]

    test_items = []
    for i in range(n_items):
        name = f"{random.choice(sample_names)} #{i+1}"
        price = round(random.uniform(2.99, 19.99), 2)
        test_items.append({"name": name, "price": price})

    sample_coupon_vals = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, "half"]
    test_coupons = random.sample(sample_coupon_vals, min(n_coups, len(sample_coupon_vals)))

    start_time = time.perf_counter()
    total_due, bundling = calculate_best_bundles(test_items, test_coupons)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    full_price = sum(i['price'] for i in test_items)
    saved = full_price - total_due
    pct = (saved / full_price * 100) if full_price > 0 else 0

    embed = discord.Embed(
        title="⚡ Optimizer Stress Test & Performance Benchmark",
        description=f"Successfully stress-tested the engine with **{n_items} items** and **{len(test_coupons)} coupons**.",
        color=COLOR_SUCCESS if elapsed_ms < 50 else 0xf1c40f
    )
    embed.add_field(name="⏱️ Computation Latency", value=f"**{elapsed_ms:.2f} ms**", inline=True)
    embed.add_field(name="📦 Items Processed", value=f"**{n_items}** items (${full_price:.2f})", inline=True)
    embed.add_field(name="🎟️ Coupons Bundled", value=", ".join(coupon_label(c) for c in test_coupons), inline=True)
    embed.add_field(name="💵 Register Total Due", value=f"**${total_due:.2f}**", inline=True)
    embed.add_field(name="💰 Dollars Saved", value=f"**${saved:.2f}** ({pct:.0f}% off)", inline=True)
    status_label = "✅ **Sub-10ms Branch-and-Bound (Ultra Fast)**" if elapsed_ms < 10 else "✅ **Healthy (<100ms)**"
    embed.add_field(name="🚀 Engine Health", value=status_label, inline=False)
    embed.set_footer(text="AIO Bot High-Performance Combinatorial Engine")
    await ctx.send(embed=embed)

# --- TEST MODE COMMANDS (Isolated Test Cart) ---

@bot.hybrid_command(name="testadd", description="[TEST] Add items to your isolated test cart")
async def test_add_item(ctx, *, items: str):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id, test=True)
    parsed = parse_items_input(items)
    if not parsed:
        await ctx.send("❌ Could not parse items.\n*Example:* `!testadd Fairlife 4.49, Shampoo 6.59` or `/testadd ...`", delete_after=8)
        return
    session["items"].extend(parsed)
    subtotal = sum(i['price'] for i in session["items"])
    await ctx.send(f"🧪 [TEST] Added {len(parsed)} item(s)! Test Cart Subtotal: **${subtotal:.2f}**")

@bot.hybrid_command(name="testcoupons", description="[TEST] Add coupons to your isolated test cart")
async def test_set_coupons(ctx, *, values: str):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id, test=True)
    parsed = parse_coupons_input(values)
    if not parsed:
        await ctx.send("❌ Could not parse coupons.\n*Example:* `!testcoupons 8 8 5 half` or `/testcoupons 8 8 5 half`", delete_after=8)
        return
    session["coupons"].extend(parsed)
    all_str = ", ".join(coupon_label(c) for c in session["coupons"])
    await ctx.send(f"🧪 [TEST] Loaded coupons! All Test Coupons: {all_str}")

@bot.hybrid_command(name="testoptimize", description="[TEST] Calculate strategy for test cart without saving stats")
async def test_optimize(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id, test=True)
    if not session["items"]:
        await ctx.send("❌ Test cart is empty! Add items with `/testadd` or `!testadd` first.", delete_after=8)
        return
    embed = build_strategy_embed(session["items"], session["coupons"])
    embed.title = "🧪 [TEST] " + embed.title
    await ctx.send(embed=embed)

@bot.hybrid_command(name="testcart", description="[TEST] View your test shopping cart")
async def test_cart(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id, test=True)
    items = session["items"]
    coupons = session["coupons"]
    embed = discord.Embed(title="🧪 [TEST] Shopping Cart", color=COLOR_TEST)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in items]) or "No test items."
    subtotal = sum(item['price'] for item in items)
    coupon_str = ", ".join([coupon_label(c) for c in coupons]) or "None loaded."
    embed.add_field(name="Scanned Items", value=item_str, inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=True)
    embed.add_field(name="Active Coupons", value=coupon_str, inline=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="testcheckout", description="[TEST] Preview checkout without recording lifetime savings")
async def test_checkout(ctx):
    await safely_delete_message(ctx)
    session = get_session(ctx.author.id, test=True)
    items = list(session["items"])
    coupons = list(session["coupons"])
    if not items:
        await ctx.send("❌ Test cart is empty!", delete_after=5)
        return
    subtotal = sum(i['price'] for i in items)
    total_due, _ = calculate_best_bundles(items, coupons)
    coupon_spend = sum(coupon_cost(c) for c in coupons)
    net_saved = (subtotal - total_due) - coupon_spend
    embed = discord.Embed(title="🧪 [TEST] Checkout Simulation", color=COLOR_TEST)
    embed.add_field(name="Full Price", value=f"${subtotal:.2f}", inline=True)
    embed.add_field(name="Register Paid", value=f"${total_due:.2f}", inline=True)
    embed.add_field(name="Net Saved", value=f"## **${net_saved:.2f}**", inline=False)
    embed.set_footer(text="Simulated checkout — lifetime savings untouched. Test cart cleared.")
    reset_session(ctx.author.id, test=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="testclear", description="[TEST] Clear test cart and coupons")
async def test_clear(ctx):
    await safely_delete_message(ctx)
    reset_session(ctx.author.id, test=True)
    await ctx.send("🧪 Test cart cleared!")

@bot.hybrid_command(name="savings", description="View your lifetime savings stats")
async def view_savings(ctx):
    await safely_delete_message(ctx)
    s = savings_tracker
    embed = discord.Embed(title="💰 Lifetime Savings Tracker", color=COLOR_SUCCESS)
    if s.get("trip_count", 0) == 0:
        embed.description = "No trips checked out yet. Run `/checkout` or `!checkout` after optimizing to start tracking your savings!"
    else:
        trip_count = s["trip_count"]
        avg_saved = s["total_net_saved"] / trip_count if trip_count > 0 else 0.0
        embed.add_field(name="🧾 Trips Checked Out", value=str(trip_count), inline=True)
        embed.add_field(name="🏷️ Total Full Price", value=f"${s['total_full_price']:.2f}", inline=True)
        embed.add_field(name="💵 Total Actually Paid", value=f"${s['total_paid']:.2f}", inline=True)
        embed.add_field(name="🎟️ Total Spent on Coupons", value=f"${s['total_coupon_cost']:.2f}", inline=True)
        embed.add_field(name="📊 Avg Net Saved / Trip", value=f"${avg_saved:.2f}", inline=True)
        embed.add_field(name="💰 Lifetime Net Money Saved", value=f"## **${s['total_net_saved']:.2f}**", inline=False)
    await ctx.send(embed=embed)

def _parse_history_date(raw: Optional[str]):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None

@bot.hybrid_command(name="history", description="View past trips (all, one date, or a date range)")
async def view_history(ctx, start: Optional[str] = None, end: Optional[str] = None):
    await safely_delete_message(ctx)
    trips = savings_tracker.get("trips", [])
    if not trips:
        await ctx.send("📭 No checked-out trips logged yet.", delete_after=8)
        return

    start_date, end_date = None, None
    if start:
        start_date = _parse_history_date(start)
        if not start_date:
            await ctx.send("❌ Couldn't read that date. Use `YYYY-MM-DD`.", delete_after=8)
            return
        end_date = _parse_history_date(end) if end else start_date
        if end and not end_date:
            await ctx.send("❌ Couldn't read end date. Use `YYYY-MM-DD`.", delete_after=8)
            return
        if start_date and end_date and end_date < start_date:
            start_date, end_date = end_date, start_date

    if start_date and end_date:
        matches = [t for t in trips if (d := _parse_history_date(t.get("date", ""))) and start_date <= d <= end_date]
    else:
        matches = trips[-10:]

    if not matches:
        await ctx.send(f"📭 No trips found for that date range.", delete_after=8)
        return

    embed = discord.Embed(title="📜 Trip History", color=COLOR_INFO)
    for trip in matches[-15:]:
        item_names = ", ".join(i["name"] for i in trip.get("items", [])) or "Items"
        embed.add_field(
            name=f"🗓️ {trip.get('date', '—')} @ {trip.get('time', '—')}",
            value=f"Items: {item_names}\nPaid: ${trip.get('total_due', 0.0):.2f} (Full: ${trip.get('subtotal', 0.0):.2f})\n💰 Net Saved: **${trip.get('net_saved', 0.0):.2f}**",
            inline=False
        )
    await ctx.send(embed=embed)

def delete_last_trip() -> Optional[Dict[str, Any]]:
    """Remove the most recent trip from savings_tracker and recalculate totals."""
    trips = savings_tracker.get("trips", [])
    if not trips:
        return None
    removed = trips.pop()
    savings_tracker["trip_count"] = max(0, savings_tracker.get("trip_count", 1) - 1)
    savings_tracker["total_full_price"] = max(0.0, round(savings_tracker.get("total_full_price", 0.0) - removed.get("subtotal", 0.0), 2))
    savings_tracker["total_paid"] = max(0.0, round(savings_tracker.get("total_paid", 0.0) - removed.get("total_due", 0.0), 2))
    savings_tracker["total_coupon_cost"] = max(0.0, round(savings_tracker.get("total_coupon_cost", 0.0) - removed.get("coupon_spend", 0.0), 2))
    savings_tracker["total_net_saved"] = max(0.0, round(savings_tracker.get("total_net_saved", 0.0) - removed.get("net_saved", 0.0), 2))
    save_savings(savings_tracker)
    return removed

@bot.hybrid_command(
    name="delete-last-trip",
    aliases=["deletelasttrip", "delete_last_trip", "undotrip", "undo-trip", "removelasttrip", "remove-last-trip"],
    description="Delete the most recently recorded trip and reverse its savings stats"
)
async def delete_last_trip_cmd(ctx):
    await safely_delete_message(ctx)
    removed = delete_last_trip()
    if not removed:
        await ctx.send("📭 No trips on record to delete.", delete_after=8)
        return

    item_names = ", ".join(i["name"] for i in removed.get("items", [])) or "Items"
    embed = discord.Embed(title="🗑️ Trip Deleted & Reverted", color=COLOR_WARN)
    embed.add_field(name="🗓️ Trip Date", value=f"{removed.get('date', '—')} @ {removed.get('time', '—')}", inline=False)
    embed.add_field(name="🛒 Items Removed", value=item_names, inline=False)
    embed.add_field(name="💵 Register Paid (Reverted)", value=f"${removed.get('total_due', 0.0):.2f}", inline=True)
    embed.add_field(name="💰 Savings (Reverted)", value=f"${removed.get('net_saved', 0.0):.2f}", inline=True)
    embed.add_field(
        name="📊 Updated Lifetime Saved",
        value=f"**${savings_tracker['total_net_saved']:.2f}** across {savings_tracker['trip_count']} trip(s)",
        inline=False
    )
    embed.set_footer(text="The trip was removed from your history and lifetime stats have been recalculated.")
    await ctx.send(embed=embed)


# --- SETUP & CHANNELS ---

@bot.hybrid_command(name="setup", description="Create the private AIO coupon optimizer channel")
@commands.guild_only()
@commands.is_owner()
@app_commands.default_permissions(administrator=True)
async def setup_channel(ctx):
    await safely_delete_message(ctx)
    guild = ctx.guild
    owner = guild.owner or (await guild.fetch_member(guild.owner_id) if guild.owner_id else None)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True, manage_messages=True)
    }
    if owner:
        overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True)

    channel_name = "aio-coupon-optimizer"
    existing = discord.utils.get(guild.text_channels, name=channel_name)
    if existing:
        await ctx.send(f"⚠️ Channel {existing.mention} already exists!", delete_after=5)
        return

    new_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
    welcome = discord.Embed(
        title="🎯 AIO Coupon Optimizer Room",
        description="Private command base for shopping bundles! Run `/panel` or `!panel` (or `/help` / `!help`) to get started.",
        color=COLOR_PRIMARY
    )
    await new_channel.send(embed=welcome)
    await ctx.send(f"✅ Secure channel {new_channel.mention} created!", delete_after=5)

@bot.hybrid_command(name="permit", description="Grant a member access to the coupon optimizer channel")
@commands.guild_only()
@commands.is_owner()
@app_commands.default_permissions(administrator=True)
async def permit_user(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    channel = discord.utils.get(ctx.guild.text_channels, name="aio-coupon-optimizer")
    if not channel:
        await ctx.send("❌ `#aio-coupon-optimizer` doesn't exist yet. Run `/setup` or `!setup` first!", delete_after=5)
        return
    await channel.set_permissions(member, view_channel=True, send_messages=True, read_messages=True)
    await ctx.send(f"✅ Granted access to {member.mention}!", delete_after=5)

@bot.hybrid_command(name="ping", description="Check the bot's latency")
async def ping(ctx):
    await safely_delete_message(ctx)
    latency_ms = round(bot.latency * 1000) if bot.latency and not (bot.latency != bot.latency) else 0
    await ctx.send(f"🏓 Pong! Latency: **{latency_ms}ms**", delete_after=8)

@bot.hybrid_command(name="about", description="About AIO Bot")
async def about(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="ℹ️ About AIO Bot",
        description="All-In-One Discord assistant featuring advanced server moderation, channel nuking & cloning, rich embed builder, and combinatorics shopping optimizer.",
        color=COLOR_PRIMARY
    )
    embed.add_field(name="Commands", value="Run `/help` or `!help` to explore all tools.", inline=False)
    embed.add_field(name="Features", value="• 🛡️ Server Moderation & Channel Nuker\n• 🎨 Rich Embed Builder\n• 🛍️ Coupon Optimizer & Savings Engine", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("⛔ Security Error: Only the bot application owner can run this command.", delete_after=10)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("⛔ This command can only be used inside a server channel.", delete_after=8)
    elif isinstance(error, discord.Forbidden):
        await ctx.send(f"❌ Permission Error: The bot is missing required Discord permissions to perform that action.", delete_after=15)
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingPermissions):
        missing = ", ".join(p.replace('_', ' ').title() for p in error.missing_permissions)
        await ctx.send(f"⛔ You need the **{missing}** permission to run that.", delete_after=8)
    elif isinstance(error, (commands.RoleNotFound, commands.ChannelNotFound, commands.MemberNotFound)):
        await ctx.send(f"❌ {error}", delete_after=8)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(f"⛔ Permission Error: You do not meet the permission requirements for this command.", delete_after=8)
    else:
        print(f"❌ Command Error in '{ctx.command}': {type(error).__name__} | Details: {error}", file=sys.stderr)
        try:
            await ctx.send(f"❌ Error: `{type(error).__name__}: {error}`", delete_after=15)
        except Exception:
            pass

def main():
    keep_alive()
    token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
    if not token or token.strip() in ("", "YOUR_BOT_TOKEN_HERE"):
        print("=" * 60)
        print("⚠️ NOTICE: DISCORD_BOT_TOKEN environment variable is not set.")
        print("The background web server is running on port 8000.")
        print("=" * 60)
        try:
            import time
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            print("Bot shutdown.")
    else:
        bot.run(token)

if __name__ == '__main__':
    main()
