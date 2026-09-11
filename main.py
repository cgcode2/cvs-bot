from __future__ import annotations
import io
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal, Optional, Dict, Any, List, Tuple, Union, Set, Callable
import asyncio
import itertools
import copy
import re
from flask import Flask, jsonify, redirect
from threading import Thread
import os
import sys
import json
import time
import random
import secrets
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
    try:
        port_val = os.environ.get('PORT', '8000')
        port = int(port_val) if port_val and str(port_val).strip().isdigit() else 8000
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        print(f"⚠️ Web server encountered an error: {e}", file=sys.stderr)

def keep_alive():
    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

# 2. DISCORD BOT ENGINE
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
owner_id_env = os.environ.get('OWNER_ID') or os.environ.get('BOT_OWNER_ID')
if owner_id_env and owner_id_env.strip().isdigit():
    bot.owner_id = int(owner_id_env.strip())

# Multi-user session storage: user_id -> {"items": [], "coupons": [], "cart_message": None}
user_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {"items": [], "coupons": [], "cart_message": None}
    return user_sessions[user_id]

def reset_session(user_id: int) -> None:
    user_sessions[user_id] = {"items": [], "coupons": [], "cart_message": None}

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
CVS_ACCOUNTS_FILE = "cvs_accounts.json"
cvs_accounts_db: List[Dict[str, Any]] = load_json_file(CVS_ACCOUNTS_FILE, [])

def save_cvs_accounts(data: List[Dict[str, Any]]) -> None:
    save_json_file(CVS_ACCOUNTS_FILE, data)

def get_cvs_account(query: str) -> Optional[Dict[str, Any]]:
    q = str(query).strip().lower()
    for acc in cvs_accounts_db:
        if str(acc.get("id")) == q:
            return acc
        if q in str(acc.get("name", "")).lower():
            return acc
        if q in str(acc.get("email", "")).lower():
            return acc
        if q in str(acc.get("phone", "")).lower():
            return acc
        if q in str(acc.get("extraCareNumber", "")).lower():
            return acc
    return None

FILTERS_FILE = "automod_filters.json"
MOD_CASES_FILE = "mod_cases.json"
MOD_NOTES_FILE = "mod_notes.json"

filters_db: Dict[str, List[str]] = load_json_file(FILTERS_FILE, {})
mod_cases_db: Dict[str, Any] = load_json_file(MOD_CASES_FILE, {"next_id": 1, "cases": []})
mod_notes_db: Dict[str, Dict[str, List[Dict[str, Any]]]] = load_json_file(MOD_NOTES_FILE, {})

# --- STRICT CHANNEL PROTECTION GUARDRAIL ---
def is_protected_channel(channel: Any) -> bool:
    """Returns True if the channel or its category is strictly protected (#form-automation)
    and must NEVER be touched, edited, nuked, moved, or deleted under any circumstance.
    """
    if channel is None:
        return False
    if isinstance(channel, str):
        name = channel
    else:
        name = getattr(channel, "name", "")
        # Also check parent category if applicable
        parent_cat = getattr(channel, "category", None)
        if parent_cat is not None and is_protected_channel(parent_cat):
            return True
    if not isinstance(name, str):
        return False
    clean_name = name.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
    return "formautomation" in clean_name

# --- TICKETS PERSISTENCE ---
TICKETS_FILE = "tickets_data.json"
tickets_db: Dict[str, Any] = load_json_file(TICKETS_FILE, {"counter": 0, "tickets": {}})

def save_tickets(data: Optional[Dict[str, Any]] = None) -> None:
    global tickets_db
    if data is not None:
        tickets_db = data
    save_json_file(TICKETS_FILE, tickets_db)

def get_user_active_ticket(guild_id: int, user_id: int) -> Optional[int]:
    """Returns channel_id if user has an active open ticket in guild, else None."""
    for ch_id_str, info in tickets_db.get("tickets", {}).items():
        if info.get("guild_id") == guild_id and info.get("owner_id") == user_id and info.get("status") == "open":
            try:
                return int(ch_id_str)
            except (ValueError, TypeError):
                continue
    return None

def create_ticket_record(guild_id: int, channel_id: int, owner_id: int, channel_name: str) -> Dict[str, Any]:
    tickets_db["counter"] = tickets_db.get("counter", 0) + 1
    num = tickets_db["counter"]
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "id": num,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "owner_id": owner_id,
        "channel_name": channel_name,
        "status": "open",
        "claimed_by": None,
        "created_at": now_iso
    }
    if "tickets" not in tickets_db:
        tickets_db["tickets"] = {}
    tickets_db["tickets"][str(channel_id)] = record
    save_tickets()
    return record

def claim_ticket_record(channel_id: int, staff_id: int) -> bool:
    ch_str = str(channel_id)
    if ch_str in tickets_db.get("tickets", {}):
        tickets_db["tickets"][ch_str]["claimed_by"] = staff_id
        save_tickets()
        return True
    return False

def close_ticket_record(channel_id: int) -> bool:
    ch_str = str(channel_id)
    if ch_str in tickets_db.get("tickets", {}):
        tickets_db["tickets"][ch_str]["status"] = "closed"
        tickets_db["tickets"][ch_str]["closed_at"] = datetime.now(timezone.utc).isoformat()
        save_tickets()
        return True
    return False

def update_ticket_status(channel_id: int, status: str) -> bool:
    ch_str = str(channel_id)
    if ch_str in tickets_db.get("tickets", {}):
        tickets_db["tickets"][ch_str]["status"] = status
        save_tickets()
        return True
    return False

def get_founder_role(guild: Optional[discord.Guild]) -> Optional[discord.Role]:
    if not guild:
        return None
    for r in guild.roles:
        if r.name.lower() in ("founder", "founders", "owner", "co-founder"):
            return r
    for r in guild.roles:
        if "founder" in r.name.lower():
            return r
    return None

def get_moderator_role(guild: Optional[discord.Guild]) -> Optional[discord.Role]:
    if not guild:
        return None
    for r in guild.roles:
        if r.name.lower() in ("moderator", "moderators", "mod", "mods"):
            return r
    for r in guild.roles:
        if "moderator" in r.name.lower() or "mod" in r.name.lower():
            return r
    return None

def get_staff_role(guild: Optional[discord.Guild]) -> Optional[discord.Role]:
    if not guild:
        return None
    for r in guild.roles:
        if r.name.lower() in ("staff", "support", "operator", "team"):
            return r
    for r in guild.roles:
        if "staff" in r.name.lower():
            return r
    return None

def is_staff_member(member: Optional[Any]) -> bool:
    """Checks if a user is server owner, administrator, moderator, or staff."""
    if member is None:
        return False
    guild = getattr(member, "guild", None)
    if guild and getattr(guild, "owner_id", None) == getattr(member, "id", None):
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms:
        if getattr(perms, "manage_channels", False) or getattr(perms, "administrator", False) or getattr(perms, "manage_messages", False):
            return True
    staff_roles = {"staff", "moderator", "moderators", "mod", "mods", "admin", "administrator", "operator", "founder", "founders", "owner", "co-founder"}
    roles = getattr(member, "roles", [])
    return any(getattr(r, "name", "").lower() in staff_roles for r in roles)

def is_admin_member(member: Optional[Any]) -> bool:
    """Checks if a user is server owner, bot owner, guild administrator,
    or holds a role designated as Founder, Owner, or Admin."""
    if member is None:
        return False
    guild = getattr(member, "guild", None)
    if guild and getattr(guild, "owner_id", None) == getattr(member, "id", None):
        return True
    if getattr(bot, "owner_id", None) and getattr(bot, "owner_id", None) == getattr(member, "id", None):
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms and getattr(perms, "administrator", False):
        return True
    admin_roles = {"admin", "administrator", "founder", "founders", "owner", "co-founder", "co founder", "head admin", "lead admin"}
    roles = getattr(member, "roles", [])
    for r in roles:
        r_name = getattr(r, "name", "").lower()
        if r_name in admin_roles or "founder" in r_name or "admin" in r_name:
            return True
    return False

def resolve_member_from_input(guild: Optional[discord.Guild], query: str) -> Optional[discord.Member]:
    """Resolves a guild member from mention (<@123>), user ID (123), or username / nickname."""
    if not guild or not query:
        return None
    cleaned = query.strip().lstrip("<@!").rstrip(">")
    if cleaned.isdigit():
        mem = guild.get_member(int(cleaned))
        if mem:
            return mem
    q_lower = query.strip().lower()
    for m in guild.members:
        if m.name.lower() == q_lower or m.display_name.lower() == q_lower:
            return m
    for m in guild.members:
        if q_lower in m.name.lower() or q_lower in m.display_name.lower():
            return m
    return None

def resolve_channel_from_input(guild: Optional[discord.Guild], query: str) -> Optional[discord.TextChannel]:
    """Resolves a guild text channel from mention (<#123>), channel ID (123), or channel name."""
    if not guild or not query:
        return None
    cleaned = query.strip().lstrip("<#").rstrip(">").strip()
    if cleaned.isdigit():
        ch = guild.get_channel(int(cleaned))
        if isinstance(ch, discord.TextChannel):
            return ch
    q_clean = query.strip().lower().lstrip("#").strip()
    for ch in guild.text_channels:
        if ch.name.lower() == q_clean:
            return ch
    q_alpha = re.sub(r'[^a-zA-Z0-9]', '', q_clean)
    if q_alpha:
        for ch in guild.text_channels:
            ch_alpha = re.sub(r'[^a-zA-Z0-9]', '', ch.name.lower())
            if ch_alpha == q_alpha:
                return ch
    for ch in guild.text_channels:
        if q_clean in ch.name.lower():
            return ch
    if q_alpha:
        for ch in guild.text_channels:
            ch_alpha = re.sub(r'[^a-zA-Z0-9]', '', ch.name.lower())
            if q_alpha in ch_alpha:
                return ch
    return None

async def resolve_user_or_member(guild: Optional[discord.Guild], query: str) -> Optional[Union[discord.Member, discord.User]]:
    """Resolves a member or user from guild members, or fetches user directly via bot API."""
    if not query:
        return None
    if guild:
        mem = resolve_member_from_input(guild, query)
        if mem:
            return mem
    cleaned = query.strip().lstrip("<@!").rstrip(">").strip()
    if cleaned.isdigit():
        try:
            return await bot.fetch_user(int(cleaned))
        except Exception:
            pass
    return None

def get_channel_mention(guild: Optional[discord.Guild], name: str, fallback: Optional[str] = None) -> str:
    """Finds a channel by name or partial match and returns a clickable <#channel_id> mention."""
    if not guild:
        return fallback or f"#{name}"
    ch = discord.utils.get(guild.channels, name=name)
    if ch:
        return ch.mention
    clean = re.sub(r'^[^\w\-]+', '', name).strip("-").lower()
    for c in guild.channels:
        clean_c = re.sub(r'^[^\w\-]+', '', c.name).strip("-").lower()
        if clean and (clean == clean_c or clean in clean_c or clean_c in clean):
            return c.mention
    return fallback or f"#{name}"

# --- COMPLETED ORDER STATS TRACKER ---
def record_completed_order(
    guild_id: int,
    ticket_id: int,
    channel_id: int,
    channel_name: str,
    customer_id: int,
    customer_name: str,
    completed_by_id: int,
    completed_by_name: str,
    brand: str,
    amount: float,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    if "completed_orders" not in tickets_db:
        tickets_db["completed_orders"] = []
    
    order_num = len(tickets_db["completed_orders"]) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "order_id": order_num,
        "ticket_id": ticket_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "guild_id": guild_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "completed_by_id": completed_by_id,
        "completed_by_name": completed_by_name,
        "brand": brand,
        "amount": round(float(amount), 2),
        "notes": notes or "",
        "completed_at": now_iso
    }
    tickets_db["completed_orders"].append(record)
    save_tickets()
    return record

def remove_completed_order(order_or_ticket_id: int, guild_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    orders = tickets_db.get("completed_orders", [])
    for idx, order in enumerate(orders):
        if guild_id and order.get("guild_id") and order.get("guild_id") != guild_id:
            continue
        if order.get("order_id") == order_or_ticket_id or order.get("ticket_id") == order_or_ticket_id:
            removed = orders.pop(idx)
            save_tickets()
            return removed
    return None

def clear_completed_orders(guild_id: Optional[int] = None) -> int:
    orders = tickets_db.get("completed_orders", [])
    if guild_id is None:
        count = len(orders)
        tickets_db["completed_orders"] = []
        save_tickets()
        return count
    else:
        new_orders = [o for o in orders if o.get("guild_id") and o.get("guild_id") != guild_id]
        cleared = len(orders) - len(new_orders)
        tickets_db["completed_orders"] = new_orders
        save_tickets()
        return cleared

# --- STAFF PAYMENT PROFILE CACHE ---
STAFF_PAYMENT_FILE = "staff_payment_data.json"
staff_payment_db: Dict[str, Any] = load_json_file(STAFF_PAYMENT_FILE, {})

def save_staff_payment(staff_id: int, cashapp: Optional[str] = None, venmo: Optional[str] = None) -> None:
    sid = str(staff_id)
    if sid not in staff_payment_db:
        staff_payment_db[sid] = {}
    if cashapp:
        staff_payment_db[sid]["cashapp"] = cashapp.strip().lstrip("$")
    if venmo:
        staff_payment_db[sid]["venmo"] = venmo.strip().lstrip("@")
    save_json_file(STAFF_PAYMENT_FILE, staff_payment_db)

def get_staff_payment(staff_id: int) -> Dict[str, str]:
    return staff_payment_db.get(str(staff_id), {})

def build_order_stats_embed(guild: Optional[discord.Guild]) -> discord.Embed:
    orders = tickets_db.get("completed_orders", [])
    if guild:
        guild_orders = [o for o in orders if o.get("guild_id") == guild.id or not o.get("guild_id")]
    else:
        guild_orders = orders

    total_count = len(guild_orders)
    total_rev = sum(o.get("amount", 0.0) for o in guild_orders)
    taco_count = sum(1 for o in guild_orders if "taco" in o.get("brand", "").lower())
    pizza_count = sum(1 for o in guild_orders if "pizza" in o.get("brand", "").lower())
    other_count = total_count - (taco_count + pizza_count)

    gname = guild.name if guild else "AIO Bot"
    embed = discord.Embed(
        title="📊 Completed Orders & Sales Tracker",
        description=f"Summary of all fulfilled customer orders in **{gname}**:",
        color=COLOR_SUCCESS
    )
    embed.add_field(name="🏆 Total Completed", value=f"**{total_count} orders**", inline=True)
    embed.add_field(name="💰 Total Revenue", value=f"**${total_rev:.2f}**", inline=True)
    embed.add_field(
        name="🏷️ Brand Breakdown",
        value=f"🌮 Taco Bell: **{taco_count}**\n🍕 Pizza Hut: **{pizza_count}**" + (f"\n✨ Other: **{other_count}**" if other_count > 0 else ""),
        inline=True
    )

    if guild_orders:
        recent = guild_orders[-8:]
        lines = []
        for o in reversed(recent):
            oid = o.get("order_id", o.get("ticket_id", "?"))
            brand = o.get("brand", "Order")
            amt = o.get("amount", 0.0)
            cust = o.get("customer_name") or f"<@{o.get('customer_id', '')}>"
            ts = ""
            if o.get("completed_at"):
                try:
                    dt = datetime.fromisoformat(o["completed_at"])
                    ts = f" · <t:{int(dt.timestamp())}:R>"
                except Exception:
                    pass
            lines.append(f"`#{oid:02d}` **{brand}** — **${amt:.2f}** ({cust}){ts}")
        embed.add_field(name="📋 Recent Completed Orders", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Recent Completed Orders", value="*No completed orders tracked yet.*", inline=False)

    embed.set_footer(text="Staff: Run /clearorder <id> to remove an order, or /orderstats reset to clear test data")
    return embed

def create_invoice_embed(
    author: Union[discord.Member, discord.User],
    channel: Any,
    price_str: str,
    cashapp: Optional[str] = None,
    venmo: Optional[str] = None,
    item: Optional[str] = None,
    customer: Optional[discord.Member] = None
) -> Tuple[Optional[discord.Embed], Optional[discord.Member], Optional[str]]:
    clean_price_str = price_str.replace("$", "").replace(",", "").strip()
    try:
        val = float(clean_price_str)
        price_formatted = f"${val:.2f}"
    except ValueError:
        return None, None, "❌ Please enter a valid number for price (e.g. `10.00` or `$15`)."

    saved_handles = get_staff_payment(author.id)
    ca_handle = (cashapp.strip().lstrip("$") if cashapp else saved_handles.get("cashapp", "")).strip()
    vm_handle = (venmo.strip().lstrip("@") if venmo else saved_handles.get("venmo", "")).strip()

    if cashapp or venmo:
        save_staff_payment(author.id, cashapp=ca_handle if ca_handle else None, venmo=vm_handle if vm_handle else None)

    guild = getattr(channel, "guild", None)
    t_info = tickets_db.get("tickets", {}).get(str(getattr(channel, "id", 0)), {})
    ticket_id = t_info.get("id")
    target_cust = customer
    if not target_cust and t_info.get("owner_id") and guild:
        target_cust = guild.get_member(t_info["owner_id"])

    item_desc = item.strip() if item else None
    if not item_desc:
        ch_name = getattr(channel, "name", "")
        if "taco" in ch_name.lower():
            item_desc = "Taco Bell Preloaded Account(s)"
        elif "pizza" in ch_name.lower():
            item_desc = "Pizza Hut Preloaded Account(s)"
        else:
            item_desc = "Fast Food Rewards / Preloaded Account"

    ch_id_str = str(getattr(channel, "id", 0))
    if ch_id_str in tickets_db.get("tickets", {}):
        tickets_db["tickets"][ch_id_str]["invoice_amount"] = val
        tickets_db["tickets"][ch_id_str]["invoice_item"] = item_desc
        tickets_db["tickets"][ch_id_str]["status"] = "invoiced"
        save_tickets()

    embed = discord.Embed(
        title="🧾 Official Payment Invoice",
        description=(
            f"Payment requested for {target_cust.mention if target_cust else 'this order'}!\n\n"
            "Please send payment using either **Cash App** or **Venmo** below to complete your order."
        ),
        color=0xf1c40f
    )
    embed.add_field(name="💵 Amount Due", value=f"**{price_formatted}**", inline=True)
    embed.add_field(name="📦 Item", value=f"**{item_desc}**", inline=True)
    if ticket_id:
        embed.add_field(name="🎫 Ticket", value=f"`#{ticket_id:04d}`", inline=True)

    pay_methods = []
    if ca_handle:
        pay_methods.append(f"• **Cash App:** [${ca_handle}](https://cash.app/${ca_handle}) · ` ${ca_handle} `")
    if vm_handle:
        pay_methods.append(f"• **Venmo:** [@{vm_handle}](https://venmo.com/u/{vm_handle}) · ` @{vm_handle} `")
    if not pay_methods:
        pay_methods.append("• *Contact staff in this channel for payment handle details.*")
    embed.add_field(name="💳 Payment Handles", value="\n".join(pay_methods), inline=False)

    instructions = (
        f"1️⃣ Send exactly **{price_formatted}** to the handle listed above.\n"
        "2️⃣ In the payment note, include your **Discord username** or ticket number.\n"
        "3️⃣ Reply in this channel once sent (or upload a screenshot).\n"
        "4️⃣ Staff will verify payment and immediately fulfill your order!"
    )
    embed.add_field(name="📌 Instructions", value=instructions, inline=False)
    embed.add_field(name="⏳ Status", value="🟡 Awaiting Payment", inline=True)
    author_name = getattr(author, "display_name", str(author))
    embed.set_footer(text=f"Issued by {author_name} • Staff: run /paid once payment arrives")

    return embed, target_cust, None

async def fix_server_roles(guild: discord.Guild) -> Dict[str, Any]:
    """
    Consolidates duplicate Moderator roles into a single Moderator role,
    migrates members, removes redundant Staff role, ensures Founder role
    is hoisted and assigned to the server owner, unhoists bot roles so
    the bot never displays above the owner, and adjusts hierarchy positions.
    """
    results = {
        "moderators_merged": 0,
        "staff_role_removed": False,
        "primary_mod_role": None,
        "founder_role": None,
        "bot_roles_unhoisted": 0,
        "bot_roles_stripped": 0,
        "bot_is_top": False,
        "logs": []
    }
    if not guild or not getattr(guild, "me", None):
        return results

    can_manage = getattr(guild.me, "guild_permissions", None) and guild.me.guild_permissions.manage_roles
    
    # 1. Identify all Moderator roles
    mod_roles = [
        r for r in guild.roles 
        if r.name.strip().lower() in ("moderator", "moderators", "mod", "mods")
    ]
    if not mod_roles:
        mod_roles = [r for r in guild.roles if "moderator" in r.name.strip().lower()]

    primary_mod = None
    if mod_roles:
        mod_roles.sort(key=lambda r: (len(getattr(r, "members", [])), getattr(r, "position", 0)), reverse=True)
        primary_mod = mod_roles[0]
        results["primary_mod_role"] = primary_mod.name

        for dup in mod_roles[1:]:
            results["logs"].append(f"Found duplicate Moderator role: {dup.name} (ID {dup.id})")
            if can_manage and guild.me.top_role > dup:
                for m in getattr(dup, "members", []):
                    try:
                        if primary_mod not in m.roles and guild.me.top_role > primary_mod:
                            await m.add_roles(primary_mod, reason="Consolidating duplicate Moderator roles")
                    except Exception as e:
                        print(f"⚠️ Error migrating member {m} to primary mod role: {e}", file=sys.stderr)
                try:
                    await dup.delete(reason="Deleting duplicate Moderator role")
                    results["moderators_merged"] += 1
                    results["logs"].append(f"Deleted duplicate Moderator role {dup.name}")
                except Exception as e:
                    print(f"⚠️ Error deleting duplicate Moderator role {dup.name}: {e}", file=sys.stderr)

        if can_manage and guild.me.top_role > primary_mod:
            try:
                updates = {}
                if primary_mod.name != "Moderator":
                    updates["name"] = "Moderator"
                if not primary_mod.mentionable:
                    updates["mentionable"] = True
                if not primary_mod.hoist:
                    updates["hoist"] = True
                if updates:
                    await primary_mod.edit(**updates, reason="Standardizing primary Moderator role")
            except Exception as e:
                print(f"⚠️ Error updating primary Moderator role: {e}", file=sys.stderr)

    # 2. Clean up redundant "Staff" role
    staff_roles = [
        r for r in guild.roles 
        if r.name.strip().lower() in ("staff",)
    ]
    for s_role in staff_roles:
        results["logs"].append(f"Found extra Staff role: {s_role.name} (ID {s_role.id})")
        if can_manage and guild.me.top_role > s_role:
            if primary_mod:
                for m in getattr(s_role, "members", []):
                    try:
                        if primary_mod not in m.roles and guild.me.top_role > primary_mod:
                            await m.add_roles(primary_mod, reason="Migrating Staff role members to Moderator")
                    except Exception:
                        pass
            try:
                await s_role.delete(reason="Removing redundant Staff role per user request")
                results["staff_role_removed"] = True
                results["logs"].append(f"Deleted extra Staff role {s_role.name}")
            except Exception as e:
                print(f"⚠️ Error deleting redundant Staff role {s_role.name}: {e}", file=sys.stderr)

    # 3. Ensure Founder role is properly set & assigned to server owner
    founder_role = get_founder_role(guild)
    if not founder_role and can_manage:
        try:
            founder_role = await guild.create_role(
                name="Founder",
                color=discord.Color.gold(),
                hoist=True,
                mentionable=True,
                reason="Created Founder role during role audit"
            )
            results["logs"].append("Created new Founder role")
        except Exception as e:
            print(f"⚠️ Could not create Founder role: {e}", file=sys.stderr)

    if founder_role:
        results["founder_role"] = founder_role.name
        if can_manage and guild.me.top_role > founder_role:
            try:
                updates = {}
                if not founder_role.mentionable:
                    updates["mentionable"] = True
                if not founder_role.hoist:
                    updates["hoist"] = True
                if updates:
                    await founder_role.edit(**updates, reason="Ensuring Founder role is hoisted & mentionable")
            except Exception:
                pass

        if getattr(guild, "owner", None) and can_manage and guild.me.top_role > founder_role:
            if founder_role not in getattr(guild.owner, "roles", []):
                try:
                    await guild.owner.add_roles(founder_role, reason="Assigned Founder role to server owner")
                    results["logs"].append(f"Assigned Founder role to server owner ({guild.owner})")
                except Exception as e:
                    print(f"⚠️ Could not assign Founder role to owner: {e}", file=sys.stderr)

    # 4. Demote & Unhoist Bot Roles so Bot is NEVER displayed above Owner/Founder in member list
    if can_manage:
        for r in getattr(guild.me, "roles", []):
            if r.is_default():
                continue
            r_name = r.name.strip().lower()
            if r_name in ("founder", "owner", "admin", "administrator", "moderator", "mod", "staff", "co-founder"):
                try:
                    await guild.me.remove_roles(r, reason="Bot should not possess founder or staff roles")
                    results["bot_roles_stripped"] += 1
                    results["logs"].append(f"Stripped {r.name} from bot")
                except Exception as e:
                    print(f"⚠️ Error removing role {r.name} from bot: {e}", file=sys.stderr)

        for r in getattr(guild.me, "roles", []):
            if r.is_default():
                continue
            if getattr(r, "hoist", False):
                try:
                    await r.edit(hoist=False, reason="Unhoist bot role so it does not display above server owner")
                    results["bot_roles_unhoisted"] += 1
                    results["logs"].append(f"Unhoisted bot role {r.name}")
                except Exception as e:
                    print(f"⚠️ Notice unhoisting bot role {r.name}: {e}", file=sys.stderr)

    # 5. Elevate Founder role position as high as Discord API allows
    if founder_role and can_manage:
        try:
            target_pos = max(1, getattr(guild.me.top_role, "position", 1) - 1)
            if getattr(founder_role, "position", 0) < target_pos:
                await guild.edit_role_positions({founder_role: target_pos}, reason="Elevating Founder role to highest possible position")
                results["logs"].append(f"Elevated {founder_role.name} position")
        except Exception as e:
            print(f"⚠️ Role position elevation notice: {e}", file=sys.stderr)

    owner_top = getattr(guild.owner, "top_role", None) if getattr(guild, "owner", None) else None
    bot_top = getattr(guild.me, "top_role", None)
    if owner_top and bot_top:
        results["bot_is_top"] = (getattr(bot_top, "position", 0) >= getattr(owner_top, "position", 0))

    return results

def format_ticket_transcript(messages: List[discord.Message], ticket_id: int, owner_id: int) -> str:
    lines = [
        "============================================================",
        "               AIO BOT TICKET TRANSCRIPT",
        f"Ticket Number: #{ticket_id:04d}",
        f"Author ID:     {owner_id}",
        f"Exported:      {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "============================================================",
        ""
    ]
    for msg in messages:
        ts = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
        author_display = getattr(msg.author, "display_name", str(msg.author))
        author_name = f"{author_display} ({msg.author})" if author_display != str(msg.author) else str(msg.author)
        content = getattr(msg, "clean_content", getattr(msg, "content", "")) or "(No text content)"
        lines.append(f"[{ts}] {author_name}: {content}")
        if getattr(msg, "attachments", None):
            for att in msg.attachments:
                lines.append(f"    [Attachment: {att.filename} ({att.url})]")
    lines.append("\n=== END OF TRANSCRIPT ===")
    return "\n".join(lines)

def save_session_channels(data: Dict[str, int]) -> None:
    save_json_file(SESSION_CHANNELS_FILE, data)

def save_warnings(data: Dict[str, List[Dict[str, Any]]]) -> None:
    save_json_file(WARNINGS_FILE, data)

def save_filters(data: Dict[str, List[str]]) -> None:
    save_json_file(FILTERS_FILE, data)

def save_mod_cases(data: Dict[str, Any]) -> None:
    save_json_file(MOD_CASES_FILE, data)

def save_mod_notes(data: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> None:
    save_json_file(MOD_NOTES_FILE, data)

def log_mod_case(guild_id: int, action: str, target: str, moderator: str, reason: str, details: Optional[str] = None) -> int:
    case_id = mod_cases_db.get("next_id", 1)
    mod_cases_db["next_id"] = case_id + 1
    case_entry = {
        "case_id": case_id,
        "guild_id": str(guild_id),
        "action": action,
        "target": target,
        "moderator": moderator,
        "reason": reason,
        "details": details or "None",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    }
    if "cases" not in mod_cases_db:
        mod_cases_db["cases"] = []
    mod_cases_db["cases"].append(case_entry)
    save_mod_cases(mod_cases_db)
    return case_id

def get_filter_words(guild_id: int) -> List[str]:
    return filters_db.get(str(guild_id), [])

def add_filter_word(guild_id: int, word: str) -> bool:
    gid = str(guild_id)
    if gid not in filters_db:
        filters_db[gid] = []
    w = word.strip().lower()
    if w and w not in filters_db[gid]:
        filters_db[gid].append(w)
        save_filters(filters_db)
        return True
    return False

def remove_filter_word(guild_id: int, word: str) -> bool:
    gid = str(guild_id)
    if gid not in filters_db:
        return False
    w = word.strip().lower()
    if w in filters_db[gid]:
        filters_db[gid].remove(w)
        save_filters(filters_db)
        return True
    return False

def add_mod_note(guild_id: int, user_id: int, moderator: str, note: str) -> None:
    gid = str(guild_id)
    uid = str(user_id)
    if gid not in mod_notes_db:
        mod_notes_db[gid] = {}
    if uid not in mod_notes_db[gid]:
        mod_notes_db[gid][uid] = []
    mod_notes_db[gid][uid].append({
        "moderator": moderator,
        "note": note,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    })
    save_mod_notes(mod_notes_db)

def get_mod_notes(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    return mod_notes_db.get(str(guild_id), {}).get(str(user_id), [])

def clear_mod_notes(guild_id: int, user_id: int) -> int:
    gid = str(guild_id)
    uid = str(user_id)
    if gid in mod_notes_db and uid in mod_notes_db[gid]:
        count = len(mod_notes_db[gid][uid])
        del mod_notes_db[gid][uid]
        save_mod_notes(mod_notes_db)
        return count
    return 0


# --- ECONOMY & COIN SYSTEM ---
ECONOMY_FILE = "economy_data.json"
DEFAULT_STARTING_COINS = 1000
DAILY_REWARD_COINS = 250

economy_db: Dict[str, Any] = load_json_file(ECONOMY_FILE, {})

def save_economy(data: Dict[str, Any]) -> None:
    save_json_file(ECONOMY_FILE, data)

def get_user_coins(user_id: int) -> int:
    uid = str(user_id)
    if uid not in economy_db:
        economy_db[uid] = {
            "coins": DEFAULT_STARTING_COINS,
            "last_daily": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        save_economy(economy_db)
    return int(economy_db[uid].get("coins", DEFAULT_STARTING_COINS))

def add_user_coins(user_id: int, amount: int) -> int:
    uid = str(user_id)
    current = get_user_coins(user_id)
    new_balance = max(0, current + int(amount))
    economy_db[uid]["coins"] = new_balance
    save_economy(economy_db)
    return new_balance

def deduct_user_coins(user_id: int, amount: int) -> bool:
    uid = str(user_id)
    current = get_user_coins(user_id)
    amt = int(amount)
    if amt <= 0:
        return True
    if current < amt:
        return False
    economy_db[uid]["coins"] = current - amt
    save_economy(economy_db)
    return True

def claim_daily_coins(user_id: int) -> Tuple[bool, int, Optional[int]]:
    """
    Returns (success, reward_or_current_bal, seconds_remaining)
    """
    uid = str(user_id)
    _ = get_user_coins(user_id)
    user_record = economy_db[uid]
    last_daily = user_record.get("last_daily")
    now = datetime.now(timezone.utc)

    if last_daily:
        try:
            last_time = datetime.fromisoformat(last_daily)
            diff = now - last_time
            if diff.total_seconds() < 86400:
                seconds_remaining = int(86400 - diff.total_seconds())
                return False, int(user_record.get("coins", DEFAULT_STARTING_COINS)), seconds_remaining
        except Exception:
            pass

    user_record["coins"] = int(user_record.get("coins", DEFAULT_STARTING_COINS)) + DAILY_REWARD_COINS
    user_record["last_daily"] = now.isoformat()
    save_economy(economy_db)
    return True, DAILY_REWARD_COINS, None

def transfer_user_coins(from_user_id: int, to_user_id: int, amount: int) -> Tuple[bool, str]:
    if from_user_id == to_user_id:
        return False, "You cannot transfer coins to yourself!"
    if amount <= 0:
        return False, "Transfer amount must be greater than 0!"
    if not deduct_user_coins(from_user_id, amount):
        return False, f"Insufficient balance! You do not have {amount:,} 🪙 coins."
    add_user_coins(to_user_id, amount)
    return True, "Transfer successful!"

def get_coin_leaderboard(limit: int = 10) -> List[Tuple[int, int]]:
    records = []
    for uid, data in economy_db.items():
        try:
            records.append((int(uid), int(data.get("coins", DEFAULT_STARTING_COINS))))
        except ValueError:
            continue
    records.sort(key=lambda x: x[1], reverse=True)
    return records[:limit]


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


# 3.5 GAME ENGINES & CONSTANTS
CARD_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
    'J': 10, 'Q': 10, 'K': 10, 'A': 11
}
CARD_SUITS = ['♠', '♥', '♦', '♣']

def calculate_hand_value(hand: List[str]) -> int:
    val = 0
    aces = 0
    for card in hand:
        rank = card[:-1]
        val += CARD_VALUES.get(rank, 0)
        if rank == 'A':
            aces += 1
    while val > 21 and aces > 0:
        val -= 10
        aces -= 1
    return val

def create_shuffled_deck() -> List[str]:
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [f"{r}{s}" for r in ranks for s in CARD_SUITS]
    secrets.SystemRandom().shuffle(deck)
    return deck

CONNECT4_ROWS = 6
CONNECT4_COLS = 7

def create_connect4_board() -> List[List[str]]:
    return [["⚪" for _ in range(CONNECT4_COLS)] for _ in range(CONNECT4_ROWS)]

def render_connect4_board(board: List[List[str]]) -> str:
    rows = []
    for r in board:
        rows.append("".join(r))
    rows.append("1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣")
    return "\n".join(rows)

def drop_piece(board: List[List[str]], col: int, piece: str) -> Optional[int]:
    if col < 0 or col >= CONNECT4_COLS:
        return None
    for r in range(CONNECT4_ROWS - 1, -1, -1):
        if board[r][col] == "⚪":
            board[r][col] = piece
            return r
    return None

def check_connect4_win(board: List[List[str]], piece: str) -> bool:
    # Horizontal
    for r in range(CONNECT4_ROWS):
        for c in range(CONNECT4_COLS - 3):
            if all(board[r][c+i] == piece for i in range(4)):
                return True
    # Vertical
    for r in range(CONNECT4_ROWS - 3):
        for c in range(CONNECT4_COLS):
            if all(board[r+i][c] == piece for i in range(4)):
                return True
    # Diagonal Down-Right
    for r in range(CONNECT4_ROWS - 3):
        for c in range(CONNECT4_COLS - 3):
            if all(board[r+i][c+i] == piece for i in range(4)):
                return True
    # Diagonal Up-Right
    for r in range(3, CONNECT4_ROWS):
        for c in range(CONNECT4_COLS - 3):
            if all(board[r-i][c+i] == piece for i in range(4)):
                return True
    return False

def is_connect4_full(board: List[List[str]]) -> bool:
    return all(board[0][c] != "⚪" for c in range(CONNECT4_COLS))

TRIVIA_QUESTIONS = {
    "general": [
        {"q": "What is the capital of Australia?", "options": ["Sydney", "Melbourne", "Canberra", "Brisbane"], "ans": 2, "info": "Canberra was chosen as the capital in 1908."},
        {"q": "How many continents are there on Earth?", "options": ["5", "6", "7", "8"], "ans": 2, "info": "The 7 continents are Africa, Antarctica, Asia, Europe, North America, Oceania, and South America."},
        {"q": "What is the longest river in the world?", "options": ["Amazon", "Nile", "Mississippi", "Yangtze"], "ans": 1, "info": "The Nile River spans over 6,650 km."},
        {"q": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "ans": 1, "info": "Mars appears red due to iron oxide on its surface."}
    ],
    "tech": [
        {"q": "Who created the Python programming language?", "options": ["Linus Torvalds", "Guido van Rossum", "Dennis Ritchie", "James Gosling"], "ans": 1, "info": "Guido van Rossum released Python in 1991."},
        {"q": "What does CPU stand for?", "options": ["Central Process Unit", "Central Processing Unit", "Computer Personal Unit", "Control Power Unit"], "ans": 1, "info": "The CPU is often called the brains of the computer."},
        {"q": "Which year was Git released by Linus Torvalds?", "options": ["2001", "2005", "2008", "2011"], "ans": 1, "info": "Git was created in 2005 to manage Linux kernel development."},
        {"q": "What is the default port for HTTPS traffic?", "options": ["80", "8080", "443", "22"], "ans": 2, "info": "Port 443 is the standard port for secure HTTPS web traffic."}
    ],
    "gaming": [
        {"q": "In Minecraft, what block is needed to build a Nether Portal?", "options": ["Bedrock", "Obsidian", "Crying Obsidian", "Netherite"], "ans": 1, "info": "A minimum of 10 Obsidian blocks is required."},
        {"q": "What is the name of Mario's dinosaur companion?", "options": ["Bowser", "Toad", "Yoshi", "Koopa"], "ans": 2, "info": "Yoshi made his debut in Super Mario World (1990)."},
        {"q": "Which game popularized the Battle Royale genre in 2017?", "options": ["Fortnite", "PUBG", "Apex Legends", "H1Z1"], "ans": 1, "info": "PUBG sparked the global Battle Royale wave in 2017."},
        {"q": "What is the highest competitive rank in Valorant?", "options": ["Immortal", "Challenger", "Radiant", "Master"], "ans": 2, "info": "Radiant is the top rank in Valorant."}
    ],
    "science": [
        {"q": "What is the chemical symbol for Gold?", "options": ["Go", "Gd", "Au", "Ag"], "ans": 2, "info": "Au comes from the Latin word for gold, 'Aurum'."},
        {"q": "What is the powerhouse of the cell?", "options": ["Nucleus", "Ribosome", "Mitochondria", "Endoplasmic Reticulum"], "ans": 2, "info": "Mitochondria generate most of the cellular ATP energy."},
        {"q": "What is the speed of light in a vacuum (approx)?", "options": ["300,000 km/s", "150,000 km/s", "30,000 km/s", "1,000,000 km/s"], "ans": 0, "info": "Light travels at ~299,792 km/s in a vacuum."},
        {"q": "Which gas makes up approximately 78% of Earth's atmosphere?", "options": ["Oxygen", "Carbon Dioxide", "Nitrogen", "Argon"], "ans": 2, "info": "Nitrogen constitutes ~78% of Earth's atmosphere."}
    ]
}

SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "💎", "7️⃣"]
SLOT_PAYOUTS = {
    "7️⃣7️⃣7️⃣": (50.0, "JACKPOT! 🏆 50x Payout"),
    "💎💎💎": (25.0, "Diamond Win! 💎 25x Payout"),
    "⭐⭐⭐": (15.0, "Super Star! ⭐ 15x Payout"),
    "🔔🔔🔔": (10.0, "Triple Bells! 🔔 10x Payout"),
    "🍇🍇🍇": (5.0, "Fruit Burst! 🍇 5x Payout"),
    "🍋🍋🍋": (4.0, "Triple Lemon! 🍋 4x Payout"),
    "🍒🍒🍒": (3.0, "Cherry Trio! 🍒 3x Payout"),
}

def roll_3x3_slots() -> List[List[str]]:
    """
    Generates a 3x3 grid of slot symbols using Python's secrets module (cryptographic PRNG)
    for 100% fair, unbiased, uniform independent random probability on every cell.
    """
    return [[secrets.choice(SLOT_SYMBOLS) for _ in range(3)] for _ in range(3)]

def format_3x3_grid(grid: List[List[str]], active_cols: int = 3) -> str:
    """
    Renders the 3x3 grid with active_cols (0..3) revealing columns left-to-right.
    Unrevealed columns show spinning 🌀 reels.
    """
    lines = []
    for r in range(3):
        row_cells = []
        for c in range(3):
            if c < active_cols:
                row_cells.append(grid[r][c])
            else:
                row_cells.append("🌀")
        indicator = " ◀" if r == 1 else "  "
        lines.append(f"`[ {row_cells[0]} │ {row_cells[1]} │ {row_cells[2]} ]`{indicator}")
    return "\n".join(lines)

def evaluate_3x3_slots(grid: List[List[str]], stake: int) -> Tuple[int, List[str], str]:
    """
    Evaluates 5 standard paylines in the 3x3 grid:
    - 3 Horizontal rows (Top, Center, Bottom)
    - 2 Diagonals (Top-Left to Bottom-Right, Bottom-Left to Top-Right)
    Returns (total_winnings, hit_descriptions, summary_title)
    """
    lines = [
        ("Center Row", [grid[1][0], grid[1][1], grid[1][2]]),
        ("Top Row", [grid[0][0], grid[0][1], grid[0][2]]),
        ("Bottom Row", [grid[2][0], grid[2][1], grid[2][2]]),
        ("Diagonal ↘", [grid[0][0], grid[1][1], grid[2][2]]),
        ("Diagonal ↗", [grid[2][0], grid[1][1], grid[0][2]]),
    ]

    total_mult = 0.0
    hits = []

    # Check for Full-Board 9-of-a-kind Jackpot Bonus
    flat = [cell for row in grid for cell in row]
    if len(set(flat)) == 1:
        full_symbol = flat[0]
        full_mult = 100.0
        winnings = int(stake * full_mult)
        hits.append(f"🌟 **FULL BOARD JACKPOT!** 9x {full_symbol} (100x Payout)")
        return winnings, hits, f"FULL BOARD 9x {full_symbol} (100x)"

    for name, symbols in lines:
        combo = f"{symbols[0]}{symbols[1]}{symbols[2]}"
        if combo in SLOT_PAYOUTS:
            mult, title = SLOT_PAYOUTS[combo]
            total_mult += mult
            hits.append(f"{name}: **{title}** (+{int(stake * mult):,} 🪙)")
        elif symbols[0] == symbols[1] or symbols[1] == symbols[2] or symbols[0] == symbols[2]:
            if name == "Center Row":
                total_mult += 1.5
                hits.append(f"Center Row: **Pair Match (1.5x)** (+{int(stake * 1.5):,} 🪙)")

    if total_mult > 0:
        total_winnings = int(stake * total_mult)
        summary_title = hits[0].split(":")[1].strip() if hits else "Multi-Line Win"
        return total_winnings, hits, summary_title
    else:
        return 0, [], "Miss"



# --- CODE 128 BARCODE GENERATOR & CVS ACCOUNT FORMATTER ---

CODE128_PATTERNS = [
    '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312',
    '132212', '221213', '221312', '231212', '112232', '122132', '122231', '113222',
    '123122', '123221', '223211', '221132', '221231', '213212', '223112', '312131',
    '311222', '321122', '321221', '312212', '322112', '322211', '212123', '212321',
    '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
    '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121',
    '313121', '211331', '231131', '213113', '213311', '213131', '311123', '311321',
    '331121', '312113', '312311', '332111', '314111', '221411', '431111', '111224',
    '111422', '121124', '121421', '141122', '141221', '112214', '112412', '122114',
    '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
    '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112',
    '421211', '212141', '214121', '412121', '111143', '111341', '131141', '114113',
    '114311', '411113', '411311', '113141', '114131', '311141', '411131', '211412',
    '211214', '211232', '2331112'
]

def generate_code128_barcode_bytes(data: str, height: int = 100, bar_width: int = 3) -> io.BytesIO:
    from PIL import Image, ImageDraw
    clean_data = re.sub(r'[^A-Za-z0-9]', '', str(data)).upper()
    if not clean_data:
        clean_data = "0000000000"

    start_code = 104  # Start Code 128B
    stop_code = 106
    values = [start_code]
    for char in clean_data:
        val = ord(char) - 32
        values.append(val if 0 <= val <= 95 else 0)

    checksum = values[0]
    for i, val in enumerate(values[1:], 1):
        checksum += i * val
    checksum %= 103
    values.append(checksum)
    values.append(stop_code)

    bit_pattern = ''
    for v in values:
        pattern = CODE128_PATTERNS[v]
        is_bar = True
        for digit in pattern:
            width = int(digit)
            bit_pattern += ('1' if is_bar else '0') * width
            is_bar = not is_bar

    quiet_zone = 25 * bar_width
    img_width = len(bit_pattern) * bar_width + 2 * quiet_zone
    img_height = height + 45

    img = Image.new('RGB', (img_width, img_height), color='white')
    draw = ImageDraw.Draw(img)

    x = quiet_zone
    for bit in bit_pattern:
        if bit == '1':
            draw.rectangle([x, 15, x + bar_width - 1, 15 + height], fill='black')
        x += bar_width

    formatted_text = " ".join([clean_data[i:i+4] for i in range(0, len(clean_data), 4)])
    draw.text((img_width // 2, 22 + height), formatted_text, fill='black', anchor='mm')

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer

# 4. DISCORD UI MODALS & INTERACTIVE VIEWS



def format_account_card(acc: Dict[str, Any]) -> Tuple[discord.Embed, discord.File]:
    raw_card = str(acc.get("extraCareNumber", "0000000000")).strip()
    barcode_buffer = generate_code128_barcode_bytes(raw_card)
    file = discord.File(fp=barcode_buffer, filename="cvs_barcode.png")

    name = acc.get("name", "Account Holder")
    acc_id = acc.get("id", 1)
    email = acc.get("email", "")
    pwd = acc.get("password", "")

    coupon_link = "https://www.cvs.com/extracare/deals-and-rewards"
    extracare_link = "https://www.cvs.com/extracare/home"
    deals_link = "https://www.cvs.com/deals/coupons"

    embed = discord.Embed(
        title=f"💳 CVS ExtraCare® Card — #{acc_id} {name}",
        description=(
            f"🎯 **[Open Deals & Rewards (Send to Card)]({coupon_link})** • 🎟️ **[Digital Coupons]({deals_link})** • 💰 **[Dashboard]({extracare_link})**\n"
            f"*Scannable barcode generated below for register & self-checkout scanners.*"
        ),
        color=COLOR_PRIMARY
    )
    embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

    formatted_card = " ".join([raw_card[i:i+4] for i in range(0, len(raw_card), 4)])
    phone = acc.get("phone", "")
    phone_fmt = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}" if len(phone) == 10 else (phone or "—")

    embed.add_field(name="🔢 ExtraCare Number", value=f"`{formatted_card}`", inline=True)
    embed.add_field(name="👤 Cardholder", value=f"**{name}**", inline=True)
    embed.add_field(name="📞 Phone", value=f"`{phone_fmt}`", inline=True)

    if acc.get("extrabucks"):
        embed.add_field(name="💰 ExtraBucks Rewards", value=f"**{acc['extrabucks']}**", inline=True)
    if acc.get("birthday"):
        embed.add_field(name="🎂 Birthday", value=f"`{acc['birthday']}`", inline=True)

    val = f"📧 `{email}`" if email else ""
    if pwd:
        val += f" • 🔑 ||`{pwd}`||"
    if val:
        embed.add_field(name="🔐 Credentials", value=val, inline=False)

    if acc.get("notes"):
        embed.add_field(name="🎟️ Loaded Coupons & Notes", value=acc['notes'], inline=False)

    embed.set_image(url="attachment://cvs_barcode.png")
    embed.set_footer(text=f"AIO Bot CVS Account Manager • Account #{acc_id} of {len(cvs_accounts_db)}")
    return embed, file


class AccountSelectDropdown(discord.ui.Select):
    def __init__(self, current_idx: int = 0):
        options = []
        for idx, acc in enumerate(cvs_accounts_db[:25]):
            label = f"#{acc['id']} {acc.get('name', 'Account')}"
            desc = f"EC: {acc.get('extraCareNumber', '')} | {acc.get('phone', '')}"
            options.append(discord.SelectOption(label=label[:100], value=str(idx), description=desc[:100], default=(idx == current_idx)))
        super().__init__(placeholder="📋 Choose an account to view barcode...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        self.view.current_idx = idx
        embed, file = format_account_card(cvs_accounts_db[idx])
        self.view.update_select()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self.view)


class CVSAccountsPaginationView(discord.ui.View):
    def __init__(self, current_idx: int = 0):
        super().__init__(timeout=180)
        self.current_idx = current_idx
        self.dropdown = AccountSelectDropdown(current_idx)
        self.add_item(self.dropdown)

    def update_select(self):
        self.remove_item(self.dropdown)
        self.dropdown = AccountSelectDropdown(self.current_idx)
        self.add_item(self.dropdown)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message("⛔ Security Error: Only the bot application owner can view CVS accounts.", ephemeral=True)
        return False

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not cvs_accounts_db:
            await interaction.response.send_message("No accounts found!", ephemeral=True)
            return
        self.current_idx = (self.current_idx - 1) % len(cvs_accounts_db)
        embed, file = format_account_card(cvs_accounts_db[self.current_idx])
        self.update_select()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, emoji="▶️", row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not cvs_accounts_db:
            await interaction.response.send_message("No accounts found!", ephemeral=True)
            return
        self.current_idx = (self.current_idx + 1) % len(cvs_accounts_db)
        embed, file = format_account_card(cvs_accounts_db[self.current_idx])
        self.update_select()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="Custom Barcode", style=discord.ButtonStyle.secondary, emoji="💳", row=1)
    async def custom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CVSAccountModal())

class CVSAccountModal(discord.ui.Modal, title="💳 CVS ExtraCare® Card Formatter"):
    card_num = discord.ui.TextInput(
        label="ExtraCare / Card Number (Barcode)",
        placeholder="e.g. 48443912049281 (numbers/letters)",
        required=True,
        max_length=50
    )
    name_phone = discord.ui.TextInput(
        label="Name & Phone Number (Optional)",
        placeholder="e.g. John Doe | (555) 123-4567",
        required=False,
        max_length=100
    )
    creds = discord.ui.TextInput(
        label="Email & Password (Optional - Hidden in Spoilers)",
        placeholder="e.g. account@email.com | Password123",
        required=False,
        max_length=150
    )
    extrabucks = discord.ui.TextInput(
        label="ExtraBucks Balance / Rewards (Optional)",
        placeholder="e.g. $14.00 ExtraBucks Available",
        required=False,
        max_length=100
    )
    coupons_notes = discord.ui.TextInput(
        label="Loaded Coupons / Account Notes (Optional)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. $8 off $40 basket, $4 off Crest, CarePass active",
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw_card = self.card_num.value.strip()
        barcode_buffer = generate_code128_barcode_bytes(raw_card)
        file = discord.File(fp=barcode_buffer, filename="cvs_barcode.png")

        embed = discord.Embed(
            title="💳 CVS ExtraCare® Account & Barcode",
            description="Scannable barcode generated below for register & self-checkout scanners.",
            color=COLOR_PRIMARY
        )
        embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

        formatted_card = " ".join([raw_card[i:i+4] for i in range(0, len(raw_card), 4)])
        embed.add_field(name="🔢 ExtraCare Number", value=f"```\n{formatted_card}\n```", inline=False)

        if self.name_phone.value.strip():
            embed.add_field(name="👤 Cardholder", value=f"**{self.name_phone.value.strip()}**", inline=True)

        if self.extrabucks.value.strip():
            embed.add_field(name="💰 ExtraBucks Rewards", value=f"**{self.extrabucks.value.strip()}**", inline=True)

        if self.creds.value.strip():
            parts = self.creds.value.strip().split("|")
            email_part = parts[0].strip()
            pass_part = parts[1].strip() if len(parts) > 1 else ""
            val = f"📧 **Email:** `{email_part}`\n🔑 **Password:** ||`{pass_part}`||" if pass_part else f"📧 **Email:** `{email_part}`"
            embed.add_field(name="🔐 Account Credentials", value=val, inline=False)

        if self.coupons_notes.value.strip():
            embed.add_field(name="🎟️ Loaded Coupons & Notes", value=self.coupons_notes.value.strip(), inline=False)

        embed.set_image(url="attachment://cvs_barcode.png")
        embed.set_footer(text="AIO Bot CVS ExtraCare Barcode Generator • High-Resolution Scan")

        await interaction.response.send_message(embed=embed, file=file)

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
    embed.description = (
        f"🗓️ **{now.strftime('%A, %b %d, %Y @ %I:%M %p')}**\n"
        f"💰 **Net Money Saved:** **${net_saved:.2f}**\n"
        f"📈 **Lifetime Saved:** **${savings_tracker['total_net_saved']:.2f}** across {savings_tracker['trip_count']} trip(s)"
    )
    embed.add_field(name="🏷️ Full Retail", value=f"${subtotal:.2f}", inline=True)
    embed.add_field(name="💵 Register Paid", value=f"${total_due:.2f}", inline=True)
    embed.add_field(name="🎟️ Coupon Spend", value=f"${coupon_spend:.2f}", inline=True)
    return embed, subtotal, total_due, coupon_spend, net_saved, now


def build_cart_embed(user_id: int, notice: Optional[str] = None) -> discord.Embed:
    session = get_session(user_id)
    items = session["items"]
    coupons = session["coupons"]
    subtotal = sum(i['price'] for i in items)

    embed = discord.Embed(title="🛒 AIO Shopping Cart & Optimizer", color=COLOR_PRIMARY)
    desc_lines = []
    if notice:
        desc_lines.append(f"{notice}\n")

    if not items:
        desc_lines.append("📭 *Your cart is empty. Click **Add Items** or type `/add` to start!*")
    else:
        for item in items[:12]:
            desc_lines.append(f"• **{item['name']}** — ${item['price']:.2f}")
        if len(items) > 12:
            desc_lines.append(f"*...and {len(items) - 12} more item(s)*")

    embed.description = "\n".join(desc_lines)
    coupon_str = ", ".join(coupon_label(c) for c in coupons) if coupons else "None loaded"

    embed.add_field(name="💵 Subtotal", value=f"**${subtotal:.2f}** ({len(items)} items)", inline=True)
    embed.add_field(name="🎟️ Coupons", value=coupon_str, inline=True)
    if items and coupons:
        est_due, _ = calculate_best_bundles(items, coupons)
        saved = max(0.0, subtotal - est_due)
        embed.add_field(name="💰 Est. Register Due", value=f"**${est_due:.2f}** *(Save ${saved:.2f})*", inline=True)

    embed.set_footer(text="Manage below with buttons • Run /optimize for step-by-step cashier plan")
    return embed


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

    @discord.ui.button(label="Optimize Plan", style=discord.ButtonStyle.primary, emoji="📊", row=0)
    async def btn_opt(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Cart is empty! Click **Add Items** first.", ephemeral=True)
            return
        embed = build_strategy_embed(session["items"], session["coupons"])
        await interaction.response.send_message(embed=embed, view=QuickCartActionView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Undo Last", style=discord.ButtonStyle.secondary, emoji="↩️", row=1)
    async def btn_undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Nothing to undo — your cart is empty!", ephemeral=True)
            return
        removed  = session["items"].pop()
        subtotal = sum(i['price'] for i in session["items"])
        await interaction.response.send_message(
            f"↩️ Removed **{removed['name']}** (${removed['price']:.2f}). Subtotal: **${subtotal:.2f}**",
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


class BlackjackGameView(discord.ui.View):
    def __init__(self, player: discord.User, bet: int = 50):
        super().__init__(timeout=180)
        self.player = player
        self.bet = max(0, int(bet))
        self.initial_bet = self.bet
        self.deck = create_shuffled_deck()
        self.player_hand = [self.deck.pop(), self.deck.pop()]
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
        self.game_over = False

    def build_embed(self, hide_dealer: bool = True, outcome: Optional[str] = None) -> discord.Embed:
        p_val = calculate_hand_value(self.player_hand)
        p_cards = " ".join(f"`[{c}]`" for c in self.player_hand)

        if hide_dealer:
            d_cards = f"`[{self.dealer_hand[0]}]` `[🂠 ?]`"
            d_val_str = f"{calculate_hand_value([self.dealer_hand[0]])} + ?"
        else:
            d_cards = " ".join(f"`[{c}]`" for c in self.dealer_hand)
            d_val_str = str(calculate_hand_value(self.dealer_hand))

        color = COLOR_PRIMARY
        if outcome:
            if "Win" in outcome or "Blackjack" in outcome or "WIN" in outcome:
                color = COLOR_SUCCESS
            elif "BUST" in outcome or "DEALER WINS" in outcome:
                color = COLOR_ERROR
            else:
                color = COLOR_WARN

        embed = discord.Embed(title="🃏 Blackjack Table", color=color)
        embed.add_field(name=f"👤 {self.player.display_name}'s Hand ({p_val})", value=p_cards, inline=False)
        embed.add_field(name=f"🤖 Dealer's Hand ({d_val_str})", value=d_cards, inline=False)

        user_bal = get_user_coins(self.player.id)
        if self.bet > 0:
            embed.add_field(name="💰 Current Stake", value=f"**{self.bet:,} 🪙 coins**", inline=True)
            embed.add_field(name="👛 Your Balance", value=f"**{user_bal:,} 🪙 coins**", inline=True)

        if outcome:
            embed.add_field(name="🏁 Result", value=outcome, inline=False)
            embed.set_footer(text="Click 'Play Again 🔄' below to start a new round!")
        else:
            embed.set_footer(text="Choose an action below to continue.")
        return embed

    def finish_game(self, outcome_type: str = "lose"):
        self.game_over = True
        self.clear_items()

        # Settle coin payouts
        if self.bet > 0:
            if outcome_type == "natural":
                payout = int(self.bet * 2.5)
                add_user_coins(self.player.id, payout)
            elif outcome_type == "win":
                payout = int(self.bet * 2)
                add_user_coins(self.player.id, payout)
            elif outcome_type == "push":
                add_user_coins(self.player.id, int(self.bet))

        play_again_btn = discord.ui.Button(label="Play Again", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="bj_replay")
        async def replay_cb(interaction: discord.Interaction):
            if interaction.user.id != self.player.id:
                await interaction.response.send_message("⛔ This is not your game!", ephemeral=True)
                return

            if self.initial_bet > 0:
                if not deduct_user_coins(self.player.id, self.initial_bet):
                    current_bal = get_user_coins(self.player.id)
                    await interaction.response.send_message(
                        f"❌ You don't have enough coins ({self.initial_bet:,} 🪙) to play again! Current Balance: **{current_bal:,} 🪙**. Run `/daily` or `/balance`.",
                        ephemeral=True
                    )
                    return

            new_view = BlackjackGameView(self.player, self.initial_bet)
            p_val = calculate_hand_value(new_view.player_hand)
            if p_val == 21:
                new_view.finish_game(outcome_type="natural")
                profit = int(new_view.bet * 1.5)
                new_embed = new_view.build_embed(hide_dealer=False, outcome=f"🌟 **NATURAL BLACKJACK!** Instant Win! (+{profit:,} 🪙 profit)")
                await interaction.response.edit_message(embed=new_embed, view=new_view)
            else:
                new_embed = new_view.build_embed(hide_dealer=True)
                await interaction.response.edit_message(embed=new_embed, view=new_view)

        play_again_btn.callback = replay_cb
        self.add_item(play_again_btn)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.success, emoji="🟢")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("⛔ This is not your blackjack game!", ephemeral=True)
            return

        self.player_hand.append(self.deck.pop())
        p_val = calculate_hand_value(self.player_hand)

        # Disable double down once player has hit
        for child in self.children:
            if getattr(child, "label", None) == "Double Down":
                child.disabled = True

        if p_val > 21:
            self.finish_game(outcome_type="lose")
            embed = self.build_embed(hide_dealer=False, outcome=f"💥 **BUST!** You exceeded 21. Lost **{self.bet:,} 🪙**.")
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            embed = self.build_embed(hide_dealer=True)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger, emoji="🔴")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("⛔ This is not your blackjack game!", ephemeral=True)
            return

        while calculate_hand_value(self.dealer_hand) < 17 and self.deck:
            self.dealer_hand.append(self.deck.pop())

        p_val = calculate_hand_value(self.player_hand)
        d_val = calculate_hand_value(self.dealer_hand)

        if d_val > 21:
            self.finish_game(outcome_type="win")
            outcome = f"🎉 **DEALER BUSTS ({d_val})!** You win **+{self.bet:,} 🪙 coins**!"
        elif p_val > d_val:
            self.finish_game(outcome_type="win")
            outcome = f"🎉 **YOU WIN!** ({p_val} vs {d_val}) — Won **+{self.bet:,} 🪙 coins**!"
        elif d_val > p_val:
            self.finish_game(outcome_type="lose")
            outcome = f"💀 **DEALER WINS!** ({d_val} vs {p_val}) — Lost **{self.bet:,} 🪙**."
        else:
            self.finish_game(outcome_type="push")
            outcome = f"🤝 **PUSH / TIE!** Both scored {p_val}. Bet refunded."

        embed = self.build_embed(hide_dealer=False, outcome=outcome)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.primary, emoji="🟡")
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("⛔ This is not your blackjack game!", ephemeral=True)
            return

        if len(self.player_hand) > 2:
            await interaction.response.send_message("⛔ You can only Double Down on your first 2 cards!", ephemeral=True)
            return

        if self.bet > 0:
            if not deduct_user_coins(self.player.id, self.bet):
                await interaction.response.send_message("❌ Insufficient coins to Double Down!", ephemeral=True)
                return
            self.bet *= 2

        self.player_hand.append(self.deck.pop())
        p_val = calculate_hand_value(self.player_hand)
        if p_val > 21:
            self.finish_game(outcome_type="lose")
            embed = self.build_embed(hide_dealer=False, outcome=f"💥 **BUST on Double Down!** Dealer wins. Lost **{self.bet:,} 🪙**.")
            await interaction.response.edit_message(embed=embed, view=self)
            return

        while calculate_hand_value(self.dealer_hand) < 17 and self.deck:
            self.dealer_hand.append(self.deck.pop())

        d_val = calculate_hand_value(self.dealer_hand)
        if d_val > 21:
            self.finish_game(outcome_type="win")
            outcome = f"🎉 **DEALER BUSTS ({d_val})!** Double down win **+{self.bet:,} 🪙 coins**!"
        elif p_val > d_val:
            self.finish_game(outcome_type="win")
            outcome = f"🎉 **YOU WIN!** ({p_val} vs {d_val}) — Double payout **+{self.bet:,} 🪙 coins**!"
        elif d_val > p_val:
            self.finish_game(outcome_type="lose")
            outcome = f"💀 **DEALER WINS!** ({d_val} vs {p_val}) — Lost **{self.bet:,} 🪙**."
        else:
            self.finish_game(outcome_type="push")
            outcome = f"🤝 **PUSH / TIE!** ({p_val} each) — Bet refunded."

        embed = self.build_embed(hide_dealer=False, outcome=outcome)
        await interaction.response.edit_message(embed=embed, view=self)


class Connect4View(discord.ui.View):
    def __init__(self, p1: discord.User, p2: Optional[discord.User] = None):
        super().__init__(timeout=180)
        self.p1 = p1
        self.p2 = p2
        self.turn = p1.id
        self.board = create_connect4_board()
        self.game_over = False

        for col in range(CONNECT4_COLS):
            btn = discord.ui.Button(
                label=f"{col+1}",
                style=discord.ButtonStyle.secondary,
                row=0 if col < 4 else 1,
                custom_id=f"c4_col_{col}"
            )
            btn.callback = self.make_callback(col)
            self.add_item(btn)

    def build_embed(self, status_msg: Optional[str] = None) -> discord.Embed:
        p2_name = self.p2.display_name if self.p2 else "AIO Bot AI 🤖"
        embed = discord.Embed(title="🔴 Connect 4 Arena 🟡", color=COLOR_PRIMARY)
        embed.description = f"**Player 1 (🔴):** {self.p1.mention}\n**Player 2 (🟡):** {self.p2.mention if self.p2 else p2_name}\n\n" + render_connect4_board(self.board)
        if status_msg:
            embed.add_field(name="Status", value=status_msg, inline=False)
        else:
            current = self.p1.mention if self.turn == self.p1.id else (self.p2.mention if self.p2 else "AIO Bot 🤖")
            piece = "🔴" if self.turn == self.p1.id else "🟡"
            embed.add_field(name="Turn", value=f"{piece} {current}'s turn to drop!", inline=False)
        return embed

    def bot_make_move(self) -> Optional[int]:
        for c in range(CONNECT4_COLS):
            b_copy = [row[:] for row in self.board]
            if drop_piece(b_copy, c, "🟡") is not None:
                if check_connect4_win(b_copy, "🟡"):
                    return c
        for c in range(CONNECT4_COLS):
            b_copy = [row[:] for row in self.board]
            if drop_piece(b_copy, c, "🔴") is not None:
                if check_connect4_win(b_copy, "🔴"):
                    return c
        valid = [c for c in range(CONNECT4_COLS) if self.board[0][c] == "⚪"]
        if 3 in valid and random.random() < 0.6:
            return 3
        return random.choice(valid) if valid else None

    def make_callback(self, col: int):
        async def callback(interaction: discord.Interaction):
            if self.game_over:
                await interaction.response.send_message("Game has ended!", ephemeral=True)
                return

            if interaction.user.id != self.turn:
                await interaction.response.send_message("⛔ It is not your turn!", ephemeral=True)
                return

            piece = "🔴" if self.turn == self.p1.id else "🟡"
            row = drop_piece(self.board, col, piece)
            if row is None:
                await interaction.response.send_message("❌ That column is already full! Pick another.", ephemeral=True)
                return

            def add_play_again_button():
                self.clear_items()
                play_again_btn = discord.ui.Button(label="Play Again", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="c4_replay")
                async def c4_replay_cb(itx: discord.Interaction):
                    if itx.user.id not in (self.p1.id, (self.p2.id if self.p2 else self.p1.id)):
                        await itx.response.send_message("⛔ This is not your game!", ephemeral=True)
                        return
                    new_v = Connect4View(self.p1, self.p2)
                    await itx.response.edit_message(embed=new_v.build_embed(), view=new_v)
                play_again_btn.callback = c4_replay_cb
                self.add_item(play_again_btn)

            if check_connect4_win(self.board, piece):
                self.game_over = True
                add_play_again_button()
                winner = self.p1 if piece == "🔴" else (self.p2 or interaction.client.user)
                embed = self.build_embed(f"🏆 **CONNECT 4!** {winner.mention} wins the game!")
                embed.color = COLOR_SUCCESS
                await interaction.response.edit_message(embed=embed, view=self)
                return

            if is_connect4_full(self.board):
                self.game_over = True
                add_play_again_button()
                embed = self.build_embed("🤝 **DRAW!** The board is full.")
                embed.color = COLOR_WARN
                await interaction.response.edit_message(embed=embed, view=self)
                return

            if self.p2:
                self.turn = self.p2.id if self.turn == self.p1.id else self.p1.id
                embed = self.build_embed()
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                bot_col = self.bot_make_move()
                if bot_col is not None:
                    b_row = drop_piece(self.board, bot_col, "🟡")
                    if check_connect4_win(self.board, "🟡"):
                        self.game_over = True
                        add_play_again_button()
                        embed = self.build_embed("🤖 **CONNECT 4!** AIO Bot AI wins!")
                        embed.color = COLOR_ERROR
                        await interaction.response.edit_message(embed=embed, view=self)
                        return
                embed = self.build_embed()
                await interaction.response.edit_message(embed=embed, view=self)
        return callback


class TriviaView(discord.ui.View):
    def __init__(self, user: discord.User, question_data: Dict[str, Any]):
        super().__init__(timeout=45)
        self.user = user
        self.q_data = question_data
        self.answered = False

        for idx, opt in enumerate(question_data["options"]):
            btn = discord.ui.Button(
                label=f"{chr(65+idx)}. {opt}",
                style=discord.ButtonStyle.secondary,
                row=idx // 2,
                custom_id=f"trivia_{idx}"
            )
            btn.callback = self.make_callback(idx)
            self.add_item(btn)

    def make_callback(self, chosen_idx: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("⛔ Start your own trivia game with `/trivia`!", ephemeral=True)
                return

            if self.answered:
                return
            self.answered = True

            correct_idx = self.q_data["ans"]
            is_correct = (chosen_idx == correct_idx)

            if is_correct:
                add_user_coins(self.user.id, 50)
                cur_bal = get_user_coins(self.user.id)
                coin_reward_str = f"\n\n🪙 **+50 Coins Awarded!** (Balance: **{cur_bal:,} 🪙**)"
            else:
                coin_reward_str = ""

            for idx, child in enumerate(self.children):
                child.disabled = True
                if idx == correct_idx:
                    child.style = discord.ButtonStyle.success
                elif idx == chosen_idx and not is_correct:
                    child.style = discord.ButtonStyle.danger

            embed = discord.Embed(
                title="🧠 Trivia Challenge — " + ("🎉 Correct!" if is_correct else "❌ Incorrect!"),
                color=COLOR_SUCCESS if is_correct else COLOR_ERROR
            )
            embed.add_field(name="Question", value=self.q_data["q"], inline=False)
            embed.add_field(
                name="Correct Answer",
                value=f"**{chr(65+correct_idx)}. {self.q_data['options'][correct_idx]}**",
                inline=True
            )
            embed.add_field(name="Did You Know?", value=f"{self.q_data.get('info', 'Great knowledge!')}{coin_reward_str}", inline=False)
            embed.set_footer(text=f"Played by {self.user.display_name} • Click 'Next Question ➡️' to continue!")

            next_btn = discord.ui.Button(label="Next Question", style=discord.ButtonStyle.primary, emoji="➡️", row=2)
            async def next_q_cb(itx: discord.Interaction):
                if itx.user.id != self.user.id:
                    await itx.response.send_message("⛔ Start your own trivia with `/trivia`!", ephemeral=True)
                    return
                all_qs = [q for cat in TRIVIA_QUESTIONS.values() for q in cat]
                new_q = secrets.choice(all_qs)
                new_v = TriviaView(self.user, new_q)
                new_embed = discord.Embed(
                    title="🧠 Trivia Challenge",
                    description=f"**{new_q['q']}**\n\nSelect the correct option below (45s timer):",
                    color=COLOR_PRIMARY
                )
                for i_idx, opt in enumerate(new_q["options"]):
                    new_embed.add_field(name=f"Option {chr(65+i_idx)}", value=opt, inline=True)
                await itx.response.edit_message(embed=new_embed, view=new_v)
            next_btn.callback = next_q_cb
            self.add_item(next_btn)

            await interaction.response.edit_message(embed=embed, view=self)
        return callback


class RPSView(discord.ui.View):
    def __init__(self, p1: discord.User, p2: Optional[discord.User] = None):
        super().__init__(timeout=60)
        self.p1 = p1
        self.p2 = p2
        self.choices: Dict[int, str] = {}

    @discord.ui.button(label="Rock", emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "Rock")

    @discord.ui.button(label="Paper", emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "Paper")

    @discord.ui.button(label="Scissors", emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "Scissors")

    async def handle_choice(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if self.p2:
            if uid not in (self.p1.id, self.p2.id):
                await interaction.response.send_message("⛔ You are not in this match!", ephemeral=True)
                return
            self.choices[uid] = choice
            if len(self.choices) < 2:
                await interaction.response.send_message(f"🔒 Locked in **{choice}**! Waiting for opponent...", ephemeral=True)
                return
            c1 = self.choices[self.p1.id]
            c2 = self.choices[self.p2.id]
            outcome = self.evaluate(c1, c2, self.p1, self.p2)
            for child in self.children:
                child.disabled = True
            embed = discord.Embed(title="🪨📄✂️ Rock-Paper-Scissors Duel", color=COLOR_PRIMARY)
            embed.add_field(name=f"👤 {self.p1.display_name}", value=f"Picked **{c1}**", inline=True)
            embed.add_field(name=f"👤 {self.p2.display_name}", value=f"Picked **{c2}**", inline=True)
            embed.add_field(name="Result", value=outcome, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        else:
            if uid != self.p1.id:
                await interaction.response.send_message("⛔ Start your own game with `/rps`!", ephemeral=True)
                return
            bot_choice = secrets.choice(["Rock", "Paper", "Scissors"])
            outcome = self.evaluate(choice, bot_choice, self.p1, None)
            for child in self.children:
                child.disabled = True
            embed = discord.Embed(title="🪨📄✂️ Rock-Paper-Scissors Duel", color=COLOR_PRIMARY)
            embed.add_field(name=f"👤 {self.p1.display_name}", value=f"Picked **{choice}**", inline=True)
            embed.add_field(name="🤖 AIO Bot", value=f"Picked **{bot_choice}**", inline=True)
            embed.add_field(name="Result", value=outcome, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()

    def evaluate(self, c1: str, c2: str, p1: discord.User, p2: Optional[discord.User]) -> str:
        beats = {"Rock": "Scissors", "Paper": "Rock", "Scissors": "Paper"}
        if c1 == c2:
            return f"🤝 **It's a Tie!** Both chose {c1}."
        elif beats[c1] == c2:
            return f"🏆 **{p1.mention} WINS!** ({c1} beats {c2})"
        else:
            p2_str = p2.mention if p2 else "AIO Bot 🤖"
            return f"💀 **{p2_str} WINS!** ({c2} beats {c1})"


class SlotsSpinView(discord.ui.View):
    def __init__(self, user: discord.User, bet: int = 10):
        super().__init__(timeout=90)
        self.user = user
        self.bet = max(1, int(bet))

    @discord.ui.button(label="Spin Again", style=discord.ButtonStyle.success, emoji="🎰")
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Spin your own slots with `/slots`!", ephemeral=True)
            return

        if self.bet > 0:
            if not deduct_user_coins(self.user.id, self.bet):
                cur_bal = get_user_coins(self.user.id)
                await interaction.response.send_message(
                    f"❌ You don't have enough coins ({self.bet:,} 🪙) to spin again! Balance: **{cur_bal:,} 🪙**. Run `/daily` or `/balance`.",
                    ephemeral=True
                )
                return

        grid = roll_3x3_slots()
        cur_bal = get_user_coins(self.user.id)

        def make_spin_embed(grid_text: str, status_text: str, color_val: int = COLOR_PRIMARY):
            emb = discord.Embed(
                title="🎰 AIO 3x3 High-Roller Slots",
                description=f"{grid_text}\n\n{status_text}",
                color=color_val
            )
            emb.add_field(name="💰 Stake", value=f"**{self.bet:,} 🪙**", inline=True)
            emb.add_field(name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
            emb.set_footer(text=f"Spun by {self.user.display_name} • 5 Multi-Paylines • Fair 100% Random PRNG")
            return emb

        try:
            # Reel animation step 1: All 3 columns spinning
            await interaction.response.edit_message(embed=make_spin_embed(format_3x3_grid(grid, 0), "*Spinning 3x3 high-roller reels...*"), view=None)
            await asyncio.sleep(0.9)

            # Reel animation step 2: Column 1 stops
            await interaction.message.edit(embed=make_spin_embed(format_3x3_grid(grid, 1), "*Column 1 locked in... Columns 2 & 3 spinning...*"))
            await asyncio.sleep(0.8)

            # Reel animation step 3: Column 2 stops
            await interaction.message.edit(embed=make_spin_embed(format_3x3_grid(grid, 2), "*Columns 1 & 2 locked in... Final column spinning...*"))
            await asyncio.sleep(0.8)

            # Reel animation step 4: Final reveal & payouts
            winnings, hits, summary_title = evaluate_3x3_slots(grid, self.bet)
            if winnings > 0:
                add_user_coins(self.user.id, winnings)
                cur_bal = get_user_coins(self.user.id)
                hits_str = "\n".join(hits)
                color = COLOR_SUCCESS if winnings >= self.bet * 2 else COLOR_WARN
                final_embed = make_spin_embed(
                    format_3x3_grid(grid, 3),
                    f"🎉 **WINNER!**\n{hits_str}\n💰 Stake: **{self.bet:,} 🪙** ➔ Won: **+{winnings:,} 🪙**!",
                    color
                )
                final_embed.set_field_at(1, name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
            else:
                final_embed = make_spin_embed(
                    format_3x3_grid(grid, 3),
                    f"💀 **No matching lines!** Better luck next spin!\n💰 Lost: **{self.bet:,} 🪙**",
                    COLOR_ERROR
                )

            final_embed.set_footer(text=f"Spun by {self.user.display_name} • Click Spin Again 🎰 to roll again!")
            fresh_view = SlotsSpinView(user=self.user, bet=self.bet)
            await interaction.message.edit(embed=final_embed, view=fresh_view)
        except Exception as e:
            add_user_coins(self.user.id, self.bet)
            print(f"⚠️ Slots spin error: {e}", file=sys.stderr)
            try:
                await interaction.followup.send(f"⚠️ A network error occurred. Your **{self.bet:,} 🪙** coins were refunded!", ephemeral=True)
            except Exception:
                pass

class HelpCategorySelect(discord.ui.Select):
    def __init__(self, author_perms: discord.Permissions, is_owner: bool):
        self.author_perms = author_perms
        self.is_owner = is_owner
        options = [
            discord.SelectOption(label="Coupon Optimizer", value="coupons", description="Smart cart calculation & coupon bundling", emoji="🛍️"),
            discord.SelectOption(label="Server Moderation", value="mod", description="Server control, anti-raid, filters & mod cases", emoji="🛡️"),
            discord.SelectOption(label="Games & Arcade", value="games", description="Blackjack, Connect 4, Trivia, Slots, RPS & Dice", emoji="🎮"),
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
            embed.add_field(name="Checkout & History", value="`/checkout` or `!checkout` — save trip & get receipt\n`/savings` or `!savings` — lifetime stats\n`/history` or `!history` — view past trips", inline=False)
            embed.add_field(name="Instant Calculator", value="`/calc` or `!calc [items] | [coupons]` (e.g. `!calc Fairlife 4.49, Shampoo 6.59 | 8 5` or `/calc ...`)", inline=False)
            embed.add_field(name="Cart Management", value="`/cart` — view current cart\n`/undo` — remove last item added\n`/remove [name]` — remove item by name\n`/clear` — wipe cart & coupons", inline=False)
        elif cat == "mod":
            embed.title = "🛡️ AIO Bot — Server Moderation Suite"
            embed.description = "Complete administrative security and moderation suite. Run using either `!` or `/`."
            embed.add_field(name="Server Lockdown & Anti-Raid", value="`/lockdown [action: on/off] [reason]` — emergency lockdown for all server text channels", inline=False)
            embed.add_field(name="Auto-Mod Word Filter", value="`/filter add [word]` / `/filter remove [word]` / `/filter list` — automatic word censor & warning trigger", inline=False)
            embed.add_field(name="Case & Incident Logs", value="`/modlogs [@member]` — view all historical infractions\n`/case [id]` — look up detailed case file", inline=False)
            embed.add_field(name="Staff Private Notes", value="`/note add [@member] [note]` / `/note view` / `/note clear` — staff internal records", inline=False)
            embed.add_field(name="Member Discipline", value="`/kick` or `!kick [@member] [reason]`\n`/ban` or `!ban [@member] [reason]`\n`/unban` or `!unban [user_id_or_name]`\n`/timeout` or `!timeout [@member] [duration]` (e.g. `10m`, `1h`, `1d`)\n`/untimeout` or `!untimeout [@member]`", inline=False)
            embed.add_field(name="Warnings System", value="`/warn` or `!warn [@member] [reason]` — log a warning\n`/warnings` or `!warnings [@member]` — view warning record\n`/clearwarnings` or `!clearwarnings [@member]` — wipe records", inline=False)
            embed.add_field(name="Channel & Message Management", value="`/modpanel` or `!modpanel` — interactive menu\n`/ticketpanel` or `!tickets` — deploy interactive support ticket panel\n`/dm [user] [msg]` or `!dm` — direct message member from bot\n`/nukechannel` or `!nukechannel` — recreate & wipe channel\n`/purge [amount] [member] [channel]` — bulk delete\n`/lock` & `/unlock` / `/slowmode [sec]`", inline=False)
        elif cat == "games":
            embed.title = "🎮 AIO Bot — Arcade, Casino & Economy"
            embed.description = "Interactive Discord mini-games and coin economy system powered by Discord UI Buttons! Run using either `!` or `/`."
            embed.add_field(name="🪙 Coin Economy & Banking", value="`/balance` or `!bal [@member]` — check coin wallet\n`/daily` or `!daily` — claim daily 250 free coins (24h cooldown)\n`/pay` or `!pay [@member] [amount]` — transfer coins\n`/leaderboard` or `!top` — top 10 richest members", inline=False)
            embed.add_field(name="🃏 Blackjack / 21", value="`/blackjack [bet]` or `!blackjack` — play 21 against dealer with interactive Hit, Stand & Double Down buttons", inline=False)
            embed.add_field(name="🔴🟡 Connect 4", value="`/connect4 [@opponent]` or `!connect4` — 7-column interactive drop board against friends or smart Bot AI", inline=False)
            embed.add_field(name="🧠 Trivia Quiz Challenge", value="`/trivia [category: general/tech/gaming/science]` or `!trivia` — 4-choice timed quiz challenge (+50 🪙 per win)", inline=False)
            embed.add_field(name="🎰 High-Roller Slots", value="`/slots [bet] [rounds]` or `!slots` — spinning slot machine with up to 50x Jackpot multipliers & automated multi-round spins", inline=False)
            embed.add_field(name="🪨📄✂️ Rock-Paper-Scissors", value="`/rps [choice] [@opponent]` or `!rps` — secret choice duel against friends or the bot", inline=False)
            embed.add_field(name="🪙 Coinflip & Dice Roller", value="`/coinflip [heads/tails] [bet]` — animated flip & betting\n`/roll [dice]` — tabletop dice roller (e.g. `2d6`, `1d20+5`, `100`)", inline=False)
        elif cat == "utils":
            embed.title = "🎨 AIO Bot — Embeds & Utilities"
            embed.description = "Creative and diagnostic server tools. Run using either `!` or `/`."
            embed.add_field(name="Bot Announcement & Echo", value="`/say` or `!say [text]` — repost text and attached photos/images through the bot", inline=False)
            embed.add_field(name="Custom Embed Creator", value="`/embed` or `!embed` — open interactive modal to design & publish rich embeds with titles, images, colors, and footers", inline=False)
            embed.add_field(name="Server & Member Info", value="`/serverinfo` or `!serverinfo` — server stats, boosts, channels, and roles\n`/userinfo` or `!userinfo [@member]` — member details, account age, join date, permissions", inline=False)
            embed.add_field(name="Bot Status", value="`/ping` or `!ping` — bot latency\n`/about` or `!about` — system info", inline=False)
        elif cat == "owner":
            embed.title = "👑 AIO Bot — Operator Commands"
            embed.add_field(
                name="Server Architecture & Channel Cleanup",
                value=(
                    "`/formatserver` (or `!setupserver`) — organize full server layout with categories, channels, and roles (shields `#form-automation`)\n"
                    "`/deletechannels` (or `!clearchannels`) — delete previous or leftover unformatted channels (shields `#form-automation`)"
                ),
                inline=False
            )
            embed.add_field(name="Private Optimizer Hub", value="`/setup` — create `#🛒-coupon-optimizer` hub\n`[🛒 Open Private Optimizer Room]` — instant personal room for shopping & savings", inline=False)
            embed.add_field(name="CVS Accounts Database", value="`/accounts [query]` (or `!accounts`, `!cards`) — browse imported CVS ExtraCare accounts with barcode scans, search, pagination & custom card formatter", inline=False)
            embed.add_field(name="Database Management", value="`/delete-last-trip` (or `!undotrip`) — delete last recorded trip and revert lifetime savings stats", inline=False)
            embed.add_field(name="CPU Benchmark & Stress Test", value="`/run-stress-test` (or `!stresstest`, `!benchmark`) — benchmark algorithm latency across permutation graphs", inline=False)

        embed.set_footer(text="Tip: You can use ! or / for any command (e.g. !help or /help).")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpMenuView(discord.ui.View):
    def __init__(self, author_perms: discord.Permissions, is_owner: bool):
        super().__init__(timeout=None)
        self.add_item(HelpCategorySelect(author_perms, is_owner))


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm Close & Delete", style=discord.ButtonStyle.danger, emoji="✅")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ This action can only be performed in a text channel.", ephemeral=True)
            return

        if is_protected_channel(channel):
            await interaction.response.send_message("🛡️ **Protected Channel:** `#form-automation` CANNOT be closed or deleted!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        close_ticket_record(channel.id)
        await interaction.followup.send(f"🔒 **Ticket closed by {interaction.user.mention}.** This channel will be deleted in 5 seconds...")
        await asyncio.sleep(5)
        if not is_protected_channel(channel):
            try:
                await channel.delete(reason=f"Support ticket closed by {interaction.user}")
            except Exception as e:
                print(f"⚠️ Error deleting ticket channel {channel.id}: {e}", file=sys.stderr)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Ticket closure cancelled.", view=None)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, emoji="📋", custom_id="aio_ticket_claim_btn", row=0)
    async def btn_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        if not is_staff_or_admin(interaction.user):
            await interaction.response.send_message("⛔ Only server staff or moderators can claim tickets.", ephemeral=True)
            return

        claim_ticket_record(interaction.channel.id, interaction.user.id)
        await interaction.response.send_message(
            f"📌 **Ticket Claimed:** {interaction.user.mention} has claimed this ticket and will be assisting you!"
        )

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="aio_ticket_transcript_btn", row=0)
    async def btn_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            messages = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
            info = tickets_db.get("tickets", {}).get(str(interaction.channel.id), {})
            t_id = info.get("id", 0)
            owner_id = info.get("owner_id", interaction.user.id)
            txt = format_ticket_transcript(messages, t_id, owner_id)
            file = discord.File(io.BytesIO(txt.encode("utf-8")), filename=f"transcript-ticket-{t_id:04d}.txt")
            await interaction.followup.send("📜 **Here is the transcript for this ticket:**", file=file)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to generate transcript: {e}", ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="aio_ticket_close_btn", row=0)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_protected_channel(interaction.channel):
            await interaction.response.send_message("🛡️ **Protected Channel:** This channel cannot be closed or deleted!", ephemeral=True)
            return
        await interaction.response.send_message(
            "⚠️ **Close Ticket Confirmation**\nAre you sure you want to close this ticket? This will delete the channel.",
            view=TicketCloseConfirmView(),
            ephemeral=True
        )


class TicketLaunchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="📩", custom_id="aio_ticket_launch_btn")
    async def btn_open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        active_id = get_user_active_ticket(guild.id, interaction.user.id)
        if active_id:
            existing_ch = guild.get_channel(active_id)
            if existing_ch:
                await interaction.response.send_message(
                    f"⚠️ You already have an open ticket in {existing_ch.mention}! Please use your existing ticket.",
                    ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True)

        cat = discord.utils.get(guild.categories, name="📁 TICKETS")
        if not cat:
            cat = discord.utils.get(guild.categories, name="TICKETS")
        if not cat:
            cat_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
            }
            cat = await guild.create_category("📁 TICKETS", overwrites=cat_overwrites)

        ticket_num = tickets_db.get("counter", 0) + 1
        safe_name = re.sub(r'[^a-zA-Z0-9]', '', interaction.user.name).lower()[:12] or "user"
        channel_name = f"ticket-{ticket_num:04d}-{safe_name}"

        founder_role = get_founder_role(guild)
        mod_role = get_moderator_role(guild)

        ch_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True
            )
        }
        if founder_role:
            ch_overwrites[founder_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )
        if mod_role:
            ch_overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )
        for role in guild.roles:
            if role.permissions.administrator or role.permissions.manage_channels or role.name.lower() in ("moderator", "moderators", "mod", "mods", "admin", "administrator", "founder", "founders", "owner"):
                ch_overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
                )

        new_ch = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            overwrites=ch_overwrites,
            topic=f"AIO Support Ticket #{ticket_num:04d} | Author: {interaction.user} ({interaction.user.id})"
        )

        create_ticket_record(guild.id, new_ch.id, interaction.user.id, channel_name)

        embed = discord.Embed(
            title=f"🎫 Support Ticket #{ticket_num:04d}",
            description=(
                f"Hey {interaction.user.mention}, your ticket has been opened!\n\n"
                "**💬 What to do next**\n"
                "▸ Describe your issue or question in detail below.\n"
                "▸ A staff member will be with you shortly.\n"
                "▸ Please be patient — do **not** ping staff repeatedly."
            ),
            color=COLOR_PRIMARY
        )
        embed.add_field(name="👤 Opened By", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed.add_field(name="⏰ Opened", value=f"<t:{int(time.time())}:R>", inline=True)
        embed.add_field(name="📌 Status", value="🟢 Open · Unclaimed", inline=True)
        embed.set_footer(text="AIO Support Suite • Use the buttons below to manage this ticket")

        mention_targets = [interaction.user.mention]
        if founder_role:
            mention_targets.append(founder_role.mention)
        if mod_role and (not founder_role or mod_role.id != founder_role.id):
            mention_targets.append(mod_role.mention)
        ping_str = " ".join(mention_targets)

        await new_ch.send(
            content=f"{ping_str} Support ticket opened! Staff have been alerted.",
            embed=embed,
            view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True)
        )
        await interaction.followup.send(f"✅ Your support ticket has been created: {new_ch.mention}", ephemeral=True)


class FoodAccountOrderModal(discord.ui.Modal):
    def __init__(self, brand: str, price: float):
        super().__init__(title=f"🛒 {brand} Account Order")
        self.brand = brand
        self.price = price

        self.qty_input = discord.ui.TextInput(
            label="How many accounts do you want? (1–10)",
            placeholder="1",
            default="1",
            min_length=1,
            max_length=2,
            required=True
        )
        self.add_item(self.qty_input)

        self.notes_input = discord.ui.TextInput(
            label="Payment Method / Notes (Optional)",
            placeholder="e.g. CashApp, ApplePay, Venmo, Crypto",
            required=False,
            max_length=100
        )
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return

        raw_qty = self.qty_input.value.strip()
        try:
            qty = int(raw_qty)
            if qty < 1 or qty > 10:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid quantity between 1 and 10.", ephemeral=True)
            return

        active_id = get_user_active_ticket(guild.id, interaction.user.id)
        if active_id:
            existing_ch = guild.get_channel(active_id)
            if existing_ch:
                await interaction.response.send_message(
                    f"⚠️ You already have an open ticket in {existing_ch.mention}! Please use that channel or request staff to close it first.",
                    ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True)

        cat = discord.utils.get(guild.categories, name="📁 TICKETS")
        if not cat:
            cat = discord.utils.get(guild.categories, name="TICKETS")
        if not cat:
            cat_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
            }
            cat = await guild.create_category("📁 TICKETS", overwrites=cat_overwrites)

        ticket_num = tickets_db.get("counter", 0) + 1
        brand_slug = "tacobell" if "taco" in self.brand.lower() else "pizzahut"
        safe_user = re.sub(r'[^a-zA-Z0-9]', '', interaction.user.name).lower()[:10] or "user"
        channel_name = f"order-{brand_slug}-{ticket_num:04d}-{safe_user}"

        founder_role = get_founder_role(guild)
        mod_role = get_moderator_role(guild)

        ch_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True
            )
        }
        if founder_role:
            ch_overwrites[founder_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )
        if mod_role:
            ch_overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )
        for role in guild.roles:
            if role.permissions.administrator or role.permissions.manage_channels or role.name.lower() in ("moderator", "moderators", "mod", "mods", "admin", "administrator", "founder", "founders", "owner"):
                ch_overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
                )

        new_ch = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            overwrites=ch_overwrites,
            topic=f"{self.brand} Preloaded Accounts Order #{ticket_num:04d} | Buyer: {interaction.user} ({interaction.user.id})"
        )

        create_ticket_record(guild.id, new_ch.id, interaction.user.id, channel_name)

        total_est = qty * self.price
        notes_val = self.notes_input.value.strip() or "Standard Payment"

        if brand_slug == "tacobell":
            instructions = (
                "1️⃣  Staff will provide the account email and payment address.\n"
                "2️⃣  Enter the email into the **Taco Bell app** and tap **Send Code**.\n"
                "3️⃣  Ping staff here — they will immediately retrieve your OTP code!"
            )
        else:
            instructions = (
                "1️⃣  Staff will provide payment details and your Hut Rewards credentials.\n"
                "2️⃣  Log in on the **Pizza Hut app** or website (delivery recommended).\n"
                "3️⃣  Stack **2–3 rewards per order** for maximum savings!"
            )

        embed = discord.Embed(
            title=f"{'🌮' if brand_slug == 'tacobell' else '🍕'} {self.brand} Order #{ticket_num:04d}",
            description=(
                f"Welcome {interaction.user.mention}! Support staff has been notified of your order.\n\n"
                f"**📋 Order Details**\n"
                f"▸ Item: **{self.brand} Preloaded Account(s)**\n"
                f"▸ Quantity: **{qty} account(s)** — ${self.price:.2f} each\n"
                f"▸ Estimated Total: **${total_est:.2f}**\n"
                f"▸ Payment Note: `{notes_val}`\n\n"
                f"**📌 How This Works**\n"
                f"{instructions}"
            ),
            color=0x2ecc71
        )
        embed.add_field(name="👤 Customer", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed.add_field(name="⏰ Opened", value=f"<t:{int(time.time())}:R>", inline=True)
        embed.add_field(name="📌 Status", value="🟢 Awaiting Staff", inline=True)
        embed.set_footer(text="AIO Order Suite • Staff: use Claim Ticket to handle this order")

        mention_targets = [interaction.user.mention]
        if founder_role:
            mention_targets.append(founder_role.mention)
        if mod_role and (not founder_role or mod_role.id != founder_role.id):
            mention_targets.append(mod_role.mention)
        ping_str = " ".join(mention_targets)

        await new_ch.send(
            content=f"{ping_str} Thank you for your order! Staff have been alerted.",
            embed=embed,
            view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True)
        )
        await interaction.followup.send(f"✅ Your purchase ticket has been created: {new_ch.mention}", ephemeral=True)


def build_food_accounts_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🌮🍕 Fast Food Preloaded Rewards Accounts",
        description=(
            "Get preloaded **Taco Bell** & **Pizza Hut** rewards accounts with dozens of free items and discounts already claimed!\n\n"
            "Click **Buy Taco Bell** or **Buy Pizza Hut** below to open a ticket."
        ),
        color=0xff7b00
    )

    tb_value = (
        "**Price:** **$10.00 each** · *15 rewards already claimed on every account*\n\n"
        "**How to order:** Open a ticket for Taco Bell → specify how many you want (1–10). After staff provides account email, enter that into Taco Bell app, tap Send code in the app, then ping staff and they will retrieve the OTP code.\n\n"
        "**What's on every account:**\n"
        "• $15 off your entire order\n"
        "• $10 off your entire order\n"
        "• $5 off your entire order\n"
        "• extra $5 off\n"
        "• 1 free individual item\n"
        "• Free Chalupa Supreme (two of these)\n"
        "• Free quesadilla\n"
        "• Fire tier + Hot tier free rewards\n"
        "• Welcome + Referral free rewards\n"
        "• Birthday Baja Blast Freeze\n"
        "• Free large fountain drink\n\n"
        "🔥 **Loyalty is ACTIVE.** These are free / off-the-order rewards — not spend coupons."
    )
    embed.add_field(name="🌮 Taco Bell Rewards", value=tb_value, inline=False)

    ph_value = (
        "**Price:** **$15.00 each** · *rewards stackable, recommended 2–3 at a time*\n\n"
        "**Description:** Every account is a Hut Rewards login with these free rewards already claimed:\n\n"
        "**Pizzas:**\n"
        "• 2 Large pizzas\n"
        "• 1 Medium pizza\n"
        "• 1 Personal Pan pizza\n"
        "• 1 Melt\n\n"
        "**Sides:**\n"
        "• 1 order of breadsticks\n"
        "• 1 order of cheesy breadsticks\n"
        "• 8 pc boneless wings\n"
        "• Triple cheese mac\n"
        "• Cinnamon sticks\n"
        "• S'mores sticks\n"
        "• Cinnabon cinnamon rolls\n"
        "• 1 free dip cup (ranch / marinara / etc.)\n\n"
        "**Drinks & dessert:**\n"
        "• 1× 2-liter drink\n"
        "• 1× 20oz drink\n"
        "• Triple chocolate fudge brownie\n"
        "• Huge ultimate cookie\n\n"
        "🚗 *Delivery is recommended if you're shy lol — pickup works too.*"
    )
    embed.add_field(name="🍕 Pizza Hut Preloaded Accounts", value=ph_value, inline=False)

    embed.set_footer(text="Click the buttons below to open an order ticket with staff!")
    return embed


class FoodAccountPurchaseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy Taco Bell ($10)", style=discord.ButtonStyle.primary, emoji="🌮", custom_id="aio_buy_tacobell_btn")
    async def btn_tacobell(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FoodAccountOrderModal(brand="Taco Bell", price=10.0))

    @discord.ui.button(label="Buy Pizza Hut ($15)", style=discord.ButtonStyle.success, emoji="🍕", custom_id="aio_buy_pizzahut_btn")
    async def btn_pizzahut(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FoodAccountOrderModal(brand="Pizza Hut", price=15.0))


FORMAT_SERVER_BLUEPRINT = [
    {
        "category": "📌 INFORMATION",
        "read_only": True,
        "channels": [
            {"name": "📢-announcements", "type": "text", "topic": "Official server announcements and updates."},
            {"name": "📜-rules", "type": "text", "topic": "Server guidelines and community rules."},
            {"name": "👋-welcome", "type": "text", "topic": "Welcome new members to the server!"}
        ]
    },
    {
        "category": "💬 COMMUNITY",
        "channels": [
            {"name": "💬-general-chat", "type": "text", "topic": "Main hangout and general conversation."},
            {"name": "🤖-bot-commands", "type": "text", "topic": "Run bot commands and mini-games here!"},
            {"name": "💡-suggestions", "type": "text", "topic": "Share ideas and feedback for the server."}
        ]
    },
    {
        "category": "🔒 PRIVATE CVS",
        "private": True,
        "channels": [
            {"name": "🛒-coupon-optimizer", "type": "text", "topic": "CVS & retail coupon optimizer hub. Click the button below to open your private room!"}
        ]
    },
    {
        "category": "🛍️ SAVINGS & REWARDS",
        "channels": [
            {"name": "🌮🍕-food-rewards", "type": "text", "topic": "Preloaded Taco Bell & Pizza Hut rewards accounts store. Order below!"},
            {"name": "🏷️-deals-and-savings", "type": "text", "topic": "Share latest store deals, coupons, and discounts."},
            {"name": "🧾-receipt-brags", "type": "text", "topic": "Post your receipt savings and coupon hauls!"}
        ]
    },
    {
        "category": "🎫 SUPPORT",
        "channels": [
            {"name": "📩-open-a-ticket", "type": "text", "topic": "Need help? Click the button below to open a private ticket!"}
        ]
    },
    {
        "category": "🔊 VOICE CHANNELS",
        "channels": [
            {"name": "🔊 General Voice", "type": "voice"},
            {"name": "🔊 Lounge 1", "type": "voice"}
        ]
    },
    {
        "category": "🛡️ STAFF ZONE",
        "staff_only": True,
        "channels": [
            {"name": "🛡️-staff-chat", "type": "text", "topic": "Private discussions for server staff and admins."},
            {"name": "📜-mod-logs", "type": "text", "topic": "Audit logs, moderation actions, and security alerts."},
            {"name": "🎛️-mod-panel", "type": "text", "topic": "Staff control center: execute moderation, billing, role fixes, and panel refreshes via buttons."}
        ]
    }
]

def get_blueprint_category_names() -> set[str]:
    cats = {sec["category"] for sec in FORMAT_SERVER_BLUEPRINT}
    cats.add("📁 TICKETS")
    cats.add("🛍️ CVS & SAVINGS")
    cats.add("🔒 PRIVATE CVS")
    return cats

def get_blueprint_channel_names() -> set[str]:
    names = set()
    for sec in FORMAT_SERVER_BLUEPRINT:
        for ch in sec["channels"]:
            names.add(ch["name"])
    return names

def is_preserved_channel(channel: Any, mode: str = "clean_old") -> bool:
    """Returns True if the channel or category must NOT be deleted during cleanup."""
    if channel is None:
        return True
    # CRITICAL SAFEGUARD: Never touch #form-automation under any circumstance
    if is_protected_channel(channel):
        return True

    if mode == "clean_old":
        ch_name = getattr(channel, "name", "")
        if isinstance(channel, discord.CategoryChannel):
            if ch_name in get_blueprint_category_names():
                return True
        if ch_name in get_blueprint_channel_names():
            return True
        if ch_name.startswith(("ticket-", "order-", "cart-")):
            return True
        parent = getattr(channel, "category", None)
        if parent and getattr(parent, "name", "") == "📁 TICKETS":
            return True

    return False

async def purge_channels_helper(
    guild: discord.Guild,
    mode: str = "clean_old",
    invoking_channel_id: Optional[int] = None,
    progress_callback: Optional[Any] = None
) -> int:
    """Safely deletes non-preserved channels in guild according to mode.
    Guarantees #form-automation is NEVER deleted.
    Deletes the invoking channel last (if targeted).
    Returns total count of deleted channels & categories.
    """
    to_delete_channels = []
    to_delete_categories = []

    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            if not is_preserved_channel(ch, mode=mode):
                to_delete_categories.append(ch)
        else:
            if not is_preserved_channel(ch, mode=mode):
                to_delete_channels.append(ch)

    invoking_channel = None
    if invoking_channel_id:
        for i, ch in enumerate(to_delete_channels):
            if ch.id == invoking_channel_id:
                invoking_channel = to_delete_channels.pop(i)
                break

    total_to_delete = len(to_delete_channels) + (1 if invoking_channel else 0) + len(to_delete_categories)
    deleted_count = 0

    # 1. Delete standard channels first
    for ch in to_delete_channels:
        try:
            if not is_protected_channel(ch):
                await ch.delete(reason="Channel cleanup: removing previous unformatted channel")
                deleted_count += 1
                if progress_callback:
                    await progress_callback(deleted_count, total_to_delete, ch.name)
                await asyncio.sleep(0.35)
        except Exception as e:
            print(f"⚠️ Could not delete channel #{getattr(ch, 'name', '')}: {e}", file=sys.stderr)

    # 2. Delete empty non-preserved categories next
    for cat in to_delete_categories:
        try:
            if not is_protected_channel(cat):
                # Never delete category if it holds a protected channel
                if any(is_protected_channel(c) for c in cat.channels):
                    continue
                await cat.delete(reason="Channel cleanup: removing previous category")
                deleted_count += 1
                if progress_callback:
                    await progress_callback(deleted_count, total_to_delete, cat.name)
                await asyncio.sleep(0.35)
        except Exception as e:
            print(f"⚠️ Could not delete category {getattr(cat, 'name', '')}: {e}", file=sys.stderr)

    # 3. Delete invoking channel last if targeted
    if invoking_channel:
        try:
            if not is_protected_channel(invoking_channel):
                await invoking_channel.delete(reason="Channel cleanup: removing previous channel (invoker)")
                deleted_count += 1
        except Exception as e:
            print(f"⚠️ Could not delete invoking channel #{getattr(invoking_channel, 'name', '')}: {e}", file=sys.stderr)

    return deleted_count

# --- STAFF MOD PANEL MODALS ---

class ModWarnModal(discord.ui.Modal, title="⚠️ Issue Member Warning"):
    member_query = discord.ui.TextInput(
        label="Member (Mention, ID, or Username)",
        placeholder="e.g. @username or 123456789012345678",
        required=True
    )
    reason = discord.ui.TextInput(
        label="Reason for Warning",
        placeholder="Reason for official warning...",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = resolve_member_from_input(guild, self.member_query.value)
        if not member:
            await interaction.response.send_message(f"❌ Could not find member `{self.member_query.value}` in this server.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and interaction.user != guild.owner and not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ You cannot warn a member with an equal or higher role than you.", ephemeral=True)
            return

        guild_id = str(guild.id)
        user_id = str(member.id)
        key = f"{guild_id}_{user_id}"
        record = {
            "reason": self.reason.value,
            "moderator": str(interaction.user),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        }
        warnings_db.setdefault(key, []).append(record)
        save_warnings(warnings_db)
        count = len(warnings_db[key])
        case_id = log_mod_case(guild.id, "Warning", str(member), str(interaction.user), self.reason.value, f"Active warning count: {count}")

        embed = discord.Embed(title="⚠️ Official Warning Issued", color=COLOR_WARN)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Warning Count", value=f"**#{count}**", inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=f"`{self.reason.value}`", inline=False)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await interaction.response.send_message(embed=embed)


class ModTimeoutModal(discord.ui.Modal, title="⏱️ Timeout Member"):
    member_query = discord.ui.TextInput(
        label="Member (Mention, ID, or Username)",
        placeholder="e.g. @username or 1234567890",
        required=True
    )
    duration = discord.ui.TextInput(
        label="Duration (e.g. 5m, 10m, 1h, 1d)",
        placeholder="10m",
        default="10m",
        required=True
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Reason for timeout...",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = resolve_member_from_input(guild, self.member_query.value)
        if not member:
            await interaction.response.send_message(f"❌ Could not find member `{self.member_query.value}` in this server.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and interaction.user != guild.owner and not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ You cannot timeout a member with an equal or higher role than you.", ephemeral=True)
            return

        td = parse_duration(self.duration.value)
        if not td:
            await interaction.response.send_message("❌ Invalid duration format. Examples: `10m`, `1h`, `1d`.", ephemeral=True)
            return
        if td > timedelta(days=28):
            await interaction.response.send_message("❌ Discord timeouts cannot exceed 28 days.", ephemeral=True)
            return

        r_text = self.reason.value or "No reason provided"
        try:
            await member.timeout(td, reason=f"{r_text} (by {interaction.user})")
            case_id = log_mod_case(guild.id, "Timeout", str(member), str(interaction.user), r_text, f"Duration: {self.duration.value}")
            embed = discord.Embed(title="🔇 Member Timed Out", color=COLOR_WARN)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Duration", value=f"**{self.duration.value}**", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=f"`{r_text}`", inline=False)
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to timeout this member.", ephemeral=True)


class ModKickModal(discord.ui.Modal, title="👢 Kick Member"):
    member_query = discord.ui.TextInput(
        label="Member (Mention, ID, or Username)",
        placeholder="e.g. @username or 1234567890",
        required=True
    )
    reason = discord.ui.TextInput(
        label="Reason for Kick",
        placeholder="Reason for kick...",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can kick members.", ephemeral=True)
            return
        guild = interaction.guild
        member = resolve_member_from_input(guild, self.member_query.value)
        if not member:
            await interaction.response.send_message(f"❌ Could not find member `{self.member_query.value}` in this server.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and interaction.user != guild.owner and not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ You cannot kick a member with an equal or higher role than you.", ephemeral=True)
            return

        r_text = self.reason.value or "No reason provided"
        try:
            await member.kick(reason=f"{r_text} (by {interaction.user})")
            case_id = log_mod_case(guild.id, "Kick", str(member), str(interaction.user), r_text)
            embed = discord.Embed(title="👢 Member Kicked", color=COLOR_WARN)
            embed.add_field(name="Member", value=f"**{member}** (`{member.id}`)", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=f"`{r_text}`", inline=False)
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to kick this member.", ephemeral=True)


class ModBanModal(discord.ui.Modal, title="🔨 Ban Member"):
    member_query = discord.ui.TextInput(
        label="Member (Mention, ID, or Username)",
        placeholder="e.g. @username or 1234567890",
        required=True
    )
    days = discord.ui.TextInput(
        label="Delete Message Days (0-7)",
        placeholder="0",
        default="0",
        required=False
    )
    reason = discord.ui.TextInput(
        label="Reason for Ban",
        placeholder="Reason for ban...",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can ban members.", ephemeral=True)
            return
        guild = interaction.guild
        member = resolve_member_from_input(guild, self.member_query.value)
        if not member:
            await interaction.response.send_message(f"❌ Could not find member `{self.member_query.value}` in this server.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and interaction.user != guild.owner and not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ You cannot ban a member with an equal or higher role than you.", ephemeral=True)
            return

        try:
            delete_days = max(0, min(7, int(self.days.value or "0")))
        except ValueError:
            delete_days = 0

        r_text = self.reason.value or "No reason provided"
        try:
            await member.ban(delete_message_days=delete_days, reason=f"{r_text} (by {interaction.user})")
            case_id = log_mod_case(guild.id, "Ban", str(member), str(interaction.user), r_text, f"Purged {delete_days}d messages")
            embed = discord.Embed(title="🔨 Member Banned", color=COLOR_ERROR)
            embed.add_field(name="Member", value=f"**{member}** (`{member.id}`)", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=f"`{r_text}`", inline=False)
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to ban this member.", ephemeral=True)


class ModPurgeModal(discord.ui.Modal, title="🧹 Bulk Purge Messages"):
    channel_query = discord.ui.TextInput(
        label="Target Channel (#channel, name, or blank)",
        placeholder="e.g. #general-chat, general, or leave blank for here",
        required=False
    )
    count = discord.ui.TextInput(
        label="Message Count (1-100)",
        placeholder="25",
        default="25",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ch_val = (self.channel_query.value or "").strip()
        if ch_val and ch_val.lower() != "here":
            target_ch = resolve_channel_from_input(guild, ch_val)
            if not target_ch:
                await interaction.response.send_message(
                    f"❌ Could not find text channel matching `{ch_val}` in this server. Please enter a channel mention (<#channel>), name, or ID.",
                    ephemeral=True
                )
                return
        else:
            target_ch = interaction.channel

        if not isinstance(target_ch, discord.TextChannel):
            await interaction.response.send_message("❌ Target channel is not a valid text channel.", ephemeral=True)
            return

        if is_protected_channel(target_ch):
            await interaction.response.send_message("❌ Cannot purge messages in a protected channel (#form-automation).", ephemeral=True)
            return

        count_str = (self.count.value or "").strip()
        try:
            limit_val = max(1, min(100, int(count_str or "25")))
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid message count between 1 and 100.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await target_ch.purge(limit=limit_val)
            case_id = log_mod_case(
                guild_id=guild.id if guild else None,
                action="Purge",
                target=target_ch.mention,
                moderator=str(interaction.user),
                reason=f"Purged {len(deleted)} messages via Mod Panel"
            )
            await interaction.followup.send(
                f"🧹 Successfully purged **{len(deleted)}** message(s) from {target_ch.mention}! *(Case `#CASE-{case_id:04d}`)*",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Bot is missing permissions to delete messages in {target_ch.mention}.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error purging messages in {target_ch.mention}: {e}",
                ephemeral=True
            )


class ModSlowmodeModal(discord.ui.Modal, title="⏳ Set Channel Slowmode"):
    channel_query = discord.ui.TextInput(
        label="Target Channel (#channel, name, or blank)",
        placeholder="e.g. #general-chat, general, or leave blank for here",
        required=False
    )
    seconds = discord.ui.TextInput(
        label="Slowmode Delay (Seconds, 0 to disable)",
        placeholder="5",
        default="5",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        ch_val = (self.channel_query.value or "").strip()
        if ch_val and ch_val.lower() != "here":
            target_ch = resolve_channel_from_input(guild, ch_val)
            if not target_ch:
                await interaction.response.send_message(
                    f"❌ Could not find text channel matching `{ch_val}` in this server.",
                    ephemeral=True
                )
                return
        else:
            target_ch = interaction.channel

        if not isinstance(target_ch, discord.TextChannel):
            await interaction.response.send_message("❌ Target channel is not a valid text channel.", ephemeral=True)
            return

        if is_protected_channel(target_ch):
            await interaction.response.send_message("❌ Cannot set slowmode on protected channel (#form-automation).", ephemeral=True)
            return

        try:
            sec_val = max(0, min(21600, int(self.seconds.value.strip())))
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number of seconds (0 to 21600).", ephemeral=True)
            return

        try:
            await target_ch.edit(slowmode_delay=sec_val, reason=f"Slowmode updated via Mod Panel by {interaction.user}")
            if sec_val == 0:
                await interaction.response.send_message(f"⚡ Slowmode **disabled** for {target_ch.mention}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⏳ Slowmode set to **{sec_val} seconds** for {target_ch.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Bot is missing permissions to edit {target_ch.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error updating slowmode on {target_ch.mention}: {e}", ephemeral=True)


class ModDMModal(discord.ui.Modal, title="📬 Send Direct Message (DM)"):
    user_query = discord.ui.TextInput(
        label="Recipient (Mention, Username, or ID)",
        placeholder="e.g. @username, username, or 1234567890",
        required=True
    )
    message = discord.ui.TextInput(
        label="Message to Send",
        placeholder="Type your message here...",
        style=discord.TextStyle.paragraph,
        required=True
    )
    anonymous = discord.ui.TextInput(
        label="Anonymous? (yes / no)",
        placeholder="no (default: shows your staff name)",
        default="no",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        target = await resolve_user_or_member(guild, self.user_query.value.strip())
        if not target:
            await interaction.response.send_message(
                f"❌ Could not find user `{self.user_query.value}`. Please provide a valid mention (@username), username, or User ID.",
                ephemeral=True
            )
            return

        is_anon = (self.anonymous.value or "").strip().lower() in ("yes", "true", "1", "y")
        msg_text = self.message.value.strip()

        embed = discord.Embed(
            title=f"📬 Direct Message from {guild.name if guild else 'Server Staff'}",
            description=msg_text,
            color=COLOR_PRIMARY
        )
        if not is_anon:
            embed.set_author(
                name=f"Sent by {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url if hasattr(interaction.user, 'display_avatar') else None
            )
        else:
            embed.set_author(
                name=f"Official Server Communication • {guild.name if guild else 'AIO Bot'}",
                icon_url=guild.icon.url if guild and guild.icon else None
            )
        embed.set_footer(text="AIO Bot Direct Messaging • Reply in server tickets if you need assistance")

        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Could not deliver DM to **{target}** (`{target.id}`). Their direct messages are disabled for server members or they have blocked the bot.",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to send DM to **{target}**: {e}",
                ephemeral=True
            )
            return

        case_id = log_mod_case(
            guild_id=guild.id if guild else None,
            action="Direct Message",
            target=str(target),
            moderator=str(interaction.user),
            reason=msg_text[:100]
        )

        confirm_embed = discord.Embed(
            title="✅ Direct Message Sent",
            description=f"Successfully delivered direct message to {target.mention} (`{target.id}`).",
            color=COLOR_SUCCESS
        )
        confirm_embed.add_field(name="Recipient", value=f"**{target}** (`{target.id}`)", inline=True)
        confirm_embed.add_field(name="Sent By", value=interaction.user.mention if not is_anon else "*Anonymous Staff*", inline=True)
        confirm_embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        confirm_embed.add_field(name="Message", value=f">>> {msg_text[:1000]}", inline=False)
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


class ModInvoiceModal(discord.ui.Modal, title="💵 Create & Send Customer Invoice"):
    price = discord.ui.TextInput(
        label="Total Due / Price ($)",
        placeholder="e.g. 10 or 15.00",
        required=True
    )
    cashapp = discord.ui.TextInput(
        label="Cash App Handle (Optional if cached)",
        placeholder="e.g. $cody (leave blank to use saved handle)",
        required=False
    )
    venmo = discord.ui.TextInput(
        label="Venmo Handle (Optional if cached)",
        placeholder="e.g. @cody (leave blank to use saved handle)",
        required=False
    )
    item = discord.ui.TextInput(
        label="Item / Service Name (Optional)",
        placeholder="e.g. Taco Bell Preloaded Account",
        required=False
    )
    customer = discord.ui.TextInput(
        label="Customer (Mention, ID, or Username)",
        placeholder="e.g. @customer (optional)",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        cust_mem = resolve_member_from_input(guild, self.customer.value) if self.customer.value else None
        embed, target_cust, err = create_invoice_embed(
            author=interaction.user,
            channel=interaction.channel,
            price_str=self.price.value,
            cashapp=self.cashapp.value or None,
            venmo=self.venmo.value or None,
            item=self.item.value or None,
            customer=cust_mem
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        ping_cust = target_cust.mention if target_cust else ""
        await interaction.channel.send(content=f"{ping_cust} Here is your official invoice:", embed=embed)
        await interaction.response.send_message("✅ Invoice created and sent to this channel!", ephemeral=True)


# --- COUPON OPTIMIZER HUB & PRIVATE ROOM VIEWS ---

def build_coupon_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛒 CVS & Retail Coupon Optimizer Hub",
        description=(
            "Welcome to the **CVS & Retail Coupon Optimizer**!\n\n"
            "Build optimized shopping trips, stack manufacturer and store coupons, calculate exact cashier sequencing, and maximize your savings!\n\n"
            "**How to start:**\n"
            "▸ Click **[🛒 Open Private Optimizer Room]** below\n"
            "▸ A personal room (`cart-{your-username}`) will be created just for you\n"
            "▸ Add your items, input your coupons, and run the optimizer\n"
            "▸ When you are finished, click **🔒 Close Room** to cleanly delete your room\n\n"
            "Ready to save big? Open your private room now!"
        ),
        color=COLOR_PRIMARY
    )
    embed.set_footer(text="AIO Bot Coupon Optimizer • Your cart and savings calculations remain 100% private")
    return embed


class CouponHubLaunchView(discord.ui.View):
    """Persistent view placed in #🛒-coupon-optimizer for members to spawn their private room."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Private Optimizer Room",
        style=discord.ButtonStyle.success,
        emoji="🛒",
        custom_id="coupon_hub_launch_room"
    )
    async def btn_open_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This action can only be run in a server.", ephemeral=True)
            return

        clean_username = "".join(c for c in interaction.user.name.lower() if c.isalnum() or c in "-_")[:18] or "user"
        room_name = f"cart-{clean_username}"

        # Check if an existing room already exists for this member
        existing = discord.utils.get(guild.text_channels, name=room_name)
        if existing:
            await interaction.response.send_message(
                f"⚠️ You already have an active private room: {existing.mention}!\n"
                f"Please finish your session there or click **Close Room** inside it.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Find or determine category (same category as current channel, or '🔒 PRIVATE CVS')
        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None
        if not category:
            for cat in guild.categories:
                if "private cvs" in cat.name.lower() or "cvs" in cat.name.lower():
                    category = cat
                    break

        founder_role = get_founder_role(guild)
        mod_role = get_moderator_role(guild)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_channels=True,
                manage_messages=True
            )
        }
        if founder_role:
            overwrites[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            new_room = await guild.create_text_channel(
                name=room_name,
                category=category,
                topic=f"Private coupon optimizer room for {interaction.user.name} ({interaction.user.id}).",
                overwrites=overwrites
            )

            welcome_embed = discord.Embed(
                title=f"🛒 {interaction.user.display_name}'s Private Optimizer Room",
                description=(
                    f"Welcome {interaction.user.mention}! This is your private shopping and coupon optimization room.\n\n"
                    "**Quick Controls:**\n"
                    "• **➕ Add Items:** Enter items and prices to build your cart\n"
                    "• **🎟️ Load Coupons:** Load digital manufacturer and store coupons / ExtraBucks\n"
                    "• **📊 Optimize Plan:** Calculate optimal cashier scanning order and max savings\n"
                    "• **↩️ Undo Last:** Remove the most recently added item\n"
                    "• **✅ Checkout:** Complete your cart trip summary\n"
                    "• **🔒 Close Room:** Cleanly delete this private room when you're done\n\n"
                    "*Use the buttons below to manage your session.*"
                ),
                color=COLOR_SUCCESS
            )
            welcome_embed.set_footer(text="Private CVS Session • Click Close Room when finished")

            view = CouponRoomControlView(interaction.user.id)
            await new_room.send(embed=welcome_embed, view=view)

            await interaction.followup.send(
                f"✅ Your private coupon room has been created: {new_room.mention}!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create private room: {e}", ephemeral=True)


class CouponRoomControlView(discord.ui.View):
    """Interactive buttons inside an active private coupon optimizer room."""
    def __init__(self, room_owner_id: int = 0):
        super().__init__(timeout=None)
        self.room_owner_id = room_owner_id

    def _is_owner_or_staff(self, user: discord.Member, channel: Any) -> bool:
        if self.room_owner_id != 0 and user.id == self.room_owner_id:
            return True
        if is_staff_member(user):
            return True
        topic = getattr(channel, "topic", "") or ""
        if str(user.id) in topic:
            return True
        ch_name = getattr(channel, "name", "")
        clean_user = "".join(c for c in user.name.lower() if c.isalnum() or c in "-_")
        if clean_user and clean_user in ch_name.lower():
            return True
        return False

    @discord.ui.button(label="Add Items", style=discord.ButtonStyle.success, emoji="➕", custom_id="croom_add", row=0)
    async def btn_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddItemModal(interaction.user.id))

    @discord.ui.button(label="Load Coupons", style=discord.ButtonStyle.primary, emoji="🎟️", custom_id="croom_coupons", row=0)
    async def btn_coupons(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LoadCouponsModal(interaction.user.id))

    @discord.ui.button(label="Optimize Plan", style=discord.ButtonStyle.primary, emoji="📊", custom_id="croom_opt", row=0)
    async def btn_opt(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Cart is empty! Click **Add Items** first.", ephemeral=True)
            return
        embed = build_strategy_embed(session["items"], session["coupons"])
        await interaction.response.send_message(embed=embed, view=CouponRoomControlView(self.room_owner_id))

    @discord.ui.button(label="Undo Last", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="croom_undo", row=1)
    async def btn_undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Nothing to undo — your cart is empty!", ephemeral=True)
            return
        removed = session["items"].pop()
        subtotal = sum(i['price'] for i in session["items"])
        await interaction.response.send_message(
            f"↩️ Removed **{removed['name']}** (${removed['price']:.2f}). Subtotal: **${subtotal:.2f}**",
            ephemeral=True
        )

    @discord.ui.button(label="Checkout", style=discord.ButtonStyle.success, emoji="✅", custom_id="croom_checkout", row=1)
    async def btn_checkout(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(interaction.user.id)
        if not session["items"]:
            await interaction.response.send_message("❌ Cart is empty — nothing to check out!", ephemeral=True)
            return
        items = list(session["items"])
        coupons = list(session["coupons"])
        embed = _do_checkout(items, coupons)[0]
        reset_session(interaction.user.id)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Clear Cart", style=discord.ButtonStyle.secondary, emoji="🧹", custom_id="croom_clear", row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        reset_session(interaction.user.id)
        await interaction.response.send_message("🧹 Your cart and coupons have been cleared!", ephemeral=True)

    @discord.ui.button(label="Close Room", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="croom_close", row=2)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner_or_staff(interaction.user, interaction.channel):
            await interaction.response.send_message("❌ Only the room owner or server staff can close this room.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing private coupon room in 3 seconds...", ephemeral=False)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Private coupon room closed by {interaction.user.display_name}")
        except Exception as e:
            print(f"⚠️ Could not delete coupon room channel: {e}", file=sys.stderr)


# --- STAFF MOD PANEL BUTTON VIEW & EMBED ---

def build_staff_modpanel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎛️ Staff Control Center & Moderation Panel",
        description=(
            "Welcome to the **Staff Command Hub**. Execute server moderation, channel controls, "
            "order billing, direct messaging, and system refreshes directly using the interactive buttons below.\n\n"
            "**🛡️ Member Discipline:**\n"
            "• **⚠️ Warn:** Issue an official logged warning to a member\n"
            "• **⏱️ Timeout:** Temporarily mute/timeout a member\n"
            "• **👢 Kick:** *(Admin Only)* Remove a member from the server\n"
            "• **🔨 Ban:** *(Admin Only)* Ban a member and optionally purge messages\n"
            "• **🧹 Purge:** Clean up recent messages in any specified channel\n\n"
            "**🔒 Channel & Server Security:**\n"
            "• **🔒 Lock / 🔓 Unlock:** Restrict or restore messaging in this channel\n"
            "• **⏳ Slowmode:** Configure channel message cooldown\n"
            "• **🚨 Server Lockdown:** *(Admin Only)* Emergency freeze across text channels\n\n"
            "**💵 Store, Billing & Member Outreach:**\n"
            "• **💵 Create Invoice:** Generate official bill with CashApp / Venmo links\n"
            "• **📈 Order Stats:** View completed sales, revenue & order log\n"
            "• **📬 DM Member:** Send an official direct message from the bot\n"
            "• **👥 Fix Roles:** *(Admin Only)* Consolidate Moderator roles & remove redundant Staff\n"
            "• **🌮 Refresh Store:** *(Admin Only)* Update `#🌮🍕-food-rewards` with latest stock\n\n"
            "**🎟️ Panels & Server Info:**\n"
            "• **🎟️ Refresh Tickets:** *(Admin Only)* Refresh the ticket deployment panel\n"
            "• **🛒 Refresh Hub:** *(Admin Only)* Refresh the Coupon Optimizer Hub\n"
            "• **ℹ️ Server Info:** View guild statistics and diagnostics"
        ),
        color=COLOR_PRIMARY
    )
    embed.set_footer(text="AIO Bot Staff Control Center • Admin Only actions marked accordingly")
    return embed


class StaffModPanelButtonView(discord.ui.View):
    """Persistent button-driven moderation, billing, and channel control center."""
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_staff_member(interaction.user):
            await interaction.response.send_message(
                "⛔ **Access Denied**: This control panel is strictly reserved for Server Founders and Staff.",
                ephemeral=True
            )
            return False
        return True

    # --- ROW 0: MEMBER DISCIPLINE ---
    @discord.ui.button(label="Warn", style=discord.ButtonStyle.secondary, emoji="⚠️", custom_id="modpanel_warn", row=0)
    async def btn_warn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModWarnModal())

    @discord.ui.button(label="Timeout", style=discord.ButtonStyle.secondary, emoji="⏱️", custom_id="modpanel_timeout", row=0)
    async def btn_timeout(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModTimeoutModal())

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger, emoji="👢", custom_id="modpanel_kick", row=0)
    async def btn_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can kick members.", ephemeral=True)
            return
        await interaction.response.send_modal(ModKickModal())

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨", custom_id="modpanel_ban", row=0)
    async def btn_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can ban members.", ephemeral=True)
            return
        await interaction.response.send_modal(ModBanModal())

    @discord.ui.button(label="Purge", style=discord.ButtonStyle.secondary, emoji="🧹", custom_id="modpanel_purge", row=0)
    async def btn_purge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModPurgeModal())

    # --- ROW 1: CHANNEL & SERVER CONTROLS ---
    @discord.ui.button(label="Lock Channel", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="modpanel_lock", row=1)
    async def btn_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_protected_channel(interaction.channel):
            await interaction.response.send_message("❌ Cannot lock protected channel (#form-automation).", ephemeral=True)
            return
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False, reason=f"Channel locked via Mod Panel by {interaction.user}")
        await interaction.response.send_message(f"🔒 {interaction.channel.mention} has been **locked** by {interaction.user.mention}.")

    @discord.ui.button(label="Unlock Channel", style=discord.ButtonStyle.secondary, emoji="🔓", custom_id="modpanel_unlock", row=1)
    async def btn_unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None, reason=f"Channel unlocked via Mod Panel by {interaction.user}")
        await interaction.response.send_message(f"🔓 {interaction.channel.mention} has been **unlocked** by {interaction.user.mention}.")

    @discord.ui.button(label="Slowmode", style=discord.ButtonStyle.secondary, emoji="⏳", custom_id="modpanel_slowmode", row=1)
    async def btn_slowmode(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModSlowmodeModal())

    @discord.ui.button(label="Server Lockdown", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="modpanel_lockdown", row=1)
    async def btn_lockdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can activate a server lockdown.", ephemeral=True)
            return
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return
        gen_ch = discord.utils.get(guild.text_channels, name="💬-general-chat") or interaction.channel
        curr_lock = (gen_ch.overwrites_for(guild.default_role).send_messages is False)
        new_lock = not curr_lock
        action_name = "on" if new_lock else "off"

        await interaction.response.defer(ephemeral=False)
        changed_count = 0
        for ch in guild.text_channels:
            if is_protected_channel(ch):
                continue
            perms = ch.overwrites_for(guild.default_role)
            if new_lock:
                if perms.send_messages is not False:
                    perms.send_messages = False
                    try:
                        await ch.set_permissions(guild.default_role, overwrite=perms, reason=f"Lockdown ON via Mod Panel by {interaction.user}")
                        changed_count += 1
                    except Exception:
                        pass
            else:
                if perms.send_messages is False:
                    perms.send_messages = None
                    try:
                        await ch.set_permissions(guild.default_role, overwrite=perms, reason=f"Lockdown OFF via Mod Panel by {interaction.user}")
                        changed_count += 1
                    except Exception:
                        pass

        case_id = log_mod_case(
            guild_id=guild.id,
            action=f"Lockdown {action_name.upper()}",
            target=f"{changed_count} channels",
            moderator=str(interaction.user),
            reason="Triggered via Mod Panel"
        )
        embed = discord.Embed(
            title=f"🚨 Server Lockdown {'ACTIVATED' if new_lock else 'DEACTIVATED'}",
            description=f"Server-wide message permissions have been **{'LOCKED' if new_lock else 'UNLOCKED'}**.",
            color=COLOR_ERROR if new_lock else COLOR_SUCCESS
        )
        embed.add_field(name="Channels Updated", value=f"**{changed_count}** text channels", inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await interaction.followup.send(embed=embed)

    # --- ROW 2: STORE, BILLING & ROLES ---
    @discord.ui.button(label="Create Invoice", style=discord.ButtonStyle.success, emoji="💵", custom_id="modpanel_invoice", row=2)
    async def btn_invoice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModInvoiceModal())

    @discord.ui.button(label="Order Stats", style=discord.ButtonStyle.primary, emoji="📈", custom_id="modpanel_orderstats", row=2)
    async def btn_orderstats(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_order_stats_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="DM Member", style=discord.ButtonStyle.primary, emoji="📬", custom_id="modpanel_dm", row=2)
    async def btn_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModDMModal())

    @discord.ui.button(label="Fix Roles", style=discord.ButtonStyle.primary, emoji="👥", custom_id="modpanel_fixroles", row=2)
    async def btn_fixroles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can audit and fix server roles.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        summary = await fix_server_roles(interaction.guild)
        deleted_mods = summary.get("moderators_merged", 0)
        removed_staff = summary.get("staff_role_removed", False)
        unhoisted = summary.get("bot_roles_unhoisted", 0)
        stripped = summary.get("bot_roles_stripped", 0)
        desc = (
            f"Role hierarchy audit and cleanup complete!\n\n"
            f"• 🧹 Duplicate Moderator Roles Merged: **{deleted_mods}**\n"
            f"• 🗑️ Redundant Staff Role Removed: **{'Yes' if removed_staff else 'No'}**\n"
            f"• 🔽 Bot Roles Lowered / Unhoisted: **{unhoisted}** *(Bot will not display above you)*\n"
            f"• 🛡️ Staff Roles Stripped from Bot: **{stripped}**\n"
        )
        if summary.get("founder_role"):
            desc += f"• 👑 Active Founder Role: **@{summary['founder_role']}** (Assigned to Server Owner)\n"
        if summary.get("bot_is_top"):
            bot_name = interaction.guild.me.display_name if interaction.guild and interaction.guild.me else "Bot"
            desc += (
                f"\n> 👑 **Role Hierarchy Tip:**\n"
                f"> Discord security prevents bots from dragging their own role below other roles via the API.\n"
                f"> **To place yourself at the very top of Server Settings:**\n"
                f"> Go to **Server Settings ➔ Roles ➔ Drag @Founder ABOVE @{bot_name}**."
            )
        embed = discord.Embed(title="👥 Roles & Hierarchy Repaired", description=desc, color=COLOR_SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Refresh Store", style=discord.ButtonStyle.success, emoji="🌮", custom_id="modpanel_refresh_food", row=2)
    async def btn_refresh_food(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can refresh the store channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ch = None
        for c in guild.text_channels:
            if "food" in c.name.lower() or "rewards" in c.name.lower():
                ch = c
                break
        if ch:
            name = await refresh_channel_content(ch, interaction.user.id)
            await interaction.followup.send(f"✅ Refreshed store in {ch.mention}!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Could not find food rewards channel in this server.", ephemeral=True)

    # --- ROW 3: PANELS & INFO ---
    @discord.ui.button(label="Refresh Tickets", style=discord.ButtonStyle.primary, emoji="🎟️", custom_id="modpanel_refresh_tickets", row=3)
    async def btn_refresh_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can refresh the ticket channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ch = None
        for c in guild.text_channels:
            if "ticket" in c.name.lower() or "open" in c.name.lower():
                ch = c
                break
        if ch:
            name = await refresh_channel_content(ch, interaction.user.id)
            await interaction.followup.send(f"✅ Refreshed ticket panel in {ch.mention}!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Could not find ticket panel channel in this server.", ephemeral=True)

    @discord.ui.button(label="Refresh Hub", style=discord.ButtonStyle.primary, emoji="🛒", custom_id="modpanel_refresh_coupon", row=3)
    async def btn_refresh_coupon(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can refresh the coupon hub.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ch = None
        for c in guild.text_channels:
            if "coupon" in c.name.lower() or "optimizer" in c.name.lower():
                ch = c
                break
        if ch:
            name = await refresh_channel_content(ch, interaction.user.id)
            await interaction.followup.send(f"✅ Refreshed coupon hub in {ch.mention}!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Could not find coupon optimizer channel in this server.", ephemeral=True)

    @discord.ui.button(label="Server Info", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="modpanel_serverinfo", row=3)
    async def btn_serverinfo(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_serverinfo_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def refresh_channel_content(channel: discord.TextChannel, author_id: int, clear_history: bool = True) -> str:
    """
    Clears channel messages (if clear_history=True) and posts the latest
    updated embed/buttons for recognized blueprint channels (Food Rewards, Ticket Panel, Coupon Optimizer, Mod Panel).
    """
    if is_protected_channel(channel):
        raise ValueError("Protected channel (#form-automation) cannot be reset or cleared.")

    if clear_history:
        try:
            await channel.purge(limit=100)
        except Exception as e:
            print(f"⚠️ Notice on channel purge #{channel.name}: {e}", file=sys.stderr)

    ch_name = channel.name.lower()

    if "food" in ch_name or "rewards" in ch_name:
        food_embed = build_food_accounts_embed()
        await channel.send(embed=food_embed, view=FoodAccountPurchaseView())
        return "🌮🍕 Fast Food Rewards Store"

    elif "ticket" in ch_name or "open" in ch_name:
        panel_embed = discord.Embed(
            title="🎫 Support & Order Help",
            description=(
                "Need assistance, have a question, or want to contact staff?\n\n"
                "**How it works:**\n"
                "▸ 🔒 A **private channel** is created just for you and staff\n"
                "▸ 👥 Only you and server staff can see it\n"
                "▸ ⚡ Staff will respond as soon as possible\n\n"
                "Click the button below to open your ticket."
            ),
            color=COLOR_PRIMARY
        )
        panel_embed.set_footer(text="AIO Bot Custom Ticket Center • One ticket per user")
        await channel.send(embed=panel_embed, view=TicketLaunchView())
        return "🎫 Support & Order Ticket Panel"

    elif "coupon" in ch_name or "optimizer" in ch_name:
        hub_embed = build_coupon_hub_embed()
        await channel.send(embed=hub_embed, view=CouponHubLaunchView())
        return "🛒 CVS Coupon Optimizer Hub"

    elif "mod-panel" in ch_name or "modpanel" in ch_name:
        mod_embed = build_staff_modpanel_embed()
        await channel.send(embed=mod_embed, view=StaffModPanelButtonView())
        return "🎛️ Staff Control Center & Moderation Panel"

    else:
        for sec in FORMAT_SERVER_BLUEPRINT:
            for c_def in sec.get("channels", []):
                if c_def["name"].lower() == channel.name.lower() or channel.name.lower() in c_def["name"].lower():
                    if c_def.get("topic"):
                        try:
                            await channel.edit(topic=c_def["topic"])
                        except Exception:
                            pass
                    return f"Channel #{channel.name}"
        return f"Channel #{channel.name}"

async def execute_format_server(guild: discord.Guild, author: discord.Member, clean_old: bool = False) -> discord.Embed:
    created_cats = 0
    created_channels = 0

    # Consolidate duplicate roles and remove redundant Staff role
    await fix_server_roles(guild)

    # Ensure Founder and single Moderator roles exist and are mentionable
    founder_role = get_founder_role(guild)
    if not founder_role and guild.me.guild_permissions.manage_roles:
        try:
            founder_role = await guild.create_role(
                name="Founder",
                color=discord.Color.gold(),
                hoist=True,
                mentionable=True,
                reason="Created Founder role during server formatting"
            )
            if guild.owner and guild.me.top_role > founder_role:
                try:
                    await guild.owner.add_roles(founder_role, reason="Assigned Founder role to server owner")
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ Could not auto-create Founder role: {e}", file=sys.stderr)
    elif founder_role and not founder_role.mentionable and guild.me.guild_permissions.manage_roles:
        try:
            if guild.me.top_role > founder_role:
                await founder_role.edit(mentionable=True, reason="Made Founder role mentionable for ticket pings")
        except Exception:
            pass

    mod_role = get_moderator_role(guild)
    if not mod_role and guild.me.guild_permissions.manage_roles:
        try:
            mod_role = await guild.create_role(
                name="Moderator",
                color=discord.Color.blue(),
                hoist=True,
                mentionable=True,
                reason="Created Moderator role during server formatting"
            )
        except Exception as e:
            print(f"⚠️ Could not auto-create Moderator role: {e}", file=sys.stderr)
    elif mod_role and not mod_role.mentionable and guild.me.guild_permissions.manage_roles:
        try:
            if guild.me.top_role > mod_role:
                await mod_role.edit(mentionable=True, reason="Made Moderator role mentionable for staff pings")
        except Exception:
            pass

    for section in FORMAT_SERVER_BLUEPRINT:
        cat_name = section["category"]
        cat = discord.utils.get(guild.categories, name=cat_name)

        cat_overwrites = {}
        if section.get("staff_only") or section.get("private"):
            cat_overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            cat_overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True)
            if founder_role:
                cat_overwrites[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            if mod_role:
                cat_overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            for role in guild.roles:
                if role.permissions.administrator or role.permissions.manage_channels or role.name.lower() in ("moderator", "moderators", "mod", "mods", "admin", "administrator", "founder", "founders", "owner"):
                    cat_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        elif section.get("read_only"):
            cat_overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=False, add_reactions=True)
            cat_overwrites[guild.me] = discord.PermissionOverwrite(send_messages=True, manage_channels=True)

        if not cat:
            cat = await guild.create_category(cat_name, overwrites=cat_overwrites)
            created_cats += 1

        for ch_def in section["channels"]:
            ch_name = ch_def["name"]
            ch_type = ch_def["type"]

            if ch_type == "text":
                existing = discord.utils.get(guild.text_channels, name=ch_name)
                # CRITICAL SAFEGUARD: Never touch #form-automation under any circumstance
                if existing and is_protected_channel(existing):
                    continue

                if existing:
                    if existing.category_id != cat.id and not is_protected_channel(existing.category):
                        try:
                            await existing.edit(category=cat)
                        except Exception:
                            pass
                    # Refresh existing blueprint panel channels with latest info
                    if ch_name in ("📩-open-a-ticket", "🛒-coupon-optimizer", "🌮🍕-food-rewards", "🎛️-mod-panel"):
                        try:
                            await refresh_channel_content(existing, author.id, clear_history=clean_old)
                        except Exception as e:
                            print(f"⚠️ Error refreshing existing channel {ch_name}: {e}", file=sys.stderr)
                else:
                    ch_overwrites = {}
                    if section.get("private") or section.get("staff_only"):
                        ch_overwrites = dict(cat_overwrites)
                    elif section.get("read_only"):
                        ch_overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=False, add_reactions=True)
                    new_ch = await guild.create_text_channel(
                        name=ch_name,
                        category=cat,
                        topic=ch_def.get("topic", ""),
                        overwrites=ch_overwrites
                    )
                    created_channels += 1

                    if ch_name in ("📩-open-a-ticket", "🛒-coupon-optimizer", "🌮🍕-food-rewards", "🎛️-mod-panel"):
                        try:
                            await refresh_channel_content(new_ch, author.id, clear_history=False)
                        except Exception as e:
                            print(f"⚠️ Error deploying panel to new channel {ch_name}: {e}", file=sys.stderr)

            elif ch_type == "voice":
                existing_vc = discord.utils.get(guild.voice_channels, name=ch_name)
                if not existing_vc:
                    await guild.create_voice_channel(name=ch_name, category=cat)
                    created_channels += 1

    deleted_count = 0
    if clean_old:
        deleted_count = await purge_channels_helper(guild, mode="clean_old")

    opt_mention = get_channel_mention(guild, "🛒-coupon-optimizer", "#🛒-coupon-optimizer")
    ticket_mention = get_channel_mention(guild, "📩-open-a-ticket", "#📩-open-a-ticket")
    food_mention = get_channel_mention(guild, "🌮🍕-food-rewards", "#🌮🍕-food-rewards")
    mod_mention = get_channel_mention(guild, "🎛️-mod-panel", "#🎛️-mod-panel")
    fa_mention = get_channel_mention(guild, "form-automation", "#form-automation")

    desc = (
        f"Your server layout has been organized with clean categories and channels!\n\n"
        f"• 📁 **Categories Created/Organized:** {created_cats}\n"
        f"• 💬 **Channels Created/Positioned:** {created_channels}\n"
    )
    if clean_old:
        desc += f"• 🧹 **Previous Channels Cleaned:** {deleted_count} old channel(s) removed\n"
    desc += (
        f"• 🛡️ **Guaranteed Safeguard:** {fa_mention} was completely preserved and untouched.\n"
        f"• 🔒 **Private CVS Optimizer:** Active in {opt_mention} under `🔒 PRIVATE CVS` (Click button to open private room)\n"
        f"• 🎫 **Tickets Deployed:** Active in {ticket_mention}\n"
        f"• 🌮🍕 **Food Accounts Store Deployed:** Active in {food_mention}\n"
        f"• 🎛️ **Staff Mod Panel:** Active in {mod_mention}"
    )

    summary_embed = discord.Embed(
        title="🏗️ Server Layout Formatted Successfully",
        description=desc,
        color=COLOR_SUCCESS
    )
    summary_embed.set_footer(text="AIO Bot Server Architecture Suite")
    return summary_embed


class FormatServerConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("⛔ Only the command author can confirm this action.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Format & Clean Old Channels", style=discord.ButtonStyle.primary, emoji="🧹")
    async def btn_format_and_clean(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        status_msg = await interaction.followup.send("⏳ **Formatting server & cleaning previous channels...** Please wait.", ephemeral=False)

        invoking_id = interaction.channel_id
        summary_embed = await execute_format_server(guild, interaction.user, clean_old=True)

        # If current channel was purged, post to #bot-commands or announcements
        channel_still_exists = any(c.id == invoking_id for c in guild.channels)
        if channel_still_exists:
            try:
                await status_msg.edit(content=None, embed=summary_embed)
            except Exception:
                pass
        else:
            bot_ch = discord.utils.get(guild.text_channels, name="🤖-bot-commands") or discord.utils.get(guild.text_channels, name="📢-announcements")
            if bot_ch:
                await bot_ch.send(embed=summary_embed)

    @discord.ui.button(label="Format (Keep Old)", style=discord.ButtonStyle.success, emoji="✅")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        status_msg = await interaction.followup.send("⏳ **Formatting server layout...** Setting up categories and channels.", ephemeral=False)

        summary_embed = await execute_format_server(guild, interaction.user, clean_old=False)
        try:
            await status_msg.edit(content=None, embed=summary_embed)
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Server formatting cancelled.", embed=None, view=None)


async def purge_specific_channels_helper(
    guild: discord.Guild,
    channel_ids: List[int],
    invoking_channel_id: Optional[int] = None,
    progress_callback: Optional[Any] = None
) -> int:
    """Safely deletes specifically targeted channels in guild.
    Guarantees #form-automation is NEVER deleted.
    Deletes the invoking channel last (if targeted).
    Returns total count of deleted channels & categories.
    """
    to_delete_channels = []
    to_delete_categories = []

    target_set = set(channel_ids)
    for ch in guild.channels:
        if ch.id in target_set:
            if is_protected_channel(ch):
                continue
            if isinstance(ch, discord.CategoryChannel):
                to_delete_categories.append(ch)
            else:
                to_delete_channels.append(ch)

    invoking_channel = None
    if invoking_channel_id:
        for i, ch in enumerate(to_delete_channels):
            if ch.id == invoking_channel_id:
                invoking_channel = to_delete_channels.pop(i)
                break

    total_to_delete = len(to_delete_channels) + (1 if invoking_channel else 0) + len(to_delete_categories)
    deleted_count = 0

    # 1. Delete text and voice channels first
    for ch in to_delete_channels:
        try:
            if not is_protected_channel(ch):
                await ch.delete(reason="Channel cleanup: user selected channel deletion")
                deleted_count += 1
                if progress_callback:
                    await progress_callback(deleted_count, total_to_delete, getattr(ch, "name", "channel"))
                await asyncio.sleep(0.35)
        except Exception as e:
            print(f"⚠️ Could not delete channel #{getattr(ch, 'name', '')}: {e}", file=sys.stderr)

    # 2. Delete categories next
    for cat in to_delete_categories:
        try:
            if not is_protected_channel(cat):
                # Never delete category if it holds a protected channel
                if any(is_protected_channel(c) for c in cat.channels):
                    continue
                await cat.delete(reason="Channel cleanup: user selected category deletion")
                deleted_count += 1
                if progress_callback:
                    await progress_callback(deleted_count, total_to_delete, getattr(cat, "name", "category"))
                await asyncio.sleep(0.35)
        except Exception as e:
            print(f"⚠️ Could not delete category {getattr(cat, 'name', '')}: {e}", file=sys.stderr)

    # 3. Delete invoking channel last
    if invoking_channel:
        try:
            if not is_protected_channel(invoking_channel):
                await invoking_channel.delete(reason="Channel cleanup: user selected channel deletion (invoker)")
                deleted_count += 1
        except Exception as e:
            print(f"⚠️ Could not delete invoking channel #{getattr(invoking_channel, 'name', '')}: {e}", file=sys.stderr)

    return deleted_count


class ChannelDeleteInteractiveView(discord.ui.View):
    def __init__(self, author_id: int, guild: discord.Guild, page: int = 0):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild = guild
        self.page = page
        self.selected_ids: List[int] = []
        self._build_components()

    def _get_deletable_channels(self) -> List[discord.abc.GuildChannel]:
        channels = [ch for ch in self.guild.channels if not is_protected_channel(ch)]
        blueprint_channels = get_blueprint_channel_names()
        blueprint_cats = get_blueprint_category_names()

        def sort_key(c):
            is_cat = isinstance(c, discord.CategoryChannel)
            c_name = getattr(c, "name", "")
            is_bp = (c_name in blueprint_cats) if is_cat else (c_name in blueprint_channels)
            is_ticket = c_name.startswith(("ticket-", "order-"))
            if is_cat:
                return (2, getattr(c, "position", 0))
            if not is_bp and not is_ticket:
                return (0, getattr(c, "position", 0))
            if is_bp:
                return (1, getattr(c, "position", 0))
            return (3, getattr(c, "position", 0))

        channels.sort(key=sort_key)
        return channels

    def _build_components(self):
        self.clear_items()
        all_channels = self._get_deletable_channels()
        total_channels = len(all_channels)
        max_pages = max(1, (total_channels + 24) // 25)
        if self.page >= max_pages:
            self.page = max_pages - 1
        if self.page < 0:
            self.page = 0

        start_idx = self.page * 25
        page_channels = all_channels[start_idx : start_idx + 25]

        # Row 0: Multi-Select Menu
        if page_channels:
            options = []
            blueprint_channels = get_blueprint_channel_names()
            blueprint_cats = get_blueprint_category_names()

            for ch in page_channels:
                is_cat = isinstance(ch, discord.CategoryChannel)
                is_vc = isinstance(ch, discord.VoiceChannel)
                ch_name = getattr(ch, "name", "")

                if is_cat:
                    label = f"📁 {ch_name}"[:100]
                    desc = "Category"
                    emoji = "📁"
                elif is_vc:
                    label = f"🔊 {ch_name}"[:100]
                    desc = "Voice Channel"
                    emoji = "🔊"
                else:
                    label = f"#{ch_name}"[:100]
                    desc = "Text Channel"
                    emoji = "💬"

                is_bp = (ch_name in blueprint_cats) if is_cat else (ch_name in blueprint_channels)
                is_ticket = ch_name.startswith(("ticket-", "order-"))
                if is_ticket:
                    desc += " • Ticket Channel"
                elif is_bp:
                    desc += " • Formatted Blueprint"
                else:
                    desc += " • Leftover / Previous"

                is_default = ch.id in self.selected_ids
                options.append(discord.SelectOption(
                    label=label,
                    value=str(ch.id),
                    description=desc[:100],
                    emoji=emoji,
                    default=is_default
                ))

            select = discord.ui.Select(
                placeholder=f"Select channels to delete (Page {self.page+1}/{max_pages})...",
                min_values=1,
                max_values=max(1, min(len(options), 25)),
                options=options,
                row=0
            )
            select.callback = self._select_callback
            self.add_item(select)

        # Row 1: Action Buttons
        del_btn = discord.ui.Button(
            label=f"Delete Selected ({len(self.selected_ids)})" if self.selected_ids else "Delete Selected",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            disabled=(len(self.selected_ids) == 0),
            row=1
        )
        del_btn.callback = self._btn_delete_selected_callback
        self.add_item(del_btn)

        clean_old_btn = discord.ui.Button(
            label="Delete All Old Channels",
            style=discord.ButtonStyle.primary,
            emoji="🧹",
            row=1
        )
        clean_old_btn.callback = self._btn_clean_old_callback
        self.add_item(clean_old_btn)

        wipe_all_btn = discord.ui.Button(
            label="Wipe All Channels",
            style=discord.ButtonStyle.secondary,
            emoji="💥",
            row=1
        )
        wipe_all_btn.callback = self._btn_wipe_all_callback
        self.add_item(wipe_all_btn)

        # Row 2: Navigation & Cancel
        if max_pages > 1:
            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=(self.page == 0), row=2)
            prev_btn.callback = self._btn_prev_callback
            self.add_item(prev_btn)

            page_indicator = discord.ui.Button(label=f"Page {self.page+1}/{max_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=2)
            self.add_item(page_indicator)

            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= max_pages - 1), row=2)
            next_btn.callback = self._btn_next_callback
            self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌", row=2 if max_pages > 1 else 1)
        cancel_btn.callback = self._btn_cancel_callback
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("⛔ Only the command author can use this channel deletion panel.", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        all_channels = self._get_deletable_channels()
        old_channels = [c for c in all_channels if not is_preserved_channel(c, mode="clean_old")]
        total_channels = len(all_channels)
        max_pages = max(1, (total_channels + 24) // 25)

        embed = discord.Embed(
            title="🗑️ Channel Deletion & Management Suite",
            description=(
                "Use the multi-selection dropdown below to choose which channels to delete.\n\n"
                f"• 🧹 **Leftover/Old Channels Detected:** {len(old_channels)}\n"
                f"• 📋 **Total Deletable Channels:** {total_channels} (Page {self.page+1} of {max_pages})\n"
                "• 🛡️ **SAFEGUARD ACTIVE:** `#form-automation` is strictly protected and hidden from this list.\n\n"
                "**How to use:**\n"
                "1. Check the channels you wish to delete in the dropdown menu.\n"
                "2. Click **Delete Selected** to delete your choices.\n"
                "*(Or click **Delete All Old Channels** to instantly purge all non-blueprint leftovers in 1 click!)*"
            ),
            color=COLOR_WARN
        )

        if self.selected_ids:
            selected_names = []
            for sid in self.selected_ids:
                ch = self.guild.get_channel(sid)
                if ch:
                    selected_names.append(f"`#{ch.name}`" if not isinstance(ch, discord.CategoryChannel) else f"`📁 {ch.name}`")
            sample = ", ".join(selected_names[:8])
            if len(selected_names) > 8:
                sample += f" ...and {len(selected_names)-8} more"
            embed.add_field(name=f"Selected Channels ({len(self.selected_ids)})", value=sample, inline=False)

        embed.set_footer(text="AIO Bot Server Purge Suite • Multi-Select Enabled")
        return embed

    async def _select_callback(self, interaction: discord.Interaction):
        select_values = interaction.data.get("values", [])
        self.selected_ids = [int(v) for v in select_values]
        self._build_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _btn_delete_selected_callback(self, interaction: discord.Interaction):
        if not self.selected_ids:
            await interaction.response.send_message("❌ Please select at least one channel first!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        invoking_id = interaction.channel_id
        count = len(self.selected_ids)
        status_msg = await interaction.followup.send(f"⏳ **Deleting {count} selected channel(s)...** Please wait.", ephemeral=False)

        async def on_progress(done, total, name):
            try:
                await status_msg.edit(content=f"⏳ **Deleting channel(s)...** ({done}/{total}) `#{name}`")
            except Exception:
                pass

        deleted = await purge_specific_channels_helper(
            self.guild,
            self.selected_ids,
            invoking_channel_id=invoking_id,
            progress_callback=on_progress
        )

        embed = discord.Embed(
            title="🧹 Selected Channels Successfully Deleted",
            description=(
                f"Successfully deleted **{deleted}** channel(s) & categories.\n\n"
                f"• 🛡️ **Guaranteed Safeguard:** `#form-automation` is untouched.\n"
                f"• ✨ Your server channels are updated!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot Server Purge Suite")

        channel_still_exists = any(c.id == invoking_id for c in self.guild.channels)
        if channel_still_exists:
            try:
                await status_msg.edit(content=None, embed=embed)
            except Exception:
                pass
        else:
            bot_ch = discord.utils.get(self.guild.text_channels, name="🤖-bot-commands") or discord.utils.get(self.guild.text_channels, name="📢-announcements")
            if bot_ch:
                await bot_ch.send(embed=embed)

    async def _btn_clean_old_callback(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        invoking_id = interaction.channel_id
        status_msg = await interaction.followup.send("⏳ **Deleting all leftover/old channels...** Please wait.", ephemeral=False)

        async def on_progress(done, total, name):
            try:
                await status_msg.edit(content=f"⏳ **Deleting old channel(s)...** ({done}/{total}) `#{name}`")
            except Exception:
                pass

        deleted = await purge_channels_helper(
            self.guild,
            mode="clean_old",
            invoking_channel_id=invoking_id,
            progress_callback=on_progress
        )

        embed = discord.Embed(
            title="🧹 Leftover Channels Successfully Purged",
            description=(
                f"Purged **{deleted}** old channel(s) & categories.\n\n"
                f"• 🛡️ **Guaranteed Safeguard:** `#form-automation` and formatted channels are untouched.\n"
                f"• ✨ Your server is now clean and organized!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot Server Purge Suite")

        channel_still_exists = any(c.id == invoking_id for c in self.guild.channels)
        if channel_still_exists:
            try:
                await status_msg.edit(content=None, embed=embed)
            except Exception:
                pass
        else:
            bot_ch = discord.utils.get(self.guild.text_channels, name="🤖-bot-commands") or discord.utils.get(self.guild.text_channels, name="📢-announcements")
            if bot_ch:
                await bot_ch.send(embed=embed)

    async def _btn_wipe_all_callback(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        invoking_id = interaction.channel_id
        status_msg = await interaction.followup.send("⏳ **Wiping all server channels...** Please wait.", ephemeral=False)

        async def on_progress(done, total, name):
            try:
                await status_msg.edit(content=f"⏳ **Wiping channel(s)...** ({done}/{total}) `#{name}`")
            except Exception:
                pass

        deleted = await purge_channels_helper(
            self.guild,
            mode="wipe_all",
            invoking_channel_id=invoking_id,
            progress_callback=on_progress
        )

        embed = discord.Embed(
            title="💥 Server Wiped Clean",
            description=(
                f"Successfully wiped **{deleted}** channel(s) & categories.\n\n"
                f"• 🛡️ **Guaranteed Safeguard:** `#form-automation` remains 100% protected and safe.\n"
                f"• 🏗️ Run `/formatserver` anytime to deploy the official layout!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot Server Purge Suite")

        channel_still_exists = any(c.id == invoking_id for c in self.guild.channels)
        if channel_still_exists:
            try:
                await status_msg.edit(content=None, embed=embed)
            except Exception:
                pass
        else:
            bot_ch = discord.utils.get(self.guild.text_channels, name="form-automation")
            if bot_ch:
                try:
                    await bot_ch.send(embed=embed)
                except Exception:
                    pass

    async def _btn_prev_callback(self, interaction: discord.Interaction):
        self.page -= 1
        self._build_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _btn_next_callback(self, interaction: discord.Interaction):
        self.page += 1
        self._build_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _btn_cancel_callback(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Channel deletion cancelled.", embed=None, view=None)


class DeleteChannelsConfirmView(discord.ui.View):
    def __init__(self, author_id: int, mode: str = "clean_old", count: int = 0):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.mode = mode
        self.count = count

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("⛔ Only the command author can confirm this channel deletion.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Delete Channels", style=discord.ButtonStyle.danger, emoji="🧹")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        invoking_id = interaction.channel_id
        status_msg = await interaction.followup.send(f"⏳ **Deleting {self.count} channel(s)...** Please wait.", ephemeral=False)

        async def on_progress(done, total, name):
            try:
                await status_msg.edit(content=f"⏳ **Deleting channel(s)...** ({done}/{total}) `#{name}`")
            except Exception:
                pass

        deleted = await purge_channels_helper(
            guild,
            mode=self.mode,
            invoking_channel_id=invoking_id,
            progress_callback=on_progress
        )

        embed = discord.Embed(
            title="🧹 Channels Successfully Deleted",
            description=(
                f"Cleaned up **{deleted}** previous channel(s) & categories.\n\n"
                f"• 🛡️ **Guaranteed Safeguard:** `#form-automation` is untouched.\n"
                f"• ✨ Your server channels are now clean and organized!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot Server Purge Suite")

        channel_still_exists = any(c.id == invoking_id for c in guild.channels)
        if channel_still_exists:
            try:
                await status_msg.edit(content=None, embed=embed)
            except Exception:
                pass
        else:
            bot_ch = discord.utils.get(guild.text_channels, name="🤖-bot-commands") or discord.utils.get(guild.text_channels, name="📢-announcements")
            if bot_ch:
                await bot_ch.send(embed=embed)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Channel deletion cancelled.", embed=None, view=None)

def build_serverinfo_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title=f"📊 {guild.name}", color=COLOR_INFO)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    owner = guild.owner or f"<@{guild.owner_id}>"
    embed.description = f"👑 **Owner:** {owner} • 🆔 `{guild.id}`\n🗓️ **Created:** {guild.created_at.strftime('%b %d, %Y')}"
    embed.add_field(name="👥 Members", value=f"**{guild.member_count:,}**", inline=True)
    embed.add_field(name="💬 Channels", value=f"**{len(guild.text_channels)}** text • **{len(guild.voice_channels)}** voice", inline=True)
    embed.add_field(name="🚀 Boosts", value=f"Tier **{guild.premium_tier}** ({guild.premium_subscription_count} boosts)", inline=True)
    embed.set_footer(text=f"AIO Bot Server Diagnostics • {len(guild.roles)} Roles")
    return embed

def build_userinfo_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title=f"👤 {member.display_name}", color=member.color if member.color.value != 0 else COLOR_PRIMARY)
    embed.set_thumbnail(url=member.display_avatar.url)
    badge = " 🤖 *(Bot)*" if member.bot else ""
    embed.description = f"**{member}**{badge} • 🆔 `{member.id}`"
    embed.add_field(name="🗓️ Created", value=member.created_at.strftime("%b %d, %Y"), inline=True)
    embed.add_field(name="📥 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    role_str = " ".join(roles[:12]) if roles else "None"
    if len(roles) > 12:
        role_str += f" *(+{len(roles)-12} more)*"
    embed.add_field(name=f"🏷️ Roles ({len(roles)})", value=role_str, inline=False)
    return embed

# 5. CORE EVENTS & COMMANDS

@bot.event
async def on_ready():
    print(f'🤖 AIO Bot is officially online! Logged in as {bot.user} (ID: {bot.user.id if bot.user else "?"})', flush=True)

    # Register persistent interactive views so buttons work across restarts
    try:
        bot.add_view(TicketLaunchView())
        bot.add_view(TicketControlView())
        bot.add_view(FoodAccountPurchaseView())
        bot.add_view(CouponHubLaunchView())
        bot.add_view(CouponRoomControlView())
        bot.add_view(StaffModPanelButtonView())
        print('✅ Persistent interactive views registered successfully.', flush=True)
    except Exception as e:
        print(f"ℹ️ Note on persistent views registration: {e}", file=sys.stderr, flush=True)

    # Global slash command tree sync
    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} global application slash command(s).', flush=True)
    except Exception as e:
        print(f'⚠️ Slash command sync notice: {e}', file=sys.stderr, flush=True)

    # Automatically unhoist bot roles so the bot never displays above the server owner/founder
    for g in bot.guilds:
        try:
            if g.me and g.me.guild_permissions.manage_roles:
                for r in g.me.roles:
                    if not r.is_default() and getattr(r, "hoist", False):
                        try:
                            await r.edit(hoist=False, reason="Auto-unhoisting bot role to ensure server owner stays on top")
                            print(f"🔽 Auto-unhoisted bot role '{r.name}' in {g.name}", flush=True)
                        except Exception:
                            pass
        except Exception:
            pass

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    # Auto-Mod Word Filter Inspection
    filter_words = get_filter_words(message.guild.id)
    if filter_words and not (message.author.guild_permissions.manage_messages or message.author.guild_permissions.administrator):
        content_lower = message.content.lower()
        for bad_word in filter_words:
            pattern = rf"\b{re.escape(bad_word)}\b"
            if re.search(pattern, content_lower):
                try:
                    await message.delete()
                    case_id = log_mod_case(
                        guild_id=message.guild.id,
                        action="AutoMod Delete",
                        target=str(message.author),
                        moderator="AIO AutoMod 🛡️",
                        reason=f"Used filtered word: '{bad_word}'",
                        details=f"Message: {message.content[:100]}"
                    )
                    warn_record = {
                        "reason": f"AutoMod: Blacklisted word '{bad_word}'",
                        "moderator": "AIO AutoMod 🛡️",
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    }
                    key = f"{message.guild.id}_{message.author.id}"
                    warnings_db.setdefault(key, []).append(warn_record)
                    save_warnings(warnings_db)

                    embed = discord.Embed(
                        title="🛡️ Auto-Mod Filter Triggered",
                        description=f"{message.author.mention}, your message contained a blacklisted word and was removed.",
                        color=COLOR_ERROR
                    )
                    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
                    embed.add_field(name="Warning Count", value=str(len(warnings_db[key])), inline=True)
                    await message.channel.send(embed=embed, delete_after=8)
                    return
                except Exception as e:
                    print(f"⚠️ AutoMod error: {e}", file=sys.stderr)

    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    # Ghost Ping Detection: message deleted containing member mentions
    actual_mentions = [m for m in message.mentions if not m.bot and m.id != message.author.id]
    if actual_mentions:
        embed = discord.Embed(
            title="👻 Ghost Ping Detected!",
            description=f"A message with mentions was deleted.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="👤 Author", value=message.author.mention, inline=True)
        embed.add_field(name="🎯 Pinged Users", value=" ".join(m.mention for m in actual_mentions), inline=True)
        embed.add_field(name="💬 Message Content", value=message.content[:500] if message.content else "*[No text content]*", inline=False)
        embed.set_footer(text="AIO Bot Anti-GhostPing Shield")
        try:
            await message.channel.send(embed=embed)
        except Exception:
            pass




# --- INTERACTIVE PANELS ---

@bot.hybrid_command(name="panel", description="Open the interactive Shopping Cart & Coupon Optimizer panel")
async def open_panel(ctx):
    await safely_delete_message(ctx)
    embed = build_cart_embed(ctx.author.id)
    await ctx.send(embed=embed, view=QuickCartActionView(ctx.author.id))


@bot.hybrid_command(name="modpanel", description="Open the interactive server moderation control panel")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def open_modpanel(ctx):
    await safely_delete_message(ctx)
    embed = build_staff_modpanel_embed()
    view = StaffModPanelButtonView()
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="help", description="Show the AIO Bot interactive help menu")
async def help_command(ctx):
    await safely_delete_message(ctx)
    author_perms = ctx.channel.permissions_for(ctx.author) if ctx.guild else discord.Permissions.none()
    is_owner = await bot.is_owner(ctx.author)
    embed = discord.Embed(
        title="📖 AIO Bot — Command Center",
        description="Select a category from the dropdown menu below to view detailed command guides.",
        color=COLOR_PRIMARY
    )
    embed.add_field(name="🛍️ Coupon Optimizer", value="Find optimal checkout bundles & max savings.", inline=True)
    embed.add_field(name="🛡️ Moderation Suite", value="Anti-raid, auto-mod filters, cases & staff discipline.", inline=True)
    embed.add_field(name="🎮 Games & Economy", value="Blackjack, Slots, Connect 4, Trivia & coin bank.", inline=True)
    embed.set_footer(text="Tip: All commands work with either / or ! (e.g. /panel or !panel)")
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
    if is_protected_channel(target):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` is strictly protected and CANNOT be nuked or deleted!", delete_after=8)
        return
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

@bot.hybrid_command(name="purge", aliases=["clean", "clear_messages", "prune"], description="Bulk delete messages (optional member or channel filter)")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def purge_messages(ctx, amount: int, member: Optional[discord.Member] = None, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target_ch = channel or ctx.channel
    if is_protected_channel(target_ch):
        await ctx.send(f"⛔ {target_ch.mention} is a protected core channel and cannot be purged.", delete_after=6)
        return
    if amount <= 0:
        await ctx.send("❌ Provide a number greater than 0.", delete_after=6)
        return
    limit = min(amount, 100)

    try:
        if member:
            def check(m):
                return m.author.id == member.id
            deleted = await target_ch.purge(limit=limit, check=check)
            await ctx.send(f"🧹 Purged **{len(deleted)}** message(s) from **{member.display_name}** in {target_ch.mention}.", delete_after=6)
        else:
            deleted = await target_ch.purge(limit=limit)
            await ctx.send(f"🧹 Purged **{len(deleted)}** message(s) in {target_ch.mention}.", delete_after=6)
    except discord.Forbidden:
        await ctx.send(f"❌ Bot lacks permissions to purge messages in {target_ch.mention}.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error purging messages in {target_ch.mention}: {e}", delete_after=6)

@bot.hybrid_command(name="dm", aliases=["directmessage", "senddm", "msg"], description="Staff command: Send a direct message (DM) to a member from the bot")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def dm_command(ctx: commands.Context, user: str, *, message: str, anonymous: Optional[bool] = False):
    await safely_delete_message(ctx)
    guild = ctx.guild
    target = await resolve_user_or_member(guild, user.strip())
    if not target:
        await ctx.send(f"❌ Could not find user `{user}`. Please provide a valid mention (@user), username, or User ID.", delete_after=8)
        return

    msg_text = message.strip()
    embed = discord.Embed(
        title=f"📬 Direct Message from {guild.name if guild else 'Server Staff'}",
        description=msg_text,
        color=COLOR_PRIMARY
    )
    if not anonymous:
        embed.set_author(
            name=f"Sent by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url if hasattr(ctx.author, 'display_avatar') else None
        )
    else:
        embed.set_author(
            name=f"Official Server Communication • {guild.name if guild else 'AIO Bot'}",
            icon_url=guild.icon.url if guild and guild.icon else None
        )
    embed.set_footer(text="AIO Bot Direct Messaging • Reply in server tickets if you need assistance")

    try:
        await target.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(f"❌ Could not deliver DM to **{target}** (`{target.id}`). Their direct messages are disabled for server members or they have blocked the bot.", delete_after=10)
        return
    except Exception as e:
        await ctx.send(f"❌ Failed to send DM to **{target}**: {e}", delete_after=8)
        return

    case_id = log_mod_case(
        guild_id=guild.id if guild else None,
        action="Direct Message",
        target=str(target),
        moderator=str(ctx.author),
        reason=msg_text[:100]
    )

    confirm_embed = discord.Embed(
        title="✅ Direct Message Sent",
        description=f"Successfully delivered direct message to {target.mention} (`{target.id}`).",
        color=COLOR_SUCCESS
    )
    confirm_embed.add_field(name="Recipient", value=f"**{target}** (`{target.id}`)", inline=True)
    confirm_embed.add_field(name="Sent By", value=ctx.author.mention if not anonymous else "*Anonymous Staff*", inline=True)
    confirm_embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    confirm_embed.add_field(name="Message", value=f">>> {msg_text[:1000]}", inline=False)
    await ctx.send(embed=confirm_embed, delete_after=12)

@bot.hybrid_command(name="kick", description="Kick a member from the server")
@commands.guild_only()
@commands.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
async def kick_member(ctx, member: discord.Member, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot kick a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.kick(reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Kick", str(member), str(ctx.author), reason or "No reason provided")
        embed = discord.Embed(title="👢 Member Kicked", color=COLOR_WARN)
        embed.add_field(name="Member", value=f"**{member}** (`{member.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to kick this user.", delete_after=6)

@bot.hybrid_command(name="ban", description="Ban a member from the server")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def ban_member(ctx, member: discord.Member, delete_message_days: Optional[int] = 0, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot ban a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.ban(delete_message_days=min(delete_message_days or 0, 7), reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Ban", str(member), str(ctx.author), reason or "No reason provided", f"Purged {delete_message_days}d messages")
        embed = discord.Embed(title="🔨 Member Banned", color=COLOR_ERROR)
        embed.add_field(name="Member", value=f"**{member}** (`{member.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await ctx.send(embed=embed)
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
    case_id = log_mod_case(ctx.guild.id, "Unban", str(target_user), str(ctx.author), "Unbanned user")
    embed = discord.Embed(title="🕊️ User Unbanned", color=COLOR_SUCCESS)
    embed.add_field(name="User", value=f"**{target_user}** (`{target_user.id}`)", inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="timeout", aliases=["mute"], description="Timeout/mute a member for a set duration (e.g. 5m, 1h, 1d)")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def timeout_member(ctx, member: discord.Member, duration: str, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
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
        case_id = log_mod_case(ctx.guild.id, "Timeout", str(member), str(ctx.author), reason or "No reason provided", f"Duration: {duration}")
        embed = discord.Embed(title="🔇 Member Timed Out", color=COLOR_WARN)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Duration", value=f"**{duration}**", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to timeout this user.", delete_after=6)

@bot.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove timeout from a member")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def untimeout_member(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot untimeout a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        case_id = log_mod_case(ctx.guild.id, "Untimeout", str(member), str(ctx.author), "Timeout removed")
        embed = discord.Embed(title="🔊 Timeout Removed", color=COLOR_SUCCESS)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to untimeout this user.", delete_after=6)

@bot.hybrid_command(name="warn", description="Issue an official warning to a member")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def warn_member(ctx, member: discord.Member, *, reason: str):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
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
    case_id = log_mod_case(ctx.guild.id, "Warning", str(member), str(ctx.author), reason, f"Active warning count: {count}")
    embed = discord.Embed(title="⚠️ Official Warning Issued", color=COLOR_WARN)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Warning Count", value=f"**#{count}**", inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    await ctx.send(embed=embed)

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

    tx_count = len(coupons) if coupons else 1
    embed = discord.Embed(
        title="🧾 Cashier Step-by-Step Checkout Strategy",
        description=f"Split your items into **{tx_count} transaction(s)** at the register for maximum savings.",
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
                item_bullets = " • ".join([f"**{i['name']}** (${i['price']:.2f})" for i in group_items])
                group_sub = sum(i['price'] for i in group_items)
                due = group_due(group_items, coupon_val)
                saved_amt = group_sub - due
                embed.add_field(
                    name=f"🛒 Step {idx+1}: Ring Up {len(group_items)} Item(s)",
                    value=(
                        f"{item_bullets}\n"
                        f"Scan: `{coupon_label(coupon_val)}` • Subtotal: ${group_sub:.2f} ➔ **Cashier Due: ${due:.2f}** *(Saved ${saved_amt:.2f}!)*"
                    ),
                    inline=False
                )

    # Cart Summary
    embed.add_field(
        name="📊 Checkout Summary",
        value=(
            f"🏷️ Retail: **${full_subtotal:.2f}** • 🎟️ Discounts: **-${total_savings:.2f}** ({savings_pct:.0f}% OFF)\n"
            f"💵 **Final Register Out-of-Pocket: ${total_due:.2f}**"
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
                f"• Buy `{coupon_label(best_candidate)}` (${coupon_cost(best_candidate):.2f}) "
                f"➔ Drops register to **${trial_due:.2f}** *(+${best_net_benefit:.2f} profit!)*"
            )
            working_coupons.append(best_candidate)
            working_due = trial_due

        if suggestions:
            embed.add_field(
                name="💡 Extra Savings Opportunities",
                value="Save even more by purchasing these coupons:\n" + "\n".join(suggestions),
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
    added_str = ", ".join(f"**{i['name']}** (${i['price']:.2f})" for i in parsed)
    embed = build_cart_embed(ctx.author.id, notice=f"✅ **Added {len(parsed)} item(s):** {added_str}")
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
    embed = build_cart_embed(ctx.author.id, notice=f"🎟️ **Loaded {len(parsed)} coupon(s):** {added_str}")
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

    thinking_embed = discord.Embed(
        title="🧠 Calculating Optimal Coupon Strategy...",
        description=f"🔬 *Analyzing **{len(items)} items** (${sum(i['price'] for i in items):.2f}) and **{len(coupons)} coupons**...*\n\n`[████████░░] Finding lowest out-of-pocket splits...`",
        color=COLOR_INFO
    )
    msg = await ctx.send(embed=thinking_embed)
    await asyncio.sleep(1.4)

    embed = build_strategy_embed(items, coupons)
    view = QuickCartActionView(ctx.author.id)
    await msg.edit(embed=embed, view=view)

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

    thinking_embed = discord.Embed(
        title="🧮 Calculating Instant Strategy...",
        description=f"🔍 *Parsing & optimizing **{len(items)} items** with **{len(coupons)} coupons**...*",
        color=COLOR_INFO
    )
    msg = await ctx.send(embed=thinking_embed)
    await asyncio.sleep(1.3)

    embed = build_strategy_embed(items, coupons)
    await msg.edit(embed=embed)

@bot.hybrid_command(name="cart", description="View your current shopping cart")
async def view_cart(ctx):
    await safely_delete_message(ctx)
    embed = build_cart_embed(ctx.author.id)
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
        item_str   = "\n".join(f"• **{i['name']}**: ${i['price']:.2f}" for i in items[:15]) or "No items."
        if len(items) > 15:
            item_str += f"\n*...and {len(items)-15} more items*"
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None"
        dm = discord.Embed(title="🧾 Your AIO Trip Receipt", color=COLOR_SUCCESS)
        dm.description = (
            f"🗓️ **{now.strftime('%A, %b %d, %Y @ %I:%M %p')}**\n"
            f"💰 **Net Money Saved:** **${net_saved:.2f}**"
        )
        dm.add_field(name="🏷️ Full Retail",   value=f"${subtotal:.2f}",     inline=True)
        dm.add_field(name="💵 Register Paid", value=f"${total_due:.2f}",     inline=True)
        dm.add_field(name="🎟️ Coupon Cost",   value=f"${coupon_spend:.2f}",  inline=True)
        dm.add_field(name="🛒 Items Purchased", value=item_str, inline=False)
        if coupons:
            dm.add_field(name="🎟️ Coupons Used", value=coupon_str, inline=False)
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
@commands.is_owner()
@app_commands.default_permissions(administrator=True)
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
    full_price = sum(i['price'] for i in test_items)

    # Step 1: Initial Launch & Synthesis
    embed = discord.Embed(
        title="⚡ Optimizer Stress Test & Performance Benchmark",
        description="🚀 **Initializing combinatorial stress test engine...**",
        color=COLOR_INFO
    )
    embed.add_field(
        name="Phase 1 • Cart Synthesis",
        value=f"🧪 Synthesizing **{n_items} items** (${full_price:.2f}) & **{len(test_coupons)} coupons**\n`[██░░░░░░░░] 20%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot High-Performance Benchmark • Phase 1/4")
    msg = await ctx.send(embed=embed)

    await asyncio.sleep(1.4)

    # Step 2: Pruning Tree & Combinatorial Permutations
    embed.description = "⚙️ **Constructing branch-and-bound pruning tree & bounding constraints...**"
    embed.set_field_at(
        0,
        name="Phase 2 • Exploration Graph",
        value=f"🌲 Mapping tree with **{2**min(n_items, 14):,} permutations** across {len(test_coupons)} coupon groups\n`[█████░░░░░] 50%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot High-Performance Benchmark • Phase 2/4")
    await msg.edit(embed=embed)

    await asyncio.sleep(1.4)

    # Step 3: Real benchmark computation
    start_time = time.perf_counter()
    total_due, bundling = calculate_best_bundles(test_items, test_coupons)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    saved = full_price - total_due
    pct = (saved / full_price * 100) if full_price > 0 else 0

    embed.description = "🔬 **Evaluating multi-pass backtracking algorithms & cache efficiency...**"
    embed.set_field_at(
        0,
        name="Phase 3 • Combinatorial Benchmark Execution",
        value=f"⚡ **Latency Measured:** `{elapsed_ms:.2f} ms`\n🎟️ **Active Bundles:** {len(bundling)} optimal registers created\n`[████████░░] 80%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot High-Performance Benchmark • Phase 3/4")
    await msg.edit(embed=embed)

    await asyncio.sleep(1.4)

    # Step 4: Final Comprehensive Telemetry Report
    final_embed = discord.Embed(
        title="⚡ Optimizer Stress Test & Performance Benchmark",
        description=f"✅ **Benchmark Complete!** Evaluated **{n_items} items** (${full_price:.2f}) and **{len(test_coupons)} coupons**.\n`[██████████] 100% (Completed)`",
        color=COLOR_SUCCESS if elapsed_ms < 50 else COLOR_WARN
    )
    final_embed.add_field(name="⏱️ Computation Latency", value=f"**{elapsed_ms:.2f} ms**", inline=True)
    final_embed.add_field(name="📦 Items Processed", value=f"**{n_items}** items (${full_price:.2f})", inline=True)
    final_embed.add_field(name="🎟️ Coupons Bundled", value=", ".join(coupon_label(c) for c in test_coupons), inline=True)
    final_embed.add_field(name="💵 Register Total Due", value=f"**${total_due:.2f}**", inline=True)
    final_embed.add_field(name="💰 Dollars Saved", value=f"**${saved:.2f}** ({pct:.0f}% off)", inline=True)
    status_label = "🚀 **Sub-10ms Branch-and-Bound (Ultra Fast)**" if elapsed_ms < 10 else "✅ **Healthy Performance (<100ms)**"
    final_embed.add_field(name="⚡ Engine Health", value=status_label, inline=False)
    final_embed.set_footer(text="AIO Bot High-Performance Combinatorial Engine • Benchmark Finished")
    await msg.edit(embed=final_embed)

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
        pct_saved = ((s['total_net_saved'] / s['total_full_price']) * 100) if s.get('total_full_price', 0) > 0 else 0
        embed.description = (
            f"## 💵 Total Saved: ${s['total_net_saved']:.2f}\n"
            f"Across **{trip_count} trip(s)** • Average **${avg_saved:.2f} saved/trip** ({pct_saved:.0f}% savings rate)"
        )
        embed.add_field(name="🏷️ Retail Value", value=f"${s['total_full_price']:.2f}", inline=True)
        embed.add_field(name="💵 Total Paid", value=f"${s['total_paid']:.2f}", inline=True)
        embed.add_field(name="🎟️ Coupon Cost", value=f"${s['total_coupon_cost']:.2f}", inline=True)
        embed.set_footer(text="AIO Bot Savings Analytics • Use /history to view individual trips")
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
    desc_lines = []
    for trip in matches[-8:]:
        item_names = ", ".join(i["name"] for i in trip.get("items", [])) or "Items"
        if len(item_names) > 42:
            item_names = item_names[:39] + "..."
        desc_lines.append(
            f"🗓️ **{trip.get('date', '—')}** ({trip.get('time', '—')}) — Paid **${trip.get('total_due', 0.0):.2f}** *(Saved ${trip.get('net_saved', 0.0):.2f})*\n"
            f"└ 🛒 *{item_names}*"
        )
    embed.description = "\n\n".join(desc_lines)
    embed.set_footer(text=f"Showing last {len(matches[-8:])} trip(s) • Total logged: {len(trips)}")
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
@commands.is_owner()
@app_commands.default_permissions(administrator=True)
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



# --- ADVANCED MODERATION & SECURITY ---

@bot.hybrid_command(name="lockdown", description="Emergency server lockdown: toggle message permissions across all text channels")
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def server_lockdown(ctx, action: Literal["on", "off"], *, reason: Optional[str] = "Emergency Server Lockdown"):
    await safely_delete_message(ctx)
    guild = ctx.guild
    lock = (action.lower() == "on")
    changed_count = 0

    progress_msg = await ctx.send(f"⏳ Executing Server Lockdown (**{action.upper()}**)...")

    for ch in guild.text_channels:
        perms = ch.overwrites_for(guild.default_role)
        if lock:
            if perms.send_messages is not False:
                perms.send_messages = False
                try:
                    await ch.set_permissions(guild.default_role, overwrite=perms, reason=f"Lockdown ON by {ctx.author}: {reason}")
                    changed_count += 1
                except Exception:
                    pass
        else:
            if perms.send_messages is False:
                perms.send_messages = None
                try:
                    await ch.set_permissions(guild.default_role, overwrite=perms, reason=f"Lockdown OFF by {ctx.author}: {reason}")
                    changed_count += 1
                except Exception:
                    pass

    case_id = log_mod_case(
        guild_id=guild.id,
        action=f"Lockdown {action.upper()}",
        target=f"{changed_count} channels",
        moderator=str(ctx.author),
        reason=reason or "Emergency Lockdown Protocol"
    )

    embed = discord.Embed(
        title=f"🚨 Server Lockdown {'ACTIVATED' if lock else 'DEACTIVATED'}",
        description=f"Server-wide message permissions have been **{'LOCKED' if lock else 'UNLOCKED'}**.",
        color=COLOR_ERROR if lock else COLOR_SUCCESS
    )
    embed.add_field(name="Channels Updated", value=f"**{changed_count}** text channels", inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    await progress_msg.edit(content=None, embed=embed)


@bot.hybrid_command(name="filter", description="Manage the server Auto-Mod blacklisted words")
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
async def filter_cmd(ctx, action: Literal["add", "remove", "list", "clear"], *, word: Optional[str] = None):
    await safely_delete_message(ctx)
    gid = ctx.guild.id
    action = action.lower()

    if action == "list":
        words = get_filter_words(gid)
        if not words:
            await ctx.send("📋 Auto-Mod filter is currently empty. Add words with `/filter add [word]`.", delete_after=8)
            return
        embed = discord.Embed(title=f"🛡️ Auto-Mod Filtered Words ({len(words)})", color=COLOR_INFO)
        embed.description = ", ".join(f"`{w}`" for w in words)
        embed.set_footer(text="Messages matching these words will be deleted with an automated warning.")
        await ctx.send(embed=embed)

    elif action == "add":
        if not word:
            await ctx.send("❌ Please provide a word or phrase to blacklist: `/filter add [word]`", delete_after=6)
            return
        success = add_filter_word(gid, word)
        if success:
            await ctx.send(f"✅ Added `{word.strip().lower()}` to the Auto-Mod blacklist.", delete_after=8)
        else:
            await ctx.send(f"⚠️ `{word.strip().lower()}` is already in the blacklist.", delete_after=6)

    elif action == "remove":
        if not word:
            await ctx.send("❌ Please provide a word to remove: `/filter remove [word]`", delete_after=6)
            return
        success = remove_filter_word(gid, word)
        if success:
            await ctx.send(f"✅ Removed `{word.strip().lower()}` from the Auto-Mod blacklist.", delete_after=8)
        else:
            await ctx.send(f"⚠️ `{word.strip().lower()}` was not found in the blacklist.", delete_after=6)

    elif action == "clear":
        filters_db[str(gid)] = []
        save_filters(filters_db)
        await ctx.send("🧹 Cleared all filtered words for this server.", delete_after=8)


@bot.hybrid_command(name="modlogs", aliases=["modlog", "historylogs"], description="View moderation case history for a user")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def modlogs_cmd(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    cases = [c for c in mod_cases_db.get("cases", []) if c.get("guild_id") == str(ctx.guild.id) and str(member.id) in str(c.get("target")) or str(member) == str(c.get("target"))]

    if not cases:
        await ctx.send(f"✨ No moderation case records found for **{member.display_name}**.", delete_after=8)
        return

    embed = discord.Embed(title=f"📜 Modlogs for {member.display_name} ({len(cases)} Cases)", color=COLOR_INFO)
    embed.set_thumbnail(url=member.display_avatar.url)
    for c in cases[-8:]:
        embed.add_field(
            name=f"`#CASE-{c['case_id']:04d}` • {c['action']} ({c['timestamp']})",
            value=f"**Reason:** {c['reason']}\n**Mod:** {c['moderator']}",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.hybrid_command(name="case", description="View specific details for a moderation case ID")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def case_cmd(ctx, case_id: int):
    await safely_delete_message(ctx)
    cases = mod_cases_db.get("cases", [])
    found = next((c for c in cases if c.get("case_id") == case_id and c.get("guild_id") == str(ctx.guild.id)), None)

    if not found:
        await ctx.send(f"❌ Case `#CASE-{case_id:04d}` not found in this server.", delete_after=6)
        return

    embed = discord.Embed(title=f"📁 Case File `#CASE-{found['case_id']:04d}`", color=COLOR_PRIMARY)
    embed.add_field(name="Action", value=f"**{found['action']}**", inline=True)
    embed.add_field(name="Target User", value=str(found['target']), inline=True)
    embed.add_field(name="Moderator", value=str(found['moderator']), inline=True)
    embed.add_field(name="Reason", value=f"`{found['reason']}`", inline=False)
    if found.get("details") and found["details"] != "None":
        embed.add_field(name="Details", value=found["details"], inline=False)
    embed.add_field(name="Timestamp", value=found['timestamp'], inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="note", description="Manage internal staff notes on members")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def note_cmd(ctx, action: Literal["add", "view", "clear"], member: discord.Member, *, note: Optional[str] = None):
    await safely_delete_message(ctx)
    gid = ctx.guild.id
    uid = member.id
    action = action.lower()

    if action == "add":
        if not note:
            await ctx.send("❌ Please provide note text: `/note add [@member] [note]`", delete_after=6)
            return
        add_mod_note(gid, uid, str(ctx.author), note)
        await ctx.send(f"📝 Added staff note to **{member.display_name}**.", delete_after=8)

    elif action == "view":
        notes = get_mod_notes(gid, uid)
        if not notes:
            await ctx.send(f"📋 No staff notes on record for **{member.display_name}**.", delete_after=8)
            return
        embed = discord.Embed(title=f"📝 Staff Notes — {member.display_name} ({len(notes)})", color=COLOR_WARN)
        embed.set_thumbnail(url=member.display_avatar.url)
        for idx, n in enumerate(notes, 1):
            embed.add_field(
                name=f"Note #{idx} • {n['timestamp']}",
                value=f"**Text:** {n['note']}\n**By:** {n['moderator']}",
                inline=False
            )
        await ctx.send(embed=embed)

    elif action == "clear":
        count = clear_mod_notes(gid, uid)
        await ctx.send(f"🧹 Cleared **{count}** staff note(s) for **{member.display_name}**.", delete_after=8)



# --- INTERACTIVE MINI-GAMES ---

@bot.hybrid_command(name="blackjack", aliases=["bj", "21"], description="Play an interactive game of 21 against the Dealer")
async def blackjack_cmd(ctx, bet: Optional[int] = 50):
    await safely_delete_message(ctx)
    stake = max(0, int(bet or 0))
    if stake > 0:
        if not deduct_user_coins(ctx.author.id, stake):
            cur_bal = get_user_coins(ctx.author.id)
            await ctx.send(
                f"❌ You don't have enough coins ({stake:,} 🪙) to play! Current Balance: **{cur_bal:,} 🪙**. Run `/daily` or `/balance`.",
                delete_after=8
            )
            return

    view = BlackjackGameView(player=ctx.author, bet=stake)
    p_val = calculate_hand_value(view.player_hand)
    if p_val == 21:
        view.finish_game(outcome_type="natural")
        profit = int(stake * 1.5)
        embed = view.build_embed(hide_dealer=False, outcome=f"🌟 **NATURAL BLACKJACK!** Instant Win! (+{profit:,} 🪙 profit)")
        await ctx.send(embed=embed, view=view)
    else:
        embed = view.build_embed(hide_dealer=True)
        await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="connect4", aliases=["c4"], description="Play Connect 4 against a friend or the AIO Bot AI")
async def connect4_cmd(ctx, opponent: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if opponent and opponent.id == ctx.author.id:
        await ctx.send("❌ You cannot play Connect 4 against yourself!", delete_after=6)
        return
    view = Connect4View(p1=ctx.author, p2=opponent)
    embed = view.build_embed()
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="trivia", aliases=["quiz"], description="Test your knowledge in a 4-choice timed trivia challenge")
async def trivia_cmd(ctx, category: Optional[Literal["general", "tech", "gaming", "science"]] = "general"):
    await safely_delete_message(ctx)
    cat = (category or "general").lower()
    pool = TRIVIA_QUESTIONS.get(cat, TRIVIA_QUESTIONS["general"])
    question_data = secrets.choice(pool)

    view = TriviaView(user=ctx.author, question_data=question_data)
    embed = discord.Embed(
        title=f"🧠 Trivia Challenge — {cat.capitalize()} Knowledge",
        description=f"**{question_data['q']}**\n\nSelect the correct option below (45s timer):",
        color=COLOR_PRIMARY
    )
    for idx, opt in enumerate(question_data["options"]):
        embed.add_field(name=f"Option {chr(65+idx)}", value=opt, inline=True)
    embed.set_footer(text="Click a button below to submit your answer! Correct answers earn +50 🪙!")
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="slots",
    aliases=["slot", "spin"],
    description="Spin the 3x3 high-roller slot machine (supports single spin or multi-round auto-spins)"
)
async def slots_cmd(ctx, bet: Optional[int] = 10, rounds: Optional[int] = 1):
    await safely_delete_message(ctx)
    stake = max(1, int(bet or 10))
    total_rounds = min(max(1, int(rounds or 1)), 25)

    if not deduct_user_coins(ctx.author.id, stake):
        cur_bal = get_user_coins(ctx.author.id)
        await ctx.send(
            f"❌ You don't have enough coins ({stake:,} 🪙) to spin! Current Balance: **{cur_bal:,} 🪙**. Run `/daily` or `/balance`.",
            delete_after=8
        )
        return

    # Track multi-round / session stats
    total_spent = 0
    total_won = 0
    wins_count = 0
    best_multiplier = 0.0
    best_payout_title = "None"
    history_lines = []
    msg = None

    for round_num in range(1, total_rounds + 1):
        if round_num > 1:
            if not deduct_user_coins(ctx.author.id, stake):
                history_lines.append(f"`#{round_num}` 🛑 *Auto-spins stopped (insufficient coins)*")
                break

        total_spent += stake
        cur_bal = get_user_coins(ctx.author.id)
        net = total_won - total_spent
        net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
        win_rate_str = f"{wins_count}/{max(1, round_num-1)} ({wins_count/max(1, round_num-1)*100:.0f}%)" if round_num > 1 else "0/0 (0%)"

        grid = roll_3x3_slots()
        title_prefix = f"🎰 AIO 3x3 High-Roller Slots — Round {round_num}/{total_rounds}" if total_rounds > 1 else "🎰 AIO 3x3 High-Roller Slots"

        def build_reel_frame(grid_text: str, subtext: str, color_code: int = COLOR_PRIMARY):
            emb = discord.Embed(
                title=title_prefix,
                description=f"{grid_text}\n\n{subtext}",
                color=color_code
            )
            if history_lines and total_rounds > 1:
                emb.description += "\n\n**Recent Spins:**\n" + "\n".join(history_lines[-4:])
            emb.add_field(name="💰 Total Bet", value=f"**{total_spent:,} 🪙**", inline=True)
            emb.add_field(name="🏆 Total Won", value=f"**{total_won:,} 🪙**", inline=True)
            emb.add_field(name="📈 Net Outcome", value=f"**{net_str} 🪙**", inline=True)
            if total_rounds > 1:
                emb.add_field(name="🎯 Win Rate", value=f"**{win_rate_str}**", inline=True)
            emb.add_field(name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
            emb.set_footer(text=f"Round {round_num}/{total_rounds} • 5 Paylines • Player: {ctx.author.display_name}")
            return emb

        try:
            # Step 1: All 3 columns spinning
            frame1 = build_reel_frame(format_3x3_grid(grid, 0), "*Spinning 3x3 high-roller reels...*")
            if msg is None:
                msg = await ctx.send(embed=frame1)
            else:
                await msg.edit(embed=frame1)
            await asyncio.sleep(0.9)

            # Step 2: Column 1 stops
            frame2 = build_reel_frame(format_3x3_grid(grid, 1), "*Column 1 locked in... Columns 2 & 3 spinning...*")
            await msg.edit(embed=frame2)
            await asyncio.sleep(0.8)

            # Step 3: Column 2 stops
            frame3 = build_reel_frame(format_3x3_grid(grid, 2), "*Columns 1 & 2 locked in... Final column spinning...*")
            await msg.edit(embed=frame3)
            await asyncio.sleep(0.8)
        except (discord.NotFound, discord.HTTPException):
            break

        # Step 4: Final reveal & calculate payouts
        winnings, hits, summary_title = evaluate_3x3_slots(grid, stake)
        if winnings > 0:
            add_user_coins(ctx.author.id, winnings)
            total_won += winnings
            wins_count += 1
            mult = winnings / stake
            if mult > best_multiplier:
                best_multiplier = mult
                best_payout_title = summary_title
            outcome_text = f"🎉 **WINNER!**\n" + "\n".join(hits) + f"\n💰 Won: **+{winnings:,} 🪙**!"
            round_log_text = f"🎉 **{summary_title}** (+{winnings:,} 🪙)"
            round_color = COLOR_SUCCESS if winnings >= stake * 2 else COLOR_WARN
        else:
            outcome_text = f"💀 **No matching lines!** Lost **{stake:,} 🪙**"
            round_log_text = f"💀 **Miss** (-{stake:,} 🪙)"
            round_color = COLOR_ERROR

        cur_bal = get_user_coins(ctx.author.id)
        center_row_str = f"{grid[1][0]} {grid[1][1]} {grid[1][2]}"
        history_lines.append(f"`#{round_num}` **[ {center_row_str} ]** ➔ {round_log_text}")
        net = total_won - total_spent
        net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
        win_rate_str = f"{wins_count}/{round_num} ({wins_count/round_num*100:.0f}%)"

        final_frame = build_reel_frame(format_3x3_grid(grid, 3), outcome_text, round_color)
        final_frame.set_field_at(0, name="💰 Total Bet", value=f"**{total_spent:,} 🪙**", inline=True)
        final_frame.set_field_at(1, name="🏆 Total Won", value=f"**{total_won:,} 🪙**", inline=True)
        final_frame.set_field_at(2, name="📈 Net Outcome", value=f"**{net_str} 🪙**", inline=True)
        if total_rounds > 1:
            final_frame.set_field_at(3, name="🎯 Win Rate", value=f"**{win_rate_str}**", inline=True)
            final_frame.set_field_at(4, name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
        else:
            final_frame.set_field_at(3, name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)

        try:
            # If single round, attach spin again button directly
            if total_rounds == 1:
                final_frame.set_footer(text=f"Spun by {ctx.author.display_name} • Click Spin Again 🎰 to roll again!")
                view = SlotsSpinView(user=ctx.author, bet=stake)
                await msg.edit(embed=final_frame, view=view)
                return

            await msg.edit(embed=final_frame)
            if round_num < total_rounds:
                await asyncio.sleep(1.2)
        except (discord.NotFound, discord.HTTPException):
            break

    # Multi-Round Final Summary Card
    net = total_won - total_spent
    net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
    cur_bal = get_user_coins(ctx.author.id)

    final_color = COLOR_SUCCESS if net > 0 else (COLOR_WARN if net == 0 else COLOR_ERROR)
    summary_embed = discord.Embed(
        title=f"🎰 AIO 3x3 Slots — {len(history_lines)} Rounds Completed!",
        description=f"Auto-spin session finished for {ctx.author.mention}!\n\n**Spin Log:**\n" + "\n".join(history_lines[-8:]),
        color=final_color
    )
    summary_embed.add_field(name="💰 Total Bet", value=f"**{total_spent:,} 🪙**", inline=True)
    summary_embed.add_field(name="🏆 Total Won", value=f"**{total_won:,} 🪙**", inline=True)
    summary_embed.add_field(name="📈 Net Outcome", value=f"**{net_str} 🪙**", inline=True)
    summary_embed.add_field(name="🎯 Wins / Losses", value=f"**{wins_count}W - {len(history_lines)-wins_count}L**", inline=True)
    summary_embed.add_field(name="🌟 Best Combo", value=f"**{best_payout_title}**", inline=True)
    summary_embed.add_field(name="👛 Final Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
    summary_embed.set_footer(text=f"Completed {len(history_lines)} spins • Click Spin Again 🎰 to spin!")

    try:
        view = SlotsSpinView(user=ctx.author, bet=stake)
        await msg.edit(embed=summary_embed, view=view)
    except Exception:
        pass


@bot.hybrid_command(name="rps", description="Play Rock-Paper-Scissors against a friend or the bot")
async def rps_cmd(ctx, opponent: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if opponent and opponent.id == ctx.author.id:
        await ctx.send("❌ You cannot duel yourself in RPS!", delete_after=6)
        return
    view = RPSView(p1=ctx.author, p2=opponent)
    opp_str = opponent.mention if opponent else "AIO Bot 🤖"
    embed = discord.Embed(
        title="🪨📄✂️ Rock-Paper-Scissors",
        description=f"**{ctx.author.mention}** challenges **{opp_str}** to a duel!\n\nClick your choice below:",
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="coinflip", aliases=["flip", "coin"], description="Flip a coin with animated call and streak result")
async def coinflip_cmd(ctx, choice: Optional[Literal["heads", "tails"]] = None, bet: Optional[int] = 0):
    await safely_delete_message(ctx)
    stake = max(0, int(bet or 0))
    if stake > 0:
        if not deduct_user_coins(ctx.author.id, stake):
            cur_bal = get_user_coins(ctx.author.id)
            await ctx.send(
                f"❌ You don't have enough coins ({stake:,} 🪙) to bet! Current Balance: **{cur_bal:,} 🪙**. Run `/daily` or `/balance`.",
                delete_after=8
            )
            return

    # Animated flipping coin embed
    anim_embed = discord.Embed(title="🪙 Coinflip in Progress...", color=COLOR_INFO)
    anim_embed.description = "🪙 *Flipping the coin high into the air...*\n\n`[ 🪙 🔄 🪙 🔄 🪙 ]`"
    call_str = f" • Called: **{choice.upper()}**" if choice else ""
    stake_str = f" • Stake: **{stake:,} 🪙**" if stake > 0 else ""
    anim_embed.set_footer(text=f"Flipping for {ctx.author.display_name}{call_str}{stake_str}")
    msg = await ctx.send(embed=anim_embed)

    await asyncio.sleep(1.8)

    result = secrets.choice(["heads", "tails"])
    coin_emoji = "🪙"

    embed = discord.Embed(title=f"{coin_emoji} Coinflip Result", color=COLOR_PRIMARY)
    if choice:
        user_choice = choice.lower()
        won = (user_choice == result)
        if won:
            embed.color = COLOR_SUCCESS
            if stake > 0:
                payout = stake * 2
                add_user_coins(ctx.author.id, payout)
                cur_bal = get_user_coins(ctx.author.id)
                embed.description = f"The coin landed on **{result.upper()}**!\n\n🎉 **You called it correctly!** Won **+{stake:,} 🪙 coins**!\n👛 Balance: **{cur_bal:,} 🪙**"
            else:
                embed.description = f"The coin landed on **{result.upper()}**!\n\n🎉 **You called it correctly!**"
        else:
            embed.color = COLOR_ERROR
            if stake > 0:
                cur_bal = get_user_coins(ctx.author.id)
                embed.description = f"The coin landed on **{result.upper()}**!\n\n💀 **You called {user_choice.upper()} — Better luck next time!** Lost **{stake:,} 🪙**.\n👛 Balance: **{cur_bal:,} 🪙**"
            else:
                embed.description = f"The coin landed on **{result.upper()}**!\n\n💀 **You called {user_choice.upper()} — Better luck next time!**"
    else:
        embed.description = f"The coin landed on **{result.upper()}**!"

    embed.set_footer(text=f"Flipped by {ctx.author.display_name}")
    await msg.edit(embed=embed)


# --- COIN ECONOMY COMMANDS ---

@bot.hybrid_command(name="balance", aliases=["bal", "coins"], description="Check your or another member's coin balance")
async def balance_cmd(ctx, member: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    target = member or ctx.author
    bal = get_user_coins(target.id)
    embed = discord.Embed(
        title=f"🪙 Coin Balance — {target.display_name}",
        color=COLOR_WARN
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Wallet", value=f"**{bal:,} 🪙 coins**", inline=True)
    if target.id == ctx.author.id:
        embed.set_footer(text="Tip: Use /daily to claim 250 free coins every 24 hours!")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="daily", description="Claim your daily allowance of 250 coins")
async def daily_cmd(ctx):
    await safely_delete_message(ctx)
    success, reward_or_bal, remaining = claim_daily_coins(ctx.author.id)
    if success:
        bal = get_user_coins(ctx.author.id)
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!",
            description=f"You received **+{reward_or_bal:,} 🪙 coins**!\n\n👛 **Current Balance:** **{bal:,} 🪙 coins**",
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="Come back in 24 hours for your next reward!")
        await ctx.send(embed=embed)
    else:
        hrs = remaining // 3600
        mins = (remaining % 3600) // 60
        secs = remaining % 60
        embed = discord.Embed(
            title="⏳ Daily Already Claimed",
            description=f"You have already claimed your daily reward today!\n\n⏰ **Cooldown:** Please wait **{hrs}h {mins}m {secs}s** before claiming again.\n👛 **Current Balance:** **{reward_or_bal:,} 🪙 coins**",
            color=COLOR_WARN
        )
        await ctx.send(embed=embed, delete_after=10)


@bot.hybrid_command(name="pay", aliases=["give", "transfer"], description="Send coins to another server member")
async def pay_cmd(ctx, member: discord.Member, amount: int):
    await safely_delete_message(ctx)
    if amount <= 0:
        await ctx.send("❌ Amount must be at least 1 coin!", delete_after=6)
        return
    if member.bot:
        await ctx.send("❌ You cannot send coins to a bot!", delete_after=6)
        return
    success, msg = transfer_user_coins(ctx.author.id, member.id, amount)
    if not success:
        await ctx.send(f"❌ {msg}", delete_after=8)
        return

    sender_bal = get_user_coins(ctx.author.id)
    recipient_bal = get_user_coins(member.id)
    embed = discord.Embed(
        title="💸 Coin Transfer Completed",
        description=f"**{ctx.author.mention}** sent **{amount:,} 🪙 coins** to **{member.mention}**!",
        color=COLOR_SUCCESS
    )
    embed.add_field(name=f"{ctx.author.display_name}'s Balance", value=f"**{sender_bal:,} 🪙**", inline=True)
    embed.add_field(name=f"{member.display_name}'s Balance", value=f"**{recipient_bal:,} 🪙**", inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="leaderboard", aliases=["top", "richest", "coinboard"], description="View the top 10 richest coin holders")
async def leaderboard_cmd(ctx):
    await safely_delete_message(ctx)
    top_users = get_coin_leaderboard(limit=10)
    embed = discord.Embed(
        title="🏆 Coin Wealth Leaderboard",
        color=COLOR_WARN
    )
    if not top_users:
        embed.description = "No coin records found yet. Claim `/daily` to get on the board!"
    else:
        medals = ["🥇", "🥈", "🥉"] + [f"`#{i}`" for i in range(4, 11)]
        lines = []
        for idx, (uid, coins) in enumerate(top_users):
            prefix = medals[idx] if idx < len(medals) else f"`#{idx+1}`"
            user_obj = bot.get_user(uid)
            name = user_obj.display_name if user_obj else f"User <@{uid}>"
            lines.append(f"{prefix} **{name}** — **{coins:,} 🪙**")
        embed.description = "\n".join(lines)

    user_bal = get_user_coins(ctx.author.id)
    embed.set_footer(text=f"Your Balance: {user_bal:,} 🪙 • Earn more with /daily, /blackjack, /slots, /trivia!")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="roll", aliases=["dice"], description="Roll dice using tabletop notation (e.g. 2d6, 1d20, 100)")
async def roll_cmd(ctx, dice: Optional[str] = "1d6"):
    await safely_delete_message(ctx)
    dice_str = (dice or "1d6").strip().lower()

    try:
        if "d" in dice_str:
            parts = dice_str.split("d")
            count = int(parts[0]) if parts[0] else 1
            sides = int(parts[1])
        else:
            count = 1
            sides = int(dice_str)

        if count < 1 or count > 50 or sides < 2 or sides > 1000:
            await ctx.send("❌ Dice limits: 1–50 dice with 2–1000 sides (e.g. `2d6`, `1d20`, `100`).", delete_after=8)
            return

        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)

        embed = discord.Embed(title="🎲 Dice Roll", color=COLOR_INFO)
        embed.add_field(name="Formula", value=f"`{count}d{sides}`", inline=True)
        embed.add_field(name="Total", value=f"**{total}**", inline=True)
        if count > 1:
            rolls_str = ", ".join(str(r) for r in rolls[:20]) + ("..." if len(rolls) > 20 else "")
            embed.add_field(name="Individual Rolls", value=f"`[{rolls_str}]`", inline=False)
        embed.set_footer(text=f"Rolled by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("❌ Invalid dice format. Examples: `1d6`, `2d20`, `100`.", delete_after=6)



# --- CVS ACCOUNT BARCODE GENERATOR ---


@bot.hybrid_command(
    name="accounts",
    aliases=["cvsaccounts", "myaccounts", "cards", "cvsaccount", "cvscard", "extracare", "barcode"],
    description="Browse or search imported CVS ExtraCare accounts with barcodes & pagination"
)
@commands.is_owner()
@app_commands.default_permissions(administrator=True)
async def list_accounts_cmd(ctx, query: Optional[str] = None):
    await safely_delete_message(ctx)
    if not cvs_accounts_db:
        await ctx.send("📭 No CVS accounts currently loaded.", delete_after=8)
        return

    idx = 0
    if query:
        matched = get_cvs_account(query.strip())
        if matched:
            idx = cvs_accounts_db.index(matched)
        else:
            await ctx.send(f"⚠️ No CVS account found matching `{query}`. Opening account list from beginning.", delete_after=6)

    view = CVSAccountsPaginationView(current_idx=idx)
    embed, file = format_account_card(cvs_accounts_db[idx])
    await ctx.send(embed=embed, file=file, view=view)


# --- SETUP & CHANNELS ---

def find_cvs_optimizer_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    candidates = [
        "🛒-coupon-optimizer", "🔒-cvs-optimizer", "cvs-coupon-optimizer",
        "aio-coupon-optimizer", "coupon-optimizer"
    ]
    for name in candidates:
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch and not is_protected_channel(ch):
            return ch
    for cat in guild.categories:
        if "cvs" in cat.name.lower():
            for ch in cat.text_channels:
                if not is_protected_channel(ch):
                    return ch
    return None

def is_staff_or_admin(member: Any) -> bool:
    if member is None:
        return False
    perms = getattr(member, "guild_permissions", None)
    if perms:
        if getattr(perms, "manage_channels", False) or getattr(perms, "administrator", False) or getattr(perms, "manage_messages", False):
            return True
    staff_roles = {"staff", "moderator", "moderators", "mod", "mods", "admin", "administrator", "operator", "founder", "founders", "owner", "co-founder"}
    roles = getattr(member, "roles", [])
    return any(getattr(r, "name", "").lower() in staff_roles for r in roles)

@bot.hybrid_command(name="setup", aliases=["setup-coupon-hub", "setupcouponhub", "setupcvs", "setup-optimizer"], description="Staff command: Set up the #🛒-coupon-optimizer channel with private room launch button")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_channel(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    cat_name = "🔒 PRIVATE CVS"
    cat = discord.utils.get(guild.categories, name=cat_name)
    if not cat:
        try:
            cat = await guild.create_category(cat_name)
        except Exception as e:
            await ctx.send(f"❌ Failed to create category `{cat_name}`: {e}", delete_after=8)
            return

    channel_name = "🛒-coupon-optimizer"
    hub_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, embed_links=True)
    }
    founder_role = get_founder_role(guild)
    mod_role = get_moderator_role(guild)
    staff_role = get_staff_role(guild)
    if founder_role:
        hub_overwrites[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    if mod_role:
        hub_overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    if staff_role:
        hub_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

    existing = discord.utils.get(guild.text_channels, name=channel_name)
    if existing:
        if existing.category_id != cat.id:
            try:
                await existing.edit(category=cat, overwrites=hub_overwrites)
            except Exception:
                pass
        try:
            await existing.purge(limit=10)
        except Exception:
            pass
        hub_embed = build_coupon_hub_embed()
        await existing.send(embed=hub_embed, view=CouponHubLaunchView())
        await ctx.send(f"✅ Private Coupon Optimizer Hub refreshed at {existing.mention}! Members can click the button to open their personal room.", delete_after=8)
        return

    try:
        new_channel = await guild.create_text_channel(
            channel_name,
            category=cat,
            topic="CVS & retail coupon optimizer hub. Click the button below to open your private room!",
            overwrites=hub_overwrites
        )
        hub_embed = build_coupon_hub_embed()
        await new_channel.send(embed=hub_embed, view=CouponHubLaunchView())
        await ctx.send(f"✅ Secure Private Coupon Optimizer Hub created at {new_channel.mention}! Members can click the button to open their personal room.", delete_after=8)
    except Exception as e:
        await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)

@bot.hybrid_command(
    name="setup-mod-panel",
    aliases=["setupmodpanel", "setup-panel", "setuppanel"],
    description="Staff command: Set up the #🎛️-mod-panel channel with staff control center action buttons"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_mod_panel_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    cat_name = "🛡️ STAFF ZONE"
    cat = discord.utils.get(guild.categories, name=cat_name)
    staff_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, embed_links=True)
    }
    founder_role = get_founder_role(guild)
    mod_role = get_moderator_role(guild)
    staff_role = get_staff_role(guild)
    if founder_role:
        staff_overwrites[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    if mod_role:
        staff_overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    if staff_role:
        staff_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

    for role in guild.roles:
        if role.permissions.administrator or role.permissions.manage_guild or role.name.lower() in ("staff", "moderator", "moderators", "mod", "mods", "admin", "administrator", "founder", "founders", "owner"):
            staff_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    if not cat:
        try:
            cat = await guild.create_category(cat_name, overwrites=staff_overwrites)
        except Exception:
            pass

    channel_name = "🎛️-mod-panel"
    existing = discord.utils.get(guild.text_channels, name=channel_name)
    if existing:
        if cat and existing.category_id != cat.id:
            try:
                await existing.edit(category=cat, overwrites=staff_overwrites)
            except Exception:
                pass
        try:
            await existing.purge(limit=10)
        except Exception:
            pass
        mod_embed = build_staff_modpanel_embed()
        await existing.send(embed=mod_embed, view=StaffModPanelButtonView())
        await ctx.send(f"✅ Staff Control Center & Moderation Panel refreshed at {existing.mention}! All actions are accessible via buttons.", delete_after=8)
        return

    try:
        new_channel = await guild.create_text_channel(
            channel_name,
            category=cat,
            topic="Staff control center: execute moderation, billing, role fixes, and panel refreshes via buttons.",
            overwrites=staff_overwrites
        )
        mod_embed = build_staff_modpanel_embed()
        await new_channel.send(embed=mod_embed, view=StaffModPanelButtonView())
        await ctx.send(f"✅ Staff Control Center & Moderation Panel created at {new_channel.mention}! All actions are accessible via buttons.", delete_after=8)
    except Exception as e:
        await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)

@bot.hybrid_command(
    name="setup-all-features",
    aliases=["setupallfeatures", "setupfeatures", "setup-all", "setupall"],
    description="Staff command: Set up both Coupon Optimizer Hub and Staff Mod Panel in one step"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_all_features_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    await setup_channel(ctx)
    await setup_mod_panel_cmd(ctx)

@bot.hybrid_command(
    name="setup-food-store",
    aliases=["setupfoodstore", "setupstore", "setup-store"],
    description="Staff command: Set up the #🌮🍕-food-rewards channel with the food accounts store panel"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_food_store_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    cat_name = "🛍️ SAVINGS & REWARDS"
    cat = discord.utils.get(guild.categories, name=cat_name)
    if not cat:
        try:
            cat = await guild.create_category(cat_name)
        except Exception:
            pass

    channel_name = "🌮🍕-food-rewards"
    existing = discord.utils.get(guild.text_channels, name=channel_name)
    target_ch = existing
    if not target_ch:
        try:
            target_ch = await guild.create_text_channel(
                channel_name,
                category=cat,
                topic="Preloaded Taco Bell & Pizza Hut rewards accounts store. Order below!"
            )
        except Exception as e:
            await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)
            return
    else:
        try:
            await target_ch.purge(limit=10)
        except Exception:
            pass

    food_embed = build_food_accounts_embed()
    await target_ch.send(embed=food_embed, view=FoodAccountPurchaseView())
    await ctx.send(f"✅ Food Rewards Store panel ready at {target_ch.mention}!", delete_after=8)

@bot.hybrid_command(
    name="setup-tickets",
    aliases=["setuptickets", "setupticketpanel", "setup-ticket-panel"],
    description="Staff command: Set up the #📩-open-a-ticket channel with the support ticket launch panel"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_tickets_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    cat_name = "🎫 SUPPORT"
    cat = discord.utils.get(guild.categories, name=cat_name)
    if not cat:
        try:
            cat = await guild.create_category(cat_name)
        except Exception:
            pass

    channel_name = "📩-open-a-ticket"
    existing = discord.utils.get(guild.text_channels, name=channel_name)
    target_ch = existing
    if not target_ch:
        try:
            target_ch = await guild.create_text_channel(
                channel_name,
                category=cat,
                topic="Need help? Click the button below to open a private ticket!"
            )
        except Exception as e:
            await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)
            return
    else:
        try:
            await target_ch.purge(limit=10)
        except Exception:
            pass

    panel_embed = discord.Embed(
        title="🎫 Support & Order Help",
        description=(
            "Need assistance, have a question, or want to contact staff?\n\n"
            "**How it works:**\n"
            "▸ 🔒 A **private channel** is created just for you and staff\n"
            "▸ 👥 Only you and server staff can see it\n"
            "▸ ⚡ Staff will respond as soon as possible\n\n"
            "Click the button below to open your ticket."
        ),
        color=COLOR_PRIMARY
    )
    panel_embed.set_footer(text="AIO Bot Custom Ticket Center • One ticket per user")
    await target_ch.send(embed=panel_embed, view=TicketLaunchView())
    await ctx.send(f"✅ Ticket launch panel ready at {target_ch.mention}!", delete_after=8)

@bot.hybrid_command(
    name="sync-commands",
    aliases=["sync", "forcesync"],
    description="Founder command: Force sync global application slash commands with Discord"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def sync_commands_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Administrator permission required to sync commands.", delete_after=6)
        return

    msg = await ctx.send("🔄 Syncing application slash commands with Discord...")
    try:
        synced = await bot.tree.sync()
        await msg.edit(content=f"✅ Successfully synced **{len(synced)}** global slash commands with Discord!")
    except Exception as e:
        await msg.edit(content=f"⚠️ Error syncing slash commands: `{e}`")

@bot.hybrid_command(
    name="permit",
    aliases=["grant", "giveaccess", "cvsaccess", "allow"],
    description="Staff command: Grant a member private access to the CVS coupon optimizer"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(member="The member to grant private access to", channel="Optional specific channel (defaults to CVS optimizer)")
async def permit_user(ctx, member: discord.Member, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to grant access.", delete_after=6)
        return

    target_channel = channel or find_cvs_optimizer_channel(ctx.guild)
    if not target_channel:
        await ctx.send("❌ CVS coupon optimizer channel not found! Run `/formatserver` or `/setup` first.", delete_after=6)
        return

    await target_channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        read_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        reason=f"Granted access by staff: {ctx.author}"
    )

    embed = discord.Embed(
        title="🔓 Access Granted",
        description=f"✅ Successfully granted {member.mention} access to {target_channel.mention}!",
        color=COLOR_SUCCESS
    )
    embed.set_footer(text=f"Authorized by {ctx.author.display_name}")
    await ctx.send(embed=embed)

    welcome_embed = discord.Embed(
        title="👋 Welcome to Private CVS Optimizer!",
        description=(
            f"Welcome {member.mention}! You have been granted access to the private CVS optimizer.\n\n"
            "• Use `/panel` or `!panel` to open the interactive shopping cart.\n"
            "• Use `/add` to add items and store coupons.\n"
            "• Use `/optimize` to compute the lowest out-of-pocket trip total!"
        ),
        color=COLOR_PRIMARY
    )
    welcome_embed.set_footer(text="Private CVS Optimizer Access")
    try:
        await target_channel.send(content=member.mention, embed=welcome_embed)
    except Exception:
        pass

@bot.hybrid_command(
    name="revoke",
    aliases=["removeaccess", "deny", "unpermit"],
    description="Staff command: Revoke a member's access from the private CVS coupon optimizer"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(member="The member to remove access from", channel="Optional specific channel (defaults to CVS optimizer)")
async def revoke_user(ctx, member: discord.Member, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to revoke access.", delete_after=6)
        return

    target_channel = channel or find_cvs_optimizer_channel(ctx.guild)
    if not target_channel:
        await ctx.send("❌ CVS coupon optimizer channel not found!", delete_after=6)
        return

    await target_channel.set_permissions(member, overwrite=None, reason=f"Revoked access by staff: {ctx.author}")
    embed = discord.Embed(
        title="🔒 Access Revoked",
        description=f"🔒 Revoked access for {member.mention} from {target_channel.mention}.",
        color=COLOR_WARN
    )
    embed.set_footer(text=f"Action by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.hybrid_command(
    name="formatserver",
    aliases=["setupserver", "buildserver", "templateserver"],
    description="Format and organize server channels into a clean professional layout"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def format_server(ctx):
    await safely_delete_message(ctx)
    def _m(name: str) -> str:
        return get_channel_mention(ctx.guild, name, f"`#{name}`")

    desc = (
        "This command will organize and build a clean, professional server layout with organized categories, topic channels, and proper permissions.\n\n"
        f"🛡️ **SAFEGUARD ACTIVE:** {_m('form-automation')} is permanently protected and will NEVER be touched, modified, or moved.\n\n"
        "**Blueprint Structure:**\n"
        f"• 📌 **INFORMATION**: {_m('📢-announcements')}, {_m('📜-rules')}, {_m('👋-welcome')}\n"
        f"• 💬 **COMMUNITY**: {_m('💬-general-chat')}, {_m('🤖-bot-commands')}, {_m('💡-suggestions')}\n"
        f"• 🔒 **PRIVATE CVS**: {_m('🛒-coupon-optimizer')} *(Click button to open private room)*\n"
        f"• 🛍️ **SAVINGS & REWARDS**: {_m('🌮🍕-food-rewards')}, {_m('🏷️-deals-and-savings')}, {_m('🧾-receipt-brags')}\n"
        f"• 🎫 **SUPPORT**: {_m('📩-open-a-ticket')} *(with Ticket Panel!)*\n"
        "• 🔊 **VOICE CHANNELS**: `🔊 General Voice`, `🔊 Lounge 1`\n"
        f"• 🛡️ **STAFF ZONE**: {_m('🛡️-staff-chat')}, {_m('📜-mod-logs')}, {_m('🎛️-mod-panel')} *(staff-only control panel)*\n\n"
        "**Options Below:**\n"
        "• **Format & Clean Old Channels**: Sets up the blueprint AND wipes leftover/unformatted channels\n"
        "• **Format (Keep Old)**: Sets up the blueprint alongside existing channels"
    )

    embed = discord.Embed(
        title="🏗️ Server Layout Formatter & Architect",
        description=desc,
        color=COLOR_PRIMARY
    )
    embed.set_footer(text="Admin Command • Choose an option below")
    view = FormatServerConfirmView(author_id=ctx.author.id)
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="deletechannels",
    aliases=["clearchannels", "cleanupchannels", "prunechannels", "wipeoldchannels"],
    description="Interactive menu or purge to delete previous or leftover server channels (protects #form-automation)"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(mode="Choose interactive multi-select menu, or directly clean old channels / wipe all")
@app_commands.choices(mode=[
    app_commands.Choice(name="Interactive Multi-Selection Panel (Dropdown with checkboxes & quick buttons)", value="menu"),
    app_commands.Choice(name="Quick Action: Clean Old / Unformatted Channels (Keeps Formatted Blueprint)", value="clean_old"),
    app_commands.Choice(name="Quick Action: Wipe All Server Channels (Keeps ONLY #form-automation)", value="wipe_all")
])
async def delete_channels_cmd(ctx, mode: str = "menu"):
    await safely_delete_message(ctx)
    guild = ctx.guild
    if not guild:
        return

    if mode == "menu":
        view = ChannelDeleteInteractiveView(author_id=ctx.author.id, guild=guild)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)
        return

    target_channels = []
    target_categories = []
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            if not is_preserved_channel(ch, mode=mode):
                target_categories.append(ch)
        else:
            if not is_preserved_channel(ch, mode=mode):
                target_channels.append(ch)

    total_count = len(target_channels) + len(target_categories)
    if total_count == 0:
        await ctx.send(
            "✅ **No leftover channels found!**\nAll channels either match the formatted layout, active tickets, or are protected `#form-automation`.",
            delete_after=10
        )
        return

    preview_items = [f"`#{c.name}`" for c in target_channels[:8]]
    if len(target_channels) > 8:
        preview_items.append(f"...and {len(target_channels) - 8} more channels")
    if target_categories:
        preview_items.append(f"📁 Categories: {', '.join(f'`{c.name}`' for c in target_categories[:4])}")

    preview_str = "\n• ".join(preview_items) if preview_items else "None"

    embed = discord.Embed(
        title="🗑️ Confirm Channel Deletion",
        description=(
            f"Are you sure you want to delete **{total_count}** previous channel(s) / categories?\n\n"
            f"• **Mode:** `{'Clean Old / Unformatted Channels' if mode == 'clean_old' else 'Wipe All Server Channels'}`\n"
            f"• 🛡️ **SAFEGUARD:** `#form-automation` is permanently protected and will NOT be touched.\n\n"
            f"**Target Channels Preview ({total_count} total):**\n• {preview_str}\n\n"
            f"⚠️ *This action is permanent and cannot be undone.* Click **Confirm Delete Channels** to proceed."
        ),
        color=COLOR_ERROR if mode == "wipe_all" else COLOR_WARN
    )
    embed.set_footer(text="AIO Bot Server Purge Suite • Requires confirmation")
    view = DeleteChannelsConfirmView(author_id=ctx.author.id, mode=mode, count=total_count)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(
    name="ticketpanel",
    aliases=["tickets", "ticket-panel", "ticketsetup"],
    description="Post the interactive support ticket panel in this channel"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def post_ticket_panel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target_channel = channel or ctx.channel
    embed = discord.Embed(
        title="🎫 Support & Inquiries",
        description=(
            "Need assistance, have questions, or need to contact staff?\n\n"
            "Click the **Open Ticket** button below to create a private support channel with our team.\n\n"
            "• 🔒 Private 1-on-1 text channel\n"
            "• 👥 Only you and server staff have access\n"
            "• ⚡ Fast response from operators"
        ),
        color=COLOR_PRIMARY
    )
    embed.set_footer(text="AIO Bot Custom Ticket Center • Click below to open")
    view = TicketLaunchView()
    await target_channel.send(embed=embed, view=view)
    if target_channel.id != ctx.channel.id:
        await ctx.send(f"✅ Ticket panel deployed to {target_channel.mention}!", delete_after=5)


@bot.hybrid_command(
    name="resetchannel",
    aliases=["refreshchannel", "updatechannel", "channelreset"],
    description="Staff command: Clear and refresh an individual channel with its latest updated bot panel and info"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(
    channel="Channel to reset/refresh (optional, defaults to current channel)",
    clear_history="Whether to purge previous messages in the channel (default True)"
)
async def resetchannel_cmd(
    ctx: commands.Context,
    channel: Optional[discord.TextChannel] = None,
    clear_history: bool = True
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not ctx.author.guild_permissions.manage_channels and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ You need Manage Channels permission or Staff role to reset channels.", delete_after=6)
        return

    target_ch = channel or ctx.channel
    if not isinstance(target_ch, discord.TextChannel):
        await ctx.send("❌ This command must be run in a text channel.", delete_after=6)
        return

    if is_protected_channel(target_ch):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` is permanently protected and CANNOT be reset, cleared, or modified!", delete_after=8)
        return

    is_same_ch = (target_ch.id == ctx.channel.id)
    status_msg = None
    if not is_same_ch:
        status_msg = await ctx.send(f"⏳ **Refreshing channel {target_ch.mention}...**")

    try:
        deployed_name = await refresh_channel_content(target_ch, ctx.author.id, clear_history=clear_history)
        embed = discord.Embed(
            title="✨ Channel Successfully Refreshed",
            description=(
                f"Successfully reset and refreshed **{target_ch.mention}**!\n\n"
                f"• 📋 **Panel Deployed:** {deployed_name}\n"
                f"• 🧹 **Messages Purged:** {'Yes (Previous messages cleaned)' if clear_history else 'No (Previous messages kept)'}\n"
                "• 🔄 Now displaying the most up-to-date info and interactive buttons."
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Channel Management Suite")
        if is_same_ch:
            await target_ch.send(embed=embed, delete_after=10)
        else:
            await status_msg.edit(content=None, embed=embed)
    except Exception as e:
        err_text = f"❌ Error resetting channel {target_ch.mention}: {e}"
        if is_same_ch:
            await target_ch.send(err_text, delete_after=8)
        else:
            await status_msg.edit(content=err_text)

async def _do_say(channel: discord.TextChannel, text: Optional[str], attachments: List[discord.Attachment]) -> bool:
    files = []
    for att in attachments:
        try:
            data = await att.read()
            files.append(discord.File(io.BytesIO(data), filename=att.filename))
        except Exception as e:
            print(f"⚠️ Error reading attachment {att.filename}: {e}", file=sys.stderr)

    if not text and not files:
        return False

    await channel.send(content=text if text else None, files=files if files else None)
    return True

@bot.command(name="say", aliases=["echo", "repeat", "repost", "botmsg"])
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def say_prefix_cmd(ctx: commands.Context, *, message: Optional[str] = None):
    await safely_delete_message(ctx)
    attachments = list(ctx.message.attachments) if ctx.message else []
    success = await _do_say(ctx.channel, message, attachments)
    if not success:
        await ctx.send("❌ Please provide text or an attached photo to repost.", delete_after=6)

@bot.tree.command(name="say", description="Reposts your message and any attached photos through the bot")
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(message="Text to repost", photo="Optional photo/image to repost", photo2="Second optional photo/image")
async def say_slash_cmd(
    interaction: discord.Interaction,
    message: Optional[str] = None,
    photo: Optional[discord.Attachment] = None,
    photo2: Optional[discord.Attachment] = None
):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("⛔ This command must be run in a server text channel.", ephemeral=True)
        return

    if not (interaction.user.guild_permissions.manage_messages or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("⛔ You need the Manage Messages permission to use /say.", ephemeral=True)
        return

    attachments = [p for p in (photo, photo2) if p is not None]
    if not message and not attachments:
        await interaction.response.send_message("❌ Please provide text or an attached photo to repost.", ephemeral=True)
        return

    await interaction.response.send_message("✅ Reposted!", ephemeral=True)
    await _do_say(interaction.channel, message, attachments)

@bot.hybrid_command(
    name="foodpanel",
    aliases=["rewardsstore", "fastfood", "foodaccounts"],
    description="Deploy the Taco Bell & Pizza Hut preloaded account purchase panel"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def post_food_panel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    embed = build_food_accounts_embed()
    view = FoodAccountPurchaseView()
    await target.send(embed=embed, view=view)
    if target.id != ctx.channel.id:
        await ctx.send(f"✅ Fast food rewards purchase panel deployed to {target.mention}!", delete_after=5)

# ============================================================
# STAFF TICKET & ORDER FULFILLMENT COMMANDS
# ============================================================

@bot.hybrid_command(
    name="paid",
    aliases=["orderpaid", "markpaid"],
    description="Staff command: Confirm payment received and update ticket status"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    amount="Amount received (e.g. $10, 10.00)",
    method="Payment method (CashApp, ApplePay, Crypto, etc.)"
)
async def paid_cmd(ctx: commands.Context, amount: Optional[str] = None, *, method: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can mark orders as paid.", delete_after=6)
        return

    update_ticket_status(ctx.channel.id, "paid")
    t_info = tickets_db.get("tickets", {}).get(str(ctx.channel.id), {})
    amt_text = amount.strip() if amount else None
    if not amt_text and t_info.get("invoice_amount"):
        amt_text = f"${t_info['invoice_amount']:.2f}"
    elif amt_text and not amt_text.startswith("$") and re.match(r"^\d+(\.\d{2})?$", amt_text):
        amt_text = f"${amt_text}"

    embed = discord.Embed(
        title="💰 Payment Verified & Received",
        description=(
            f"Payment has been confirmed by {ctx.author.mention}!\n\n"
            "• **Status:** `PAID / PROCESSING`\n"
            "• Staff is preparing your account credentials or fulfillment now."
        ),
        color=0x2ecc71
    )
    if amt_text:
        embed.add_field(name="💵 Amount Paid", value=f"**{amt_text}**", inline=True)
    if method:
        embed.add_field(name="💳 Method", value=f"**{method.strip()}**", inline=True)
    embed.add_field(name="⏰ Confirmed At", value=f"<t:{int(time.time())}:R>", inline=True)
    embed.set_footer(text=f"Confirmed by {ctx.author.display_name} • AIO Order Suite")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="complete",
    aliases=["ordercomplete", "fulfill", "done"],
    description="Staff command: Mark order/ticket fulfilled and completed"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(notes="Optional completion notes or delivery summary")
async def complete_cmd(ctx: commands.Context, *, notes: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can complete orders.", delete_after=6)
        return

    update_ticket_status(ctx.channel.id, "completed")
    t_info = tickets_db.get("tickets", {}).get(str(ctx.channel.id), {})
    ticket_id = t_info.get("id", 0)
    customer_id = t_info.get("owner_id", 0)
    customer = ctx.guild.get_member(customer_id) if ctx.guild and customer_id else None
    customer_name = str(customer) if customer else (f"User-{customer_id}" if customer_id else "Customer")

    ch_name = getattr(ctx.channel, "name", "")
    brand = "Taco Bell" if "taco" in ch_name.lower() else ("Pizza Hut" if "pizza" in ch_name.lower() else "Fast Food Rewards")
    amt = t_info.get("invoice_amount")
    if amt is None:
        amt = 10.0 if "taco" in brand.lower() else (15.0 if "pizza" in brand.lower() else 0.0)

    # Record order in persistent stats tracker
    record_completed_order(
        guild_id=ctx.guild.id if ctx.guild else 0,
        ticket_id=ticket_id,
        channel_id=ctx.channel.id,
        channel_name=ch_name,
        customer_id=customer_id,
        customer_name=customer_name,
        completed_by_id=ctx.author.id,
        completed_by_name=str(ctx.author),
        brand=brand,
        amount=amt,
        notes=notes
    )

    receipt_mention = get_channel_mention(ctx.guild, "🧾-receipt-brags", "#receipt-brags")
    embed = discord.Embed(
        title="🎉 Order Fulfilled & Completed!",
        description=(
            f"Your order has been marked as completed by {ctx.author.mention}!\n\n"
            f"Thank you for shopping with us! If you loved the service, drop a shoutout in {receipt_mention} 🎉\n\n"
            "You may click **Close Ticket** below when finished."
        ),
        color=0x9b59b6
    )
    if notes:
        embed.add_field(name="📝 Notes", value=notes.strip(), inline=False)
    embed.add_field(name="⏰ Completed At", value=f"<t:{int(time.time())}:R>", inline=True)
    embed.set_footer(text=f"Fulfilled by {ctx.author.display_name} • AIO Fulfillment Suite")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="otp",
    aliases=["code", "totp", "verifycode"],
    description="Staff command: Send Taco Bell OTP code in a tap-to-copy block"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    code="The OTP verification code",
    notes="Optional instructions or notes"
)
async def otp_cmd(ctx: commands.Context, code: str, *, notes: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can dispatch OTP codes.", delete_after=6)
        return

    clean_code = code.strip()
    embed = discord.Embed(
        title="🔑 Taco Bell App Login OTP Code",
        description=(
            "Here is your verification code! Tap the box below to copy it immediately:\n\n"
            f"```\n{clean_code}\n```\n"
            "⚠️ **Expires in 5 minutes!** Enter this code into the Taco Bell app now."
        ),
        color=0xe67e22
    )
    if notes:
        embed.add_field(name="📝 Notes", value=notes.strip(), inline=False)
    embed.set_footer(text=f"Sent by {ctx.author.display_name} • AIO Security Suite")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="deliver",
    aliases=["deliveraccount", "credentials", "sendacc"],
    description="Staff command: Securely deliver account credentials inside spoiler tags"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    credentials="Login details/credentials to deliver (email:pass, token, etc.)",
    notes="Optional customer instructions or notes"
)
async def deliver_cmd(ctx: commands.Context, credentials: str, *, notes: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can deliver accounts.", delete_after=6)
        return

    embed = discord.Embed(
        title="📦 Fast Food Account Delivered",
        description=(
            "Your preloaded rewards account is ready! Click the black spoiler box below to reveal your login details:\n\n"
            f"|| `{credentials.strip()}` ||\n\n"
            "• **Next Step:** Log in immediately to verify your rewards.\n"
            "• **Security Reminder:** Save your credentials in a safe place.\n"
            "• If you have questions or need assistance, ping staff here!"
        ),
        color=0x2ecc71
    )
    if notes:
        embed.add_field(name="📝 Staff Note", value=notes.strip(), inline=False)
    embed.set_footer(text=f"Delivered by {ctx.author.display_name} • AIO Fulfillment Suite")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="claim",
    aliases=["ticketclaim"],
    description="Staff command: Claim the current ticket"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def claim_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can claim tickets.", delete_after=6)
        return

    claim_ticket_record(ctx.channel.id, ctx.author.id)
    embed = discord.Embed(
        title="📌 Ticket Claimed",
        description=f"{ctx.author.mention} has claimed this ticket and will be assisting you!",
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="close",
    aliases=["closeticket", "ticketclose"],
    description="Close the current ticket channel (shields #form-automation)"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def close_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if is_protected_channel(ctx.channel):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` CANNOT be closed or deleted!", delete_after=8)
        return

    info = tickets_db.get("tickets", {}).get(str(ctx.channel.id), {})
    is_owner = (info.get("owner_id") == ctx.author.id)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author) and not is_owner:
        await ctx.send("⛔ Only staff, founders, or the ticket owner can close this ticket.", delete_after=6)
        return

    await ctx.send(
        "⚠️ **Close Ticket Confirmation**\nAre you sure you want to close this ticket? This will delete the channel.",
        view=TicketCloseConfirmView()
    )


# --- INVOICING / BILLING COMMAND ---
@bot.hybrid_command(
    name="invoice",
    aliases=["bill", "createbill", "sendinvoice"],
    description="Staff command: Generate a payment bill/invoice with CashApp and Venmo"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    price="Total price due (e.g. 10.00, $15, 20)",
    cashapp="Your Cash App handle/$cashtag (e.g. $cody or cody)",
    venmo="Your Venmo handle (e.g. @cody or cody)",
    item="Item/service name (optional, e.g. Taco Bell 1x Account)",
    customer="Customer to invoice (optional, defaults to ticket owner)"
)
async def invoice_cmd(
    ctx: commands.Context,
    price: str,
    cashapp: Optional[str] = None,
    venmo: Optional[str] = None,
    item: Optional[str] = None,
    customer: Optional[discord.Member] = None
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can generate invoices.", delete_after=6)
        return

    embed, target_cust, err = create_invoice_embed(
        author=ctx.author,
        channel=ctx.channel,
        price_str=price,
        cashapp=cashapp,
        venmo=venmo,
        item=item,
        customer=customer
    )
    if err:
        await ctx.send(err, delete_after=6)
        return

    ping_cust = target_cust.mention if target_cust else ""
    await ctx.send(content=f"{ping_cust} Here is your official invoice:", embed=embed)


# --- COMPLETED ORDER STATS COMMANDS ---
@bot.hybrid_command(
    name="orderstats",
    aliases=["completedorders", "sales", "orders"],
    description="Staff command: View completed order statistics and history"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(action="Optional action: 'view' (default) or 'reset' (clears all test orders)")
async def orderstats_cmd(ctx: commands.Context, action: Optional[str] = "view"):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can view order stats.", delete_after=6)
        return

    act = (action or "view").lower().strip()
    if act in ("reset", "clear_all", "clearall", "wipe"):
        cleared_count = clear_completed_orders(guild_id=ctx.guild.id if ctx.guild else None)
        embed = discord.Embed(
            title="🧹 Order Stats Reset",
            description=f"Cleared **{cleared_count}** order(s) from the stat tracker!",
            color=COLOR_SUCCESS
        )
        await ctx.send(embed=embed)
        return

    embed = build_order_stats_embed(ctx.guild)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="clearorder",
    aliases=["removeorder", "delorder", "deleteorder"],
    description="Staff command: Manually remove a test or cancelled order from the stat tracker"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(order_id="The Order ID or Ticket Number to remove (e.g. 1, 2, 0002)")
async def clearorder_cmd(ctx: commands.Context, order_id: str):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can edit order stats.", delete_after=6)
        return

    clean_id_str = order_id.replace("#", "").strip()
    try:
        target_num = int(clean_id_str)
    except ValueError:
        await ctx.send("❌ Please enter a valid order number or ticket ID (e.g. `/clearorder 2`).", delete_after=6)
        return

    removed = remove_completed_order(target_num, guild_id=ctx.guild.id if ctx.guild else None)
    if removed:
        remaining = len([o for o in tickets_db.get("completed_orders", []) if not ctx.guild or o.get("guild_id") == ctx.guild.id])
        embed = discord.Embed(
            title="🗑️ Order Removed from Tracker",
            description=(
                f"Successfully removed Order **`#{removed.get('order_id', target_num):02d}`** ({removed.get('brand', 'Item')}) from stats.\n\n"
                f"• **Amount Reverted:** ${removed.get('amount', 0.0):.2f}\n"
                f"• **Remaining Completed Orders:** {remaining}\n"
                "• *This will no longer count towards completed order statistics.*"
            ),
            color=COLOR_WARN
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"❌ Could not find Order or Ticket `#{target_num}` in the completed orders tracker.", delete_after=8)


# --- ROLE CLEANUP & FIX COMMAND ---
@bot.hybrid_command(
    name="fixroles",
    aliases=["cleanroles", "repairroles", "fixhierarchy", "demotebot"],
    description="Staff command: Audit roles, unhoist bot roles, and repair server hierarchy"
)
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
async def fixroles_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_admin_member(ctx.author):
        await ctx.send("⛔ Only server administrators or founders can fix server roles.", delete_after=6)
        return

    status_msg = await ctx.send("⏳ **Auditing and fixing server roles...** Please wait.")
    results = await fix_server_roles(ctx.guild)

    desc = "Server role audit and hierarchy cleanup complete!\n\n"
    if results["moderators_merged"] > 0:
        desc += f"• 🧹 **Duplicate Moderator Roles Merged/Deleted:** {results['moderators_merged']}\n"
    else:
        desc += "• ✅ **Moderator Roles:** Clean (No duplicate roles detected)\n"

    if results["staff_role_removed"]:
        desc += "• 🗑️ **Redundant Staff Role:** Removed (Members migrated to Moderator)\n"
    else:
        desc += "• ✅ **Staff Role:** None present / Already cleaned\n"

    if results.get("bot_roles_unhoisted", 0) > 0:
        desc += f"• 🔽 **Bot Roles Lowered / Unhoisted:** {results['bot_roles_unhoisted']} *(Bot will not display above you in member list)*\n"
    if results.get("bot_roles_stripped", 0) > 0:
        desc += f"• 🛡️ **Roles Stripped from Bot:** Removed {results['bot_roles_stripped']} human staff/founder role(s) from the bot.\n"

    founder_role = get_founder_role(ctx.guild)
    mod_role = get_moderator_role(ctx.guild)
    desc += (
        f"\n**Active Server Staff Roles:**\n"
        f"• 👑 **Founder Role:** {founder_role.mention if founder_role else 'None'}\n"
        f"• 🛡️ **Moderator Role:** {mod_role.mention if mod_role else 'None'}\n"
    )

    if results.get("bot_is_top"):
        bot_name = ctx.guild.me.display_name if ctx.guild and ctx.guild.me else "Bot"
        desc += (
            f"\n> 👑 **Role Hierarchy Tip:**\n"
            f"> Under Discord's security rules, bots cannot move their own integration role below other roles via API.\n"
            f"> **To place yourself at the very top of Server Settings:**\n"
            f"> Go to **Server Settings ➔ Roles ➔ Drag @Founder ABOVE @{bot_name}**."
        )

    embed = discord.Embed(
        title="🛡️ Server Roles & Hierarchy Repaired",
        description=desc,
        color=COLOR_SUCCESS
    )
    embed.set_footer(text="AIO Role Management Suite")
    await status_msg.edit(content=None, embed=embed)


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
        description="All-In-One Discord assistant featuring advanced server moderation, coupon & shopping optimization, and server mini-games.",
        color=COLOR_PRIMARY
    )
    latency_ms = round(bot.latency * 1000) if bot.latency and not (bot.latency != bot.latency) else 0
    embed.add_field(name="⚡ Latency", value=f"{latency_ms}ms", inline=True)
    embed.add_field(name="🌐 Servers", value=f"{len(bot.guilds):,}", inline=True)
    embed.add_field(name="👥 Total Users", value=f"{sum(g.member_count or 0 for g in bot.guilds):,}", inline=True)
    embed.set_footer(text="AIO Bot • Use /help to explore all commands")
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

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = "⛔ Security Error: Only the bot application owner can run this operator command."
    elif isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(p.replace('_', ' ').title() for p in error.missing_permissions)
        msg = f"⛔ You need the **{missing}** permission to run that."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(p.replace('_', ' ').title() for p in error.missing_permissions)
        msg = f"❌ The bot needs the **{missing}** permission to execute this."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "⛔ This command can only be used inside a server channel."
    else:
        print(f"❌ App Command Error in '/{interaction.command.name if interaction.command else 'unknown'}': {type(error).__name__} | Details: {error}", file=sys.stderr)
        msg = f"❌ Error: `{type(error).__name__}: {error}`"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

def main():
    keep_alive()
    port_val = os.environ.get('PORT', '8000')
    port = int(port_val) if port_val and str(port_val).strip().isdigit() else 8000
    token_raw = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
    token = token_raw.strip().strip("'\"") if token_raw else None
    if not token or token in ("", "YOUR_BOT_TOKEN_HERE"):
        print("=" * 60, flush=True)
        print("⚠️ NOTICE: DISCORD_BOT_TOKEN environment variable is not set.", flush=True)
        print(f"The background web server is running on port {port}.", flush=True)
        print("Please configure DISCORD_BOT_TOKEN in your Railway / hosting service Variables tab.", flush=True)
        print("=" * 60, flush=True)
        try:
            import time
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            print("Bot shutdown.", flush=True)
    else:
        try:
            bot.run(token)
        except discord.errors.PrivilegedIntentsRequired:
            print("=" * 60, file=sys.stderr, flush=True)
            print("❌ CRITICAL ERROR: Privileged Gateway Intents are not enabled in Discord Developer Portal!", file=sys.stderr, flush=True)
            print("1. Go to https://discord.com/developers/applications", file=sys.stderr, flush=True)
            print("2. Click your bot application -> 'Bot' tab.", file=sys.stderr, flush=True)
            print("3. Scroll down to 'Privileged Gateway Intents'.", file=sys.stderr, flush=True)
            print("4. Turn ON: 'MESSAGE CONTENT INTENT' and 'SERVER MEMBERS INTENT'.", file=sys.stderr, flush=True)
            print("5. Click 'Save Changes' at the bottom and redeploy on Railway.", file=sys.stderr, flush=True)
            print("=" * 60, file=sys.stderr, flush=True)
            import time
            while True:
                time.sleep(3600)
        except discord.errors.LoginFailure:
            print("=" * 60, file=sys.stderr, flush=True)
            print("❌ CRITICAL ERROR: Invalid DISCORD_BOT_TOKEN provided!", file=sys.stderr, flush=True)
            print("Please check your Railway Variables and paste your valid Discord Bot Token.", file=sys.stderr, flush=True)
            print("=" * 60, file=sys.stderr, flush=True)
            import time
            while True:
                time.sleep(3600)
        except Exception as e:
            print(f"❌ Fatal error starting Discord bot: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            import time
            while True:
                time.sleep(3600)

if __name__ == '__main__':
    main()
