from __future__ import annotations
import io
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands, tasks
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
    temp_file = f"{filename}.tmp.{os.getpid()}"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, filename)
    except Exception as e:
        print(f"⚠️ Failed to save {filename}: {e}", file=sys.stderr)
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass

session_channels: Dict[str, int] = load_json_file(SESSION_CHANNELS_FILE, {})
warnings_db: Dict[str, List[Dict[str, Any]]] = load_json_file(WARNINGS_FILE, {})
CVS_ACCOUNTS_FILE = "cvs_accounts.json"
cvs_accounts_db: List[Dict[str, Any]] = load_json_file(CVS_ACCOUNTS_FILE, [])

def save_cvs_accounts(data: List[Dict[str, Any]]) -> None:
    save_json_file(CVS_ACCOUNTS_FILE, data)

# --- SERVER-ISOLATED DISPENSER DATABASE ---
# Kept 100% separate from Cody's personal CVS accounts (cvs_accounts.json).
# Keyed strictly by str(guild_id) to ensure zero cross-talk between servers.
SERVER_DISPENSERS_FILE = "server_dispensers.json"
server_dispensers_db: Dict[str, Dict[str, Any]] = load_json_file(SERVER_DISPENSERS_FILE, {})

def save_server_dispensers(data: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    if data is None:
        data = server_dispensers_db
    save_json_file(SERVER_DISPENSERS_FILE, data)

def get_guild_dispenser(guild_id: Union[int, str]) -> Dict[str, Any]:
    gid = str(guild_id)
    if gid not in server_dispensers_db or not isinstance(server_dispensers_db[gid], dict):
        server_dispensers_db[gid] = {
            "accounts": [],
            "settings": {
                "cooldown_hours": 0,
                "role_required": None
            }
        }
    if "accounts" not in server_dispensers_db[gid] or not isinstance(server_dispensers_db[gid]["accounts"], list):
        server_dispensers_db[gid]["accounts"] = []
    if "settings" not in server_dispensers_db[gid] or not isinstance(server_dispensers_db[gid]["settings"], dict):
        server_dispensers_db[gid]["settings"] = {"cooldown_hours": 0, "role_required": None}
    return server_dispensers_db[gid]

def parse_dispenser_entries(raw_text: str) -> List[str]:
    raw_text = raw_text.strip()
    if not raw_text:
        return []
    # If separated by delimiter lines like '---' or '==='
    if "\n---" in raw_text or "\n===" in raw_text:
        parts = re.split(r'\n\s*[-=]{3,}\s*\n?', raw_text)
        return [p.strip() for p in parts if p.strip()]
    # If separated by double-newlines (blank lines between accounts)
    if "\n\n" in raw_text:
        parts = raw_text.split("\n\n")
        cleaned = [p.strip() for p in parts if p.strip()]
        if len(cleaned) > 1:
            return cleaned
    # If single lines and multiple lines exist
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) > 1 and all(len(line) < 200 for line in lines):
        return lines
    return [raw_text]

def add_guild_dispenser_accounts(
    guild_id: Union[int, str],
    entries: List[str],
    user: Optional[Union[discord.Member, discord.User]] = None
) -> int:
    dispenser = get_guild_dispenser(guild_id)
    added_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    user_id = getattr(user, "id", None)
    user_name = str(user) if user else "Unknown"

    current_ids = [a.get("id", 0) for a in dispenser["accounts"] if isinstance(a.get("id"), int)]
    next_id = (max(current_ids) + 1) if current_ids else 1

    for raw in entries:
        cleaned = raw.strip()
        if not cleaned:
            continue
        dispenser["accounts"].append({
            "id": next_id,
            "content": cleaned,
            "added_by": user_id,
            "added_by_name": user_name,
            "added_at": now_iso,
            "dispensed": False,
            "dispensed_to": None,
            "dispensed_to_name": None,
            "dispensed_at": None
        })
        next_id += 1
        added_count += 1

    if added_count > 0:
        save_server_dispensers()
    return added_count

def get_guild_dispenser_stats(guild_id: Union[int, str]) -> Dict[str, Any]:
    dispenser = get_guild_dispenser(guild_id)
    accounts = dispenser.get("accounts", [])
    available = [a for a in accounts if not a.get("dispensed", False)]
    dispensed = [a for a in accounts if a.get("dispensed", False)]
    return {
        "total": len(accounts),
        "available": len(available),
        "dispensed": len(dispensed),
        "recent_dispensed": dispensed[-5:] if dispensed else []
    }

def dispense_guild_account(
    guild_id: Union[int, str],
    user: Optional[Union[discord.Member, discord.User]] = None,
    staff: Optional[Union[discord.Member, discord.User]] = None
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    dispenser = get_guild_dispenser(guild_id)
    accounts = dispenser.get("accounts", [])

    # Find first available account
    available_acc = None
    for a in accounts:
        if not a.get("dispensed", False):
            available_acc = a
            break

    if not available_acc:
        return False, "⚠️ **Out of Stock!** There are currently no accounts available in this server's dispenser. Use `/addaccount` to restock.", None

    available_acc["dispensed"] = True
    available_acc["dispensed_to"] = getattr(user, "id", None) if user else None
    available_acc["dispensed_to_name"] = str(user) if user else "Customer"
    available_acc["dispensed_by"] = getattr(staff, "id", None) if staff else None
    available_acc["dispensed_by_name"] = str(staff) if staff else "Staff"
    available_acc["dispensed_at"] = datetime.now(timezone.utc).isoformat()
    save_server_dispensers()

    return True, "success", available_acc

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

def mark_coupon_used(
    account_query: Optional[Union[int, str, Dict[str, Any]]] = None,
    coupon_name: str = "",
    savings: Optional[float] = None,
    notes: Optional[str] = None,
    user_tag: Optional[str] = None
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Marks a coupon as used/redeemed on a CVS account, updating active and used coupon lists."""
    if not cvs_accounts_db:
        return False, "No CVS accounts currently in database.", None

    target_acc = None
    if isinstance(account_query, dict):
        target_acc = account_query
    elif isinstance(account_query, int):
        for acc in cvs_accounts_db:
            if acc.get("id") == account_query:
                target_acc = acc
                break
    elif isinstance(account_query, str) and account_query.strip():
        clean_q = account_query.strip()
        if clean_q.isdigit():
            aid = int(clean_q)
            for acc in cvs_accounts_db:
                if acc.get("id") == aid:
                    target_acc = acc
                    break
        if not target_acc:
            target_acc = get_cvs_account(clean_q)
    else:
        # If no account specified, try finding one that has this coupon loaded
        c_lower = coupon_name.lower().strip()
        if c_lower:
            for acc in cvs_accounts_db:
                for c in acc.get("coupons", []):
                    if c_lower in str(c).lower():
                        target_acc = acc
                        break
                if target_acc:
                    break
        if not target_acc:
            target_acc = cvs_accounts_db[0]

    if not target_acc and coupon_name:
        acc_cand = get_cvs_account(coupon_name)
        if acc_cand:
            target_acc = acc_cand
            coupon_name = ""

    if not target_acc:
        return False, f"Could not find CVS account matching '{account_query}'.", None

    clean_coupon = coupon_name.strip()
    if not clean_coupon:
        if target_acc.get("coupons"):
            clean_coupon = target_acc["coupons"][0]
        else:
            return False, f"Account #{target_acc.get('id')} ({target_acc.get('name')}) has no active coupons loaded.", None

    c_lower = clean_coupon.lower()

    # 1. Remove from active coupons if present
    if "coupons" in target_acc and isinstance(target_acc["coupons"], list):
        remaining = []
        matched = False
        for c in target_acc["coupons"]:
            if not matched and (c_lower in str(c).lower() or str(c).lower() in c_lower):
                matched = True
                clean_coupon = str(c)
            else:
                remaining.append(c)
        target_acc["coupons"] = remaining

    # 2. Add to used_coupons list
    used_entry = {
        "coupon": clean_coupon,
        "savings": float(savings) if savings is not None else 0.0,
        "notes": notes,
        "used_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": datetime.now(timezone.utc).strftime("%b %d, %Y"),
        "marked_by": user_tag or "Staff"
    }
    target_acc.setdefault("used_coupons", []).append(used_entry)

    # 3. Strike through if present in notes
    if target_acc.get("notes") and clean_coupon in target_acc["notes"]:
        target_acc["notes"] = target_acc["notes"].replace(clean_coupon, f"~~{clean_coupon}~~ *(Used)*")

    save_cvs_accounts(cvs_accounts_db)
    return True, f"Successfully marked '{clean_coupon}' as used.", target_acc

def unmark_coupon_used(
    account_query: Optional[Union[int, str]] = None,
    coupon_name: str = ""
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Restores a used coupon back to active coupons on an account."""
    if not cvs_accounts_db:
        return False, "No CVS accounts in database.", None

    target_acc = None
    if isinstance(account_query, int) or (isinstance(account_query, str) and account_query.strip().isdigit()):
        aid = int(account_query)
        for acc in cvs_accounts_db:
            if acc.get("id") == aid:
                target_acc = acc
                break
    if not target_acc and isinstance(account_query, str):
        target_acc = get_cvs_account(account_query)
    if not target_acc:
        target_acc = cvs_accounts_db[0]

    c_lower = coupon_name.lower().strip()
    used_list = target_acc.get("used_coupons", [])
    restored_coupon = None
    new_used = []
    for u in used_list:
        name = u.get("coupon", "") if isinstance(u, dict) else str(u)
        if not restored_coupon and (c_lower in name.lower() or name.lower() in c_lower):
            restored_coupon = name
        else:
            new_used.append(u)

    if not restored_coupon:
        return False, f"Coupon '{coupon_name}' was not found in used coupons for Account #{target_acc['id']}.", target_acc

    target_acc["used_coupons"] = new_used
    target_acc.setdefault("coupons", []).append(restored_coupon)
    save_cvs_accounts(cvs_accounts_db)
    return True, f"Restored '{restored_coupon}' back to active coupons.", target_acc

FILTERS_FILE = "automod_filters.json"
MOD_CASES_FILE = "mod_cases.json"
MOD_NOTES_FILE = "mod_notes.json"
AUTOMOD_CONFIG_FILE = "automod_config.json"
VOUCHES_FILE = "vouches.json"
GIVEAWAYS_FILE = "giveaways.json"

filters_db: Dict[str, List[str]] = load_json_file(FILTERS_FILE, {})
mod_cases_db: Dict[str, Any] = load_json_file(MOD_CASES_FILE, {"next_id": 1, "cases": []})
mod_notes_db: Dict[str, Dict[str, List[Dict[str, Any]]]] = load_json_file(MOD_NOTES_FILE, {})
automod_config_db: Dict[str, Any] = load_json_file(AUTOMOD_CONFIG_FILE, {"invites_blocked": True, "scams_blocked": True})
vouches_db: Dict[str, Any] = load_json_file(VOUCHES_FILE, {"vouches": []})
giveaways_db: Dict[str, Any] = load_json_file(GIVEAWAYS_FILE, {})

def save_automod_config(data: Optional[Dict[str, Any]] = None) -> None:
    global automod_config_db
    if data is not None:
        automod_config_db = data
    save_json_file(AUTOMOD_CONFIG_FILE, automod_config_db)

def save_vouches(data: Optional[Dict[str, Any]] = None) -> None:
    global vouches_db
    if data is not None:
        vouches_db = data
    save_json_file(VOUCHES_FILE, vouches_db)

def save_giveaways(data: Optional[Dict[str, Any]] = None) -> None:
    global giveaways_db
    if data is not None:
        giveaways_db = data
    save_json_file(GIVEAWAYS_FILE, giveaways_db)

# --- STRICT CHANNEL PROTECTION GUARDRAIL ---
def is_protected_channel(channel: Any) -> bool:
    """Returns True if the channel or its category is strictly protected (#form-automation)
    and must NEVER be touched, edited, nuked, moved, or deleted under any circumstance.
    """
    if channel is None:
        return False
    if isinstance(channel, str):
        clean_name = channel.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
        return "formautomation" in clean_name or "ownervault" in clean_name or "privatevault" in clean_name

    name = getattr(channel, "name", "")
    if isinstance(name, str):
        clean_name = name.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
        if "formautomation" in clean_name or "ownervault" in clean_name or "privatevault" in clean_name:
            return True

    # Also check parent category if applicable
    parent_cat = getattr(channel, "category", None)
    if parent_cat is not None and parent_cat is not channel:
        cat_name = getattr(parent_cat, "name", "")
        if isinstance(cat_name, str):
            clean_cat = cat_name.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
            if "formautomation" in clean_cat:
                return True

    return False

def is_coupon_optimizer_channel(channel: Any) -> bool:
    """Checks if a channel or channel name is a CVS/retail coupon optimizer hub or private shopping cart room."""
    if channel is None:
        return False
    if isinstance(channel, str):
        clean_name = channel.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
        if "couponoptimizer" in clean_name or "cvsoptimizer" in clean_name:
            return True
        if clean_name.startswith("cart") or "shoppingcart" in clean_name:
            return True
        if "coupon" in clean_name and any(k in clean_name for k in ("room", "hub", "opt", "cart")):
            return True
        return False

    name = getattr(channel, "name", "")
    if isinstance(name, str):
        clean_name = name.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
        if "couponoptimizer" in clean_name or "cvsoptimizer" in clean_name:
            return True
        if clean_name.startswith("cart") or "shoppingcart" in clean_name:
            return True
        if "coupon" in clean_name and any(k in clean_name for k in ("room", "hub", "opt", "cart")):
            return True

    # Check parent category
    parent_cat = getattr(channel, "category", None)
    if parent_cat is not None and parent_cat is not channel:
        cat_name = getattr(parent_cat, "name", "")
        if isinstance(cat_name, str):
            clean_cat = cat_name.lower().replace("-", "").replace("_", "").replace(" ", "").replace("#", "")
            if "privatecvs" in clean_cat or "couponoptimizer" in clean_cat or "cvsoptimizer" in clean_cat:
                return True

    # Check channel topic
    topic = getattr(channel, "topic", "")
    if isinstance(topic, str) and topic:
        t_lower = topic.lower()
        if any(k in t_lower for k in ("coupon optimizer", "private coupon", "optimizer room", "cvs & retail coupon")):
            return True

    # Check session_channels dictionary
    ch_id = getattr(channel, "id", None)
    if ch_id:
        try:
            if int(ch_id) in [int(v) for v in session_channels.values()]:
                return True
        except (ValueError, TypeError):
            pass

    return False

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
    if getattr(member, "id", None) in (560578688534577237, getattr(bot, "owner_id", None)):
        return True
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

CVS_ALLOWED_GUILD_IDS: Set[int] = {731326405937201183, 1514110480346513470}
_extra_cvs_env = os.environ.get("CVS_ALLOWED_GUILDS", "")
if _extra_cvs_env:
    for _gid in _extra_cvs_env.split(","):
        _gid = _gid.strip()
        if _gid.isdigit():
            CVS_ALLOWED_GUILD_IDS.add(int(_gid))

def is_cvs_guild(guild_or_id: Any) -> bool:
    """Returns True if the guild is an authorized CVS / Coupon Optimizer server."""
    if guild_or_id is None:
        return False
    gid = getattr(guild_or_id, "id", guild_or_id)
    return gid in CVS_ALLOWED_GUILD_IDS

CVS_COMMAND_NAMES: Set[str] = {
    "accounts", "stock", "organizecoupons", "used", "unusecoupon",
    "optimize", "calc", "cart", "checkout", "additem", "add", "remove", "undo", "clear",
    "coupons", "deals", "finddeals", "savings", "history", "trips", "delete-last-trip",
    "massdm", "tacobell", "foodpanel", "shop", "invoice", "addorder", "orderstats",
    "clearorder", "paid", "deliver", "complete", "setup-food-store", "setup-vault",
    "setup-all-features", "formatserver", "deletechannels", "resetchannel",
    "setup-rules", "setup-welcome", "setup-status-channel", "setup-giveaways", "setup-announcements",
    "balance", "pay", "daily", "leaderboard", "slots", "blackjack", "rps", "connect4", "trivia", "case",
    "otp", "vouch", "testwelcome", "run-stress-test", "permit", "revoke", "giveaway", "giverole", "removerole", "role", "note"
}

_orig_tree_add_command = bot.tree.add_command

def _scoped_tree_add_command(command, /, *, guild=discord.utils.MISSING, override=False):
    if guild is discord.utils.MISSING and command.name in CVS_COMMAND_NAMES:
        for gid in CVS_ALLOWED_GUILD_IDS:
            _orig_tree_add_command(command, guild=discord.Object(id=gid), override=override)
        return command
    return _orig_tree_add_command(command, guild=guild, override=override)

bot.tree.add_command = _scoped_tree_add_command

_orig_bot_add_command = bot.add_command

def _scoped_bot_add_command(command, /):
    if command.name in CVS_COMMAND_NAMES:
        async def _cvs_guild_check(ctx: commands.Context) -> bool:
            return bool(ctx.guild and is_cvs_guild(ctx.guild))
        command.add_check(_cvs_guild_check)
    return _orig_bot_add_command(command)

bot.add_command = _scoped_bot_add_command

def is_primary_bot_owner(user: Any) -> bool:
    """Checks strictly if the user is Cody (the bot creator) or bot.owner_id."""
    if user is None:
        return False
    uid = getattr(user, "id", None)
    return uid in (560578688534577237, getattr(bot, "owner_id", None))

async def is_bot_owner_safe(user: Any) -> bool:
    """Safely checks if user is bot owner without throwing unhandled exceptions if bot HTTP is uninitialized."""
    if is_primary_bot_owner(user):
        return True
    try:
        return await bot.is_owner(user)
    except Exception:
        return False

def is_bot_or_server_owner(user: Any, guild: Optional[discord.Guild] = None) -> bool:
    """Checks if a user is the bot creator (Cody) or the owner of an authorized server."""
    if user is None:
        return False
    if is_primary_bot_owner(user):
        return True
    g = guild or getattr(user, "guild", None)
    uid = getattr(user, "id", None)
    if g and is_cvs_guild(g) and getattr(g, "owner_id", None) == uid:
        return True
    return False

async def is_owner_only(ctx_or_user: Any, guild: Optional[discord.Guild] = None) -> bool:
    """Async check verifying user is strictly Cody (the bot creator) or server owner in an authorized CVS server."""
    if ctx_or_user is None:
        return False
    if is_primary_bot_owner(ctx_or_user):
        return True
    user = getattr(ctx_or_user, "author", None) or getattr(ctx_or_user, "user", None)
    if user and is_primary_bot_owner(user):
        return True
    g = guild or getattr(ctx_or_user, "guild", None)
    target_user = user or ctx_or_user
    if g and is_cvs_guild(g) and getattr(g, "owner_id", None) == getattr(target_user, "id", None):
        return True
    try:
        if await bot.is_owner(target_user):
            return True
    except Exception:
        pass
    return False

def resolve_member_from_input(guild: Optional[discord.Guild], query: str) -> Optional[discord.Member]:
    """Resolves a guild member from mention (<@123>), user ID (123), or username / nickname."""
    if not guild or not query:
        return None
    cleaned = query.strip().lstrip("<@!&").rstrip(">")
    if cleaned.isdigit():
        mem = guild.get_member(int(cleaned))
        if mem:
            return mem
    q_lower = query.strip().lower().lstrip("@")
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
    cleaned = query.strip().lstrip("<@!&").rstrip(">").strip()
    if cleaned.isdigit():
        uid = int(cleaned)
        cached = bot.get_user(uid)
        if cached:
            return cached
        try:
            return await bot.fetch_user(uid)
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
    
    existing_ids = [o.get("order_id", 0) for o in tickets_db["completed_orders"] if isinstance(o.get("order_id"), int)]
    order_num = (max(existing_ids) + 1) if existing_ids else (len(tickets_db["completed_orders"]) + 1)
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

def record_manual_order(
    guild_id: int,
    customer_input: str,
    price: float,
    item: str,
    completed_by_id: int,
    completed_by_name: str,
    notes: Optional[str] = None,
    guild: Optional[discord.Guild] = None
) -> Dict[str, Any]:
    cust_mem = resolve_member_from_input(guild, customer_input) if guild else None
    if cust_mem:
        customer_id = cust_mem.id
        customer_name = str(cust_mem)
    else:
        clean_input = customer_input.strip().lstrip("<@!").rstrip(">")
        if clean_input.isdigit():
            customer_id = int(clean_input)
            customer_name = f"User-{clean_input}"
        else:
            customer_id = 0
            customer_name = customer_input.strip()

    return record_completed_order(
        guild_id=guild_id,
        ticket_id=0,
        channel_id=0,
        channel_name="manual-entry",
        customer_id=customer_id,
        customer_name=customer_name,
        completed_by_id=completed_by_id,
        completed_by_name=completed_by_name,
        brand=item.strip() or "Custom Order",
        amount=price,
        notes=notes
    )

def remove_completed_order(order_or_ticket_id: int, guild_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    orders = tickets_db.get("completed_orders", [])
    # 1. Primary pass: match exact order_id
    for idx, order in enumerate(orders):
        if guild_id and order.get("guild_id") and order.get("guild_id") != guild_id:
            continue
        if order.get("order_id") == order_or_ticket_id:
            removed = orders.pop(idx)
            save_tickets()
            return removed

    # 2. Fallback pass: match ticket_id (if non-zero)
    if order_or_ticket_id > 0:
        for idx, order in enumerate(orders):
            if guild_id and order.get("guild_id") and order.get("guild_id") != guild_id:
                continue
            if order.get("ticket_id") == order_or_ticket_id:
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
    other_count = total_count - taco_count

    gname = guild.name if guild else "AIO Bot"
    embed = discord.Embed(
        title="📊 Completed Orders & Sales Tracker",
        description=f"Order fulfillment and revenue analytics for **{gname}**.",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Orders Fulfilled", value=f"**{total_count}** orders", inline=True)
    embed.add_field(name="Gross Revenue", value=f"**${total_rev:.2f}**", inline=True)
    dist_parts = [f"🌮 Taco Bell: **{taco_count}**"]
    if other_count > 0:
        dist_parts.append(f"Other: **{other_count}**")
    embed.add_field(
        name="Brand Distribution",
        value=" • ".join(dist_parts),
        inline=True
    )
    vstats = get_vouch_stats(guild.id if guild else None)
    embed.add_field(
        name="Customer Reviews",
        value=f"**{vstats['total']}** verified reviews • {vstats['stars_str']} (**{vstats['average']}/5.0**)",
        inline=False
    )

    if guild_orders:
        recent = guild_orders[-8:]
        lines = []
        for o in reversed(recent):
            oid = o.get("order_id", o.get("ticket_id", "?"))
            oid_str = f"#{oid:02d}" if isinstance(oid, int) else f"#{oid}"
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
            lines.append(f"• `{oid_str}` **{brand}** — **${amt:.2f}** • {cust}{ts}")
        embed.add_field(name="Recent Orders", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Recent Orders", value="*No completed orders tracked yet.*", inline=False)

    embed.set_footer(text="AIO Sales Tracker • Use /addorder to manually log an order")
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
        else:
            item_desc = "Taco Bell Preloaded Account(s)"

    ch_id_str = str(getattr(channel, "id", 0))
    if ch_id_str in tickets_db.get("tickets", {}):
        tickets_db["tickets"][ch_id_str]["invoice_amount"] = val
        tickets_db["tickets"][ch_id_str]["invoice_item"] = item_desc
        tickets_db["tickets"][ch_id_str]["status"] = "invoiced"
        save_tickets()

    embed = discord.Embed(
        title="🧾 Payment Invoice",
        description=(
            f"Payment requested for {target_cust.mention if target_cust else 'this order'}.\n"
            "Please send payment via Cash App or Venmo below."
        ),
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Amount Due", value=f"**{price_formatted}**", inline=True)
    embed.add_field(name="Item", value=f"**{item_desc}**", inline=True)
    if ticket_id:
        embed.add_field(name="Ticket Reference", value=f"`#{ticket_id:04d}`", inline=True)

    pay_methods = []
    if ca_handle:
        pay_methods.append(f"• 🟢 **Cash App:** [${ca_handle}](https://cash.app/${ca_handle}) (`${ca_handle}`)")
    if vm_handle:
        pay_methods.append(f"• 🔵 **Venmo:** [@{vm_handle}](https://venmo.com/u/{vm_handle}) (`@{vm_handle}`)")
    if not pay_methods:
        pay_methods.append("• *Contact staff in this channel for payment details.*")
    embed.add_field(name="Payment Handles", value="\n".join(pay_methods), inline=False)

    instructions = (
        f"1. Transfer exactly **{price_formatted}** to a handle above.\n"
        "2. Add your username or ticket ID in the memo note.\n"
        "3. Send a message or screenshot here once paid to receive your order!"
    )
    embed.add_field(name="Next Steps", value=instructions, inline=False)
    embed.add_field(name="Status", value="`🟡 Awaiting Payment`", inline=True)
    author_name = getattr(author, "display_name", str(author))
    embed.set_footer(text=f"AIO Bot • Issued by {author_name}")

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

def get_ticket_logs_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Finds existing ticket logs channel in the guild."""
    for ch in guild.text_channels:
        clean = ch.name.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "ticketlog" in clean or "transcript" in clean:
            return ch
    return None

def get_vouches_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Finds existing vouches/reviews channel in the guild (with fallback to receipt-brags)."""
    for ch in getattr(guild, "text_channels", []):
        clean = ch.name.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "vouch" in clean or "review" in clean:
            return ch
    for ch in getattr(guild, "text_channels", []):
        clean = ch.name.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "receiptbrag" in clean or "receipt" in clean:
            return ch
    return None

def get_giveaways_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Finds existing giveaways channel in the guild."""
    for ch in getattr(guild, "text_channels", []):
        clean = ch.name.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "giveaway" in clean:
            return ch
    return None

def get_welcome_channel(guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
    """Finds existing welcome channel in the guild."""
    if not guild:
        return None
    for ch in getattr(guild, "text_channels", []):
        clean = ch.name.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "welcome" in clean or "joins" in clean or "join" in clean or "arrivals" in clean:
            return ch
    return None

def build_welcome_embed(member: discord.Member) -> discord.Embed:
    guild = member.guild
    created_ts = int(member.created_at.timestamp())
    embed = discord.Embed(
        title=f"👋 Welcome to {guild.name}!",
        description=f"Welcome {member.mention}! We're thrilled to have you here in our community.",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    if hasattr(member, "display_avatar") and member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    elif hasattr(member, "avatar") and member.avatar:
        embed.set_thumbnail(url=member.avatar.url)

    embed.add_field(name="Member", value=f"{member.mention} (`{member.name}`)", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{created_ts}:D> (<t:{created_ts}:R>)", inline=True)
    embed.add_field(name="Member Count", value=f"**#{getattr(guild, 'member_count', 1):,}**", inline=True)

    links = []
    for ch in getattr(guild, "text_channels", []):
        cname = ch.name.lower()
        if "rule" in cname:
            links.append(f"• Rules: {ch.mention}")
        elif "coupon" in cname or "optimizer" in cname:
            links.append(f"• Coupon Optimizer: {ch.mention}")
        elif "ticket" in cname:
            links.append(f"• Support Tickets: {ch.mention}")
        elif "food" in cname or "reward" in cname:
            links.append(f"• Food Rewards: {ch.mention}")
        elif "giveaway" in cname:
            links.append(f"• Giveaways: {ch.mention}")
        if len(links) >= 3:
            break

    if links:
        embed.add_field(name="Getting Started", value="\n".join(links), inline=False)

    icon_url = guild.icon.url if getattr(guild, "icon", None) else None
    embed.set_footer(text=f"AIO Bot • Member #{getattr(guild, 'member_count', 1):,}", icon_url=icon_url)
    return embed


def is_read_only_channel_name(name: Optional[str]) -> bool:
    """Returns True if the channel name corresponds to an announcement, rules, welcome, or other read-only board channel."""
    if not name:
        return False
    n = name.lower()
    return any(k in n for k in (
        "announcement", "rule", "welcome", "giveaway",
        "shop-open", "shop-closed", "shop-status",
        "food-rewards", "rewards-store",
        "open-a-ticket", "coupon-optimizer"
    ))


async def apply_read_only_overwrites(
    channel: discord.TextChannel,
    founder_role: Optional[discord.Role] = None,
    mod_role: Optional[discord.Role] = None
) -> bool:
    """
    Enforces read-only permissions on a channel:
    - General members (@everyone) CANNOT type, cannot send threads, cannot create threads.
    - General members CAN view and add reactions.
    - Server staff, founders, administrators, and the bot CAN send messages, embeds, and attachments.
    """
    if not channel or not getattr(channel, "guild", None):
        return False
    guild = channel.guild
    founder = founder_role or get_founder_role(guild)
    mod = mod_role or get_moderator_role(guild)
    try:
        # Default role (@everyone): viewable, but strictly NO sending messages or threads
        await channel.set_permissions(
            guild.default_role,
            view_channel=True,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            add_reactions=True,
            read_message_history=True,
            reason="Enforcing read-only channel permissions for general members"
        )
        # Bot permissions
        if guild.me:
            await channel.set_permissions(
                guild.me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True,
                reason="Enforcing bot permissions in read-only channel"
            )
        # Staff / Founder roles
        if founder:
            await channel.set_permissions(
                founder,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                reason="Enforcing founder send permissions in read-only channel"
            )
        if mod:
            await channel.set_permissions(
                mod,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                reason="Enforcing moderator send permissions in read-only channel"
            )
        return True
    except Exception as e:
        print(f"⚠️ Could not set read-only permissions on #{getattr(channel, 'name', 'unknown')}: {e}", file=sys.stderr)
        return False


async def audit_and_enforce_read_only_channels(guild: discord.Guild) -> List[str]:
    """Audits all channels in guild and locks down announcements, rules, welcome, giveaways, etc. so members cannot type."""
    updated = []
    if not guild:
        return updated
    founder = get_founder_role(guild)
    mod = get_moderator_role(guild)
    for ch in getattr(guild, "text_channels", []):
        if is_protected_channel(ch):
            continue
        cname = getattr(ch, "name", "").lower()
        if is_read_only_channel_name(cname):
            perms = ch.overwrites_for(guild.default_role)
            if perms.send_messages is not False or perms.send_messages_in_threads is not False:
                ok = await apply_read_only_overwrites(ch, founder, mod)
                if ok:
                    updated.append(ch.name)
    return updated


async def setup_welcome_channel(guild: discord.Guild) -> Tuple[Optional[discord.TextChannel], bool]:
    """Creates or locates a dedicated #👋-welcome channel for new member join announcements with read-only permissions."""
    existing = get_welcome_channel(guild)
    if existing:
        await apply_read_only_overwrites(existing)
        return existing, False

    cat = (
        discord.utils.get(guild.categories, name="📌 INFORMATION") or
        discord.utils.get(guild.categories, name="💬 COMMUNITY")
    )
    if not cat:
        try:
            cat = await guild.create_category("📌 INFORMATION")
        except Exception:
            cat = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            add_reactions=True,
            read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True
        )
    }
    founder_r = get_founder_role(guild)
    mod_r = get_moderator_role(guild)
    if founder_r:
        overwrites[founder_r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)
    if mod_r:
        overwrites[mod_r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)

    try:
        new_ch = await guild.create_text_channel(
            "👋-welcome",
            category=cat,
            topic="Welcome new members to the server!",
            overwrites=overwrites
        )
        await apply_read_only_overwrites(new_ch, founder_r, mod_r)
        return new_ch, True
    except Exception:
        return None, False


async def setup_giveaways_channel(guild: discord.Guild) -> Tuple[Optional[discord.TextChannel], bool]:
    """Creates or configures a dedicated #🎉-giveaways channel with read-only permissions for members."""
    existing = get_giveaways_channel(guild)
    if existing:
        await apply_read_only_overwrites(existing)
        return existing, False

    cat = (
        discord.utils.get(guild.categories, name="💬 COMMUNITY") or
        discord.utils.get(guild.categories, name="🛍️ SAVINGS & REWARDS") or
        discord.utils.get(guild.categories, name="🎉 GIVEAWAYS")
    )
    if not cat:
        try:
            cat = await guild.create_category("💬 COMMUNITY")
        except Exception:
            cat = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            add_reactions=True,
            read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
            manage_channels=True
        )
    }
    founder_r = get_founder_role(guild)
    mod_r = get_moderator_role(guild)
    if founder_r:
        overwrites[founder_r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)
    if mod_r:
        overwrites[mod_r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)

    try:
        new_ch = await guild.create_text_channel(
            "🎉-giveaways",
            category=cat,
            topic="Official server giveaways and rewards! Enter active drops below.",
            overwrites=overwrites
        )
        await apply_read_only_overwrites(new_ch, founder_r, mod_r)
        return new_ch, True
    except Exception as e:
        print(f"⚠️ Error creating giveaways channel: {e}", file=sys.stderr)
        return None, False

    welcome_embed = discord.Embed(
        title="🎉 Official Server Giveaways Hub",
        description=(
            "Welcome to the official giveaways channel! 🎁\n\n"
            "• **How to Enter:** When a giveaway is active, simply click the **Enter** button on the giveaway embed.\n"
            "• **Requirements:** Some exclusive drops may require purchase history (Customer Only) or specific roles.\n"
            "• **Winners:** Drawn automatically by the bot at timer expiration.\n\n"
            "*Turn on notifications for this channel so you never miss a drop!*"
        ),
        color=0xF1C40F
    )
    welcome_embed.set_footer(text="AIO Bot Giveaway System")
    try:
        await new_ch.send(embed=welcome_embed)
    except Exception:
        pass

    return new_ch, True

def get_owner_vault_channel(guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
    """Finds existing strictly private owner vault channel in the guild."""
    if not guild:
        return None
    for ch in guild.text_channels:
        if ch.name in ("🔒-owner-vault", "🔒-my-accounts", "owner-vault", "private-vault"):
            return ch
    return None

async def setup_owner_vault_channel(guild: discord.Guild) -> Tuple[Optional[discord.TextChannel], bool]:
    """Creates or configures a strictly private #🔒-owner-vault channel restricted exclusively to the owner and bot."""
    existing = get_owner_vault_channel(guild)
    if existing:
        return existing, False

    cat = (
        discord.utils.get(guild.categories, name="Private") or
        discord.utils.get(guild.categories, name="🔒 PRIVATE CVS") or
        discord.utils.get(guild.categories, name="🛡️ STAFF ZONE")
    )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
            read_message_history=True
        )
    }

    owner_member = None
    if getattr(guild, "owner", None):
        owner_member = guild.owner
    elif getattr(guild, "owner_id", None):
        owner_member = guild.get_member(guild.owner_id)

    if not owner_member and getattr(bot, "owner_id", None):
        owner_member = guild.get_member(bot.owner_id)

    if not owner_member:
        owner_member = guild.get_member(560578688534577237)

    if owner_member:
        overwrites[owner_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True
        )

    try:
        new_ch = await guild.create_text_channel(
            "🔒-owner-vault",
            category=cat,
            topic="Private Owner Vault • Personal CVS ExtraCare accounts management. Restricted exclusively to Cody.",
            overwrites=overwrites
        )
    except Exception as e:
        print(f"⚠️ Error creating owner vault channel: {e}", file=sys.stderr)
        return None, False

    welcome_embed = discord.Embed(
        title="🔒 Private Owner Vault • Personal Accounts",
        description=(
            "Welcome to your private account hub! 🛡️\n\n"
            "This channel is **strictly private** and only visible to **you** and **AIO Bot**.\n"
            "All commands displaying or managing personal CVS accounts are locked down so that **only you** can execute them.\n\n"
            "**Available Owner Commands:**\n"
            "• `/stock` — Browse active coupons, cardholder names, phones, & 1-click Mark Used\n"
            "• `/accounts` — Card barcode viewer & ExtraCare details with pagination\n"
            "• `/organizecoupons` — Group accounts by coupon type ($4 off, $3 off, 40% off)\n"
            "• `/used` — Manually mark coupons as used by name or phone\n\n"
            "🔐 *Non-owners and server members cannot view or execute these commands anywhere on the server.*"
        ),
        color=0x2B2D31
    )
    welcome_embed.set_footer(text="AIO Bot • Private Owner Security Protocol")
    try:
        await new_ch.send(embed=welcome_embed)
    except Exception:
        pass

    return new_ch, True

def add_vouch(
    guild_id: int,
    user_id: int,
    user_name: str,
    rating: int,
    comment: str,
    staff_id: Optional[int] = None,
    proof_url: Optional[str] = None
) -> Dict[str, Any]:
    rating = max(1, min(5, rating))
    vouch_entry = {
        "id": len(vouches_db.get("vouches", [])) + 1,
        "guild_id": guild_id,
        "user_id": user_id,
        "user_name": user_name,
        "staff_id": staff_id,
        "rating": rating,
        "comment": comment.strip(),
        "proof_url": proof_url,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ts_unix": int(datetime.now(timezone.utc).timestamp())
    }
    vouches_db.setdefault("vouches", []).append(vouch_entry)
    save_vouches()
    return vouch_entry

def get_vouch_stats(guild_id: Optional[int] = None) -> Dict[str, Any]:
    all_v = vouches_db.get("vouches", [])
    if guild_id:
        v_list = [v for v in all_v if v.get("guild_id") == guild_id or not v.get("guild_id")]
    else:
        v_list = all_v
    count = len(v_list)
    if count == 0:
        return {"total": 0, "average": 0.0, "stars_str": "☆☆☆☆☆", "breakdown": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}}
    avg = sum(v.get("rating", 5) for v in v_list) / count
    bd = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for v in v_list:
        r = v.get("rating", 5)
        if r in bd:
            bd[r] += 1
    full_stars = int(round(avg))
    stars_str = "⭐" * full_stars + "☆" * (5 - full_stars)
    return {"total": count, "average": round(avg, 2), "stars_str": stars_str, "breakdown": bd}

def build_vouch_embed(vouch: Dict[str, Any], user: Optional[Union[discord.User, discord.Member]] = None) -> discord.Embed:
    rating = vouch.get("rating", 5)
    stars = "⭐" * rating
    embed = discord.Embed(
        title=f"{stars} ({rating}/5 Stars)",
        description=f"\"{vouch.get('comment', 'Great service!')}\"",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc)
    )
    author_tag = f"<@{vouch.get('user_id')}>"
    embed.add_field(name="Customer", value=author_tag, inline=True)
    if vouch.get("staff_id"):
        embed.add_field(name="Staff Member", value=f"<@{vouch.get('staff_id')}>", inline=True)
    embed.add_field(name="Review ID", value=f"`#{vouch.get('id', 1):03d}`", inline=True)
    if vouch.get("proof_url"):
        embed.set_image(url=vouch["proof_url"])
    if user and hasattr(user, "display_avatar"):
        embed.set_author(name=f"Vouch from {getattr(user, 'display_name', str(user))}", icon_url=user.display_avatar.url)
    embed.set_footer(text="AIO Customer Reviews")
    return embed

def parse_giveaway_duration(duration_str: str) -> Optional[int]:
    """Parses strings like '10s', '5m', '2h', '1d', '3days' into total seconds."""
    if not duration_str:
        return None
    s = duration_str.strip().lower()
    total_seconds = 0
    matches = re.findall(r'(\d+)\s*([smhdw]|sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours|d|day|days|w|week|weeks)', s)
    if not matches:
        if s.isdigit():
            return int(s) * 60
        return None
    for val, unit in matches:
        v = int(val)
        u = unit.lower()
        if u.startswith('s'):
            total_seconds += v
        elif u.startswith('m'):
            total_seconds += v * 60
        elif u.startswith('h'):
            total_seconds += v * 3600
        elif u.startswith('d'):
            total_seconds += v * 86400
        elif u.startswith('w'):
            total_seconds += v * 604800
    return total_seconds if total_seconds > 0 else None

def check_giveaway_eligibility(giveaway: Dict[str, Any], member: Union[discord.Member, discord.User]) -> Tuple[bool, str]:
    """Checks if member meets requirements (customer_only, required_role) to enter giveaway."""
    if giveaway.get("customer_only"):
        completed = tickets_db.get("completed_orders", [])
        is_order_customer = any(o.get("customer_id") == member.id for o in completed)
        roles = getattr(member, "roles", [])
        has_cust_role = any("customer" in r.name.lower() or "buyer" in r.name.lower() for r in roles)
        if not (is_order_customer or has_cust_role):
            return False, "⛔ **Customer Exclusive:** This giveaway is reserved for verified customers who have completed a purchase."

    req_role_id = giveaway.get("required_role_id")
    if req_role_id:
        roles = getattr(member, "roles", [])
        if req_role_id not in [r.id for r in roles]:
            return False, f"⛔ **Role Required:** You must have the <@&{req_role_id}> role to enter this giveaway."

    return True, ""

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
        title=f"💳 CVS ExtraCare • #{acc_id} {name}",
        description=(
            f"🎯 **[Open Deals & Rewards (Send to Card)]({coupon_link})** • 🎟️ **[Digital Coupons]({deals_link})** • 💰 **[Dashboard]({extracare_link})**\n"
            "Scannable barcode generated below for register & self-checkout."
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
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
        embed.add_field(name="🔐 Account Credentials", value=val, inline=False)

    active_coupons = acc.get("coupons", [])
    if active_coupons:
        c_lines = "\n".join(f"• **{c}**" for c in active_coupons)
        embed.add_field(name=f"🎟️ Active Coupons ({len(active_coupons)})", value=c_lines[:1024], inline=False)

    used_coupons = acc.get("used_coupons", [])
    if used_coupons:
        u_lines = []
        for u in used_coupons[-6:]:
            if isinstance(u, dict):
                cname = u.get("coupon", "Coupon")
                dt = u.get("date", "")
                sav = u.get("savings", 0.0)
                sav_str = f" (${sav:.2f})" if sav > 0 else ""
                u_lines.append(f"• ~~{cname}~~{sav_str}" + (f" *({dt})*" if dt else ""))
            else:
                u_lines.append(f"• ~~{u}~~")
        embed.add_field(name=f"✅ Used / Redeemed Coupons ({len(used_coupons)})", value="\n".join(u_lines)[:1024], inline=False)

    if acc.get("notes"):
        embed.add_field(name="📝 Account Notes", value=acc['notes'][:1000], inline=False)

    embed.set_image(url="attachment://cvs_barcode.png")
    embed.set_footer(text=f"AIO Bot • Card #{acc_id} of {len(cvs_accounts_db)}")
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
        if await is_owner_only(interaction.user, interaction.guild):
            return True
        await interaction.response.send_message("⛔ Security Error: Only the bot owner can view private CVS accounts.", ephemeral=True)
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

    @discord.ui.button(label="Use Coupon", style=discord.ButtonStyle.success, emoji="🏷️", row=1)
    async def use_coupon_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not cvs_accounts_db:
            await interaction.response.send_message("No accounts found!", ephemeral=True)
            return
        acc = cvs_accounts_db[self.current_idx]
        await interaction.response.send_modal(MarkCouponUsedModal(account_id=acc["id"], parent_view=self))

    @discord.ui.button(label="Add Coupon", style=discord.ButtonStyle.secondary, emoji="➕", row=1)
    async def add_coupon_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not cvs_accounts_db:
            await interaction.response.send_message("No accounts found!", ephemeral=True)
            return
        acc = cvs_accounts_db[self.current_idx]
        await interaction.response.send_modal(AddCouponModal(account_id=acc["id"], parent_view=self))

    @discord.ui.button(label="Custom Barcode", style=discord.ButtonStyle.secondary, emoji="💳", row=1)
    async def custom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CVSAccountModal())


def group_accounts_by_coupon() -> Dict[str, List[Dict[str, Any]]]:
    """Groups all CVS accounts by the active coupons they currently have on them."""
    grouped = {}
    no_coupons = []

    for acc in cvs_accounts_db:
        coupons = acc.get("coupons", [])
        if not coupons:
            no_coupons.append(acc)
        else:
            for c in coupons:
                c_clean = str(c).strip()
                grouped.setdefault(c_clean, []).append(acc)

    # Sort groups by count descending
    sorted_groups = dict(sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True))
    if no_coupons:
        sorted_groups["📭 No Active Coupons"] = no_coupons

    return sorted_groups


def build_coupon_organizer_embed(category_filter: Optional[str] = None) -> discord.Embed:
    grouped = group_accounts_by_coupon()
    total_accs = len(cvs_accounts_db)
    accs_with_coupons = sum(1 for a in cvs_accounts_db if a.get("coupons"))

    if not category_filter or category_filter.lower() in ("all", "overview"):
        embed = discord.Embed(
            title="🎟️ CVS Accounts Organized by Active Coupons",
            description=(
                f"> **Total Accounts:** `{total_accs}` ⏐ **With Active Coupons:** `{accs_with_coupons}` ⏐ **Without Coupons:** `{total_accs - accs_with_coupons}`\n\n"
                "Browse accounts organized by their active coupon below. Select a category from the dropdown to view full account details."
            ),
            color=COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

        for coupon_name, acc_list in grouped.items():
            is_none = "no active coupons" in coupon_name.lower()
            icon = "📭" if is_none else "🎟️"
            lines = []
            for a in acc_list:
                phone_raw = a.get('phone', '')
                phone_fmt = f"({phone_raw[:3]}) {phone_raw[3:6]}-{phone_raw[6:]}" if len(phone_raw) == 10 else phone_raw
                card = str(a.get('extraCareNumber', ''))
                last4 = card[-4:] if len(card) >= 4 else card
                lines.append(f"`#{a['id']:02d}` **{a.get('name', 'Account')}** ⏐ 📞 `{phone_fmt}` ⏐ Card ends `{last4}`")

            val_text = "\n".join(lines)
            if len(val_text) > 1000:
                val_text = val_text[:980] + f"\n*...and {len(lines) - val_text[:980].count(chr(10))} more*"

            embed.add_field(
                name=f"{icon} {coupon_name} ({len(acc_list)} accounts)",
                value=val_text or "None",
                inline=False
            )

        embed.set_footer(text="AIO Bot • Use /organizecoupons <category> or choose from dropdown")
        return embed
    else:
        q = category_filter.lower().strip()
        matched_category = None
        for cat in grouped.keys():
            if q in cat.lower() or cat.lower() in q:
                matched_category = cat
                break
        if not matched_category:
            matched_category = list(grouped.keys())[0] if grouped else "All"

        acc_list = grouped.get(matched_category, [])
        is_none = "no active coupons" in matched_category.lower()
        icon = "📭" if is_none else "🎟️"

        embed = discord.Embed(
            title=f"{icon} {matched_category}",
            description=f"> Showing **{len(acc_list)}** account(s) matching this category.\nUse `/accounts id:<number>` to view scannable barcodes.",
            color=COLOR_SUCCESS if not is_none else COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

        for a in acc_list[:25]:
            phone_raw = a.get('phone', '')
            phone_fmt = f"({phone_raw[:3]}) {phone_raw[3:6]}-{phone_raw[6:]}" if len(phone_raw) == 10 else phone_raw
            card = str(a.get('extraCareNumber', ''))
            formatted_card = " ".join([card[i:i+4] for i in range(0, len(card), 4)])

            val = f"📞 **Phone:** `{phone_fmt}`\n🔢 **Card:** `{formatted_card}` (ends `{card[-4:]}`)\n📧 **Email:** `{a.get('email', '')}`"
            if a.get('coupon_link'):
                val += f"\n🔗 **[1-Click Send to Card]({a['coupon_link']})**"

            embed.add_field(
                name=f"#{a['id']:02d} • {a.get('name', 'Account')}",
                value=val,
                inline=True
            )

        embed.set_footer(text=f"AIO Bot • Showing {len(acc_list)} accounts in category")
        return embed


class CouponCategorySelect(discord.ui.Select):
    def __init__(self, current_selection: str = "all"):
        grouped = group_accounts_by_coupon()
        options = [
            discord.SelectOption(
                label="Overview (All Categories)",
                value="all",
                description="View all coupon categories and summaries",
                emoji="📋",
                default=(current_selection == "all")
            )
        ]
        for cat, accs in list(grouped.items())[:24]:
            is_none = "no active coupons" in cat.lower()
            emoji = "📭" if is_none else "🎟️"
            clean_label = cat[:95]
            desc = f"{len(accs)} accounts"
            options.append(
                discord.SelectOption(
                    label=clean_label,
                    value=clean_label[:90],
                    description=desc,
                    emoji=emoji,
                    default=(current_selection.lower() in clean_label.lower())
                )
            )
        super().__init__(placeholder="📂 Filter by Coupon Category...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        sel = self.values[0]
        embed = build_coupon_organizer_embed(sel)
        self.view.update_select(sel)
        await interaction.response.edit_message(embed=embed, view=self.view)


class CouponOrganizerView(discord.ui.View):
    def __init__(self, selected_category: Optional[str] = "all"):
        super().__init__(timeout=180)
        self.selected_category = selected_category or "all"
        self.dropdown = CouponCategorySelect(self.selected_category)
        self.add_item(self.dropdown)

    def update_select(self, new_selection: str):
        self.remove_item(self.dropdown)
        self.dropdown = CouponCategorySelect(new_selection)
        self.add_item(self.dropdown)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_owner_only(interaction.user, interaction.guild):
            return True
        await interaction.response.send_message("⛔ Security Error: Only the bot owner can view private CVS accounts.", ephemeral=True)
        return False


def get_stock_accounts(filter_type: str = "all") -> List[Dict[str, Any]]:
    """Returns CVS accounts that have active coupons, optionally filtered by coupon type."""
    accs = [a for a in cvs_accounts_db if a.get("coupons")]
    f_clean = filter_type.lower().strip()
    if f_clean == "4":
        return [a for a in accs if any("4 off" in str(c).lower() for c in a.get("coupons", []))]
    elif f_clean == "3":
        return [a for a in accs if any("3 off" in str(c).lower() for c in a.get("coupons", []))]
    elif f_clean in ("40", "40%"):
        return [a for a in accs if any("40%" in str(c).lower() for c in a.get("coupons", []))]
    elif f_clean == "other":
        return [a for a in accs if not any(k in str(c).lower() for k in ["4 off", "3 off", "40%"] for c in a.get("coupons", []))]
    return accs


def build_stock_embed(page: int = 0, filter_type: str = "all", per_page: int = 10) -> Tuple[discord.Embed, int, int]:
    stock = get_stock_accounts(filter_type)
    total_items = len(stock)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    current_page = max(0, min(page, total_pages - 1))

    start_idx = current_page * per_page
    end_idx = min(start_idx + per_page, total_items)
    page_items = stock[start_idx:end_idx]

    filter_title = "All Active Stock"
    if filter_type == "4":
        filter_title = "$4 Off Entire Purchase"
    elif filter_type == "3":
        filter_title = "$3 Off Entire Purchase"
    elif filter_type in ("40", "40%"):
        filter_title = "40% Off 1 Item"
    elif filter_type == "other":
        filter_title = "Special Offers ($8 & $5 Off)"

    embed = discord.Embed(
        title=f"📦 CVS Coupon Stock • {filter_title}",
        description=(
            f"> 🎟️ **Available In Stock:** `{total_items}` accounts loaded with active coupons\n"
            "> 💡 **To Mark Used:** Pick an account from the dropdown below or run `/used account:<name>`"
        ),
        color=COLOR_SUCCESS if total_items > 0 else COLOR_WARN,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

    if not page_items:
        embed.description = "📭 No active coupons found in this category."
    else:
        for idx, a in enumerate(page_items, start_idx + 1):
            phone_raw = a.get('phone', '')
            phone_fmt = f"({phone_raw[:3]}) {phone_raw[3:6]}-{phone_raw[6:]}" if len(phone_raw) == 10 else (phone_raw or "—")
            card = str(a.get('extraCareNumber', ''))
            last4 = card[-4:] if len(card) >= 4 else card
            c_list = a.get('coupons', [])
            c_str = ", ".join(c_list)
            clean_c = re.sub(r'\(Exp:.*?\)', '', c_str).strip()
            exp_m = re.search(r'Exp:?\s*([A-Za-z0-9\s\,]+?)\)', c_str)
            exp = exp_m.group(1).strip() if exp_m else "Active"

            link_str = f" • [1-Click Link]({a['coupon_link']})" if a.get('coupon_link') else ""
            val = f"📞 **Phone:** `{phone_fmt}` ⏐ **Card:** `ends {last4}`\n🎟️ **Coupon:** `{clean_c}`\n⏳ **Expires:** `{exp}`{link_str}"
            embed.add_field(
                name=f"`{idx:02d}.` {a.get('name', 'Account')}",
                value=val,
                inline=False
            )

    embed.set_footer(text=f"AIO Bot • Page {current_page + 1} of {total_pages} • Total in Stock: {total_items}")
    return embed, current_page, total_pages


class StockMarkUsedSelect(discord.ui.Select):
    def __init__(self, page_accounts: List[Dict[str, Any]]):
        options = []
        for acc in page_accounts:
            c_list = acc.get("coupons", [])
            c_str = ", ".join(c_list) if c_list else "Coupon"
            clean_c = re.sub(r'\(Exp:.*?\)', '', c_str).strip()
            exp_m = re.search(r'Exp:?\s*([A-Za-z0-9\s\,]+?)\)', c_str)
            exp = f" | Exp: {exp_m.group(1).strip()}" if exp_m else ""

            phone_raw = acc.get('phone', '')
            phone_fmt = f"({phone_raw[:3]}) {phone_raw[3:6]}-{phone_raw[6:]}" if len(phone_raw) == 10 else phone_raw
            label = f"{acc.get('name', 'Account')} — {clean_c}"[:100]
            desc = f"📞 {phone_fmt} • Ends {str(acc.get('extraCareNumber', ''))[-4:]}{exp}"[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(acc["id"]),
                    description=desc,
                    emoji="🏷️"
                )
            )
        super().__init__(
            placeholder="🏷️ Quick Mark as Used: Choose account from this list...",
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="No active coupons on this page", value="none")],
            disabled=not bool(options),
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No active coupons to mark on this page.", ephemeral=True)
            return

        acc_id = int(self.values[0])
        acc = get_cvs_account(str(acc_id))
        if not acc:
            await interaction.response.send_message("❌ Account not found.", ephemeral=True)
            return

        c_list = acc.get("coupons", [])
        if not c_list:
            await interaction.response.send_message(f"⚠️ **{acc.get('name')}** has no active coupons loaded.", ephemeral=True)
            return

        coupon_to_use = c_list[0]
        clean_coupon = re.sub(r'\(Exp:.*?\)', '', coupon_to_use).strip()

        success, msg, updated_acc = mark_coupon_used(
            account_query=acc_id,
            coupon_name=coupon_to_use,
            user_tag=str(interaction.user)
        )

        if not success:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        embed, cur_p, tot_p = build_stock_embed(self.view.current_page, self.view.filter_type)
        self.view.current_page = cur_p
        self.view.total_pages = tot_p
        self.view.update_select()
        await interaction.response.edit_message(embed=embed, view=self.view)

        await interaction.followup.send(
            f"✅ **Marked as Used!**\n• **Cardholder:** {acc.get('name')} (`{acc.get('phone')}`)\n• **Coupon:** {clean_coupon}\nInventory updated!",
            ephemeral=True
        )


class CVSStockView(discord.ui.View):
    def __init__(self, page: int = 0, filter_type: str = "all"):
        super().__init__(timeout=240)
        self.current_page = page
        self.filter_type = filter_type

        stock = get_stock_accounts(self.filter_type)
        per_page = 10
        self.total_pages = max(1, (len(stock) + per_page - 1) // per_page)

        start_idx = self.current_page * per_page
        end_idx = min(start_idx + per_page, len(stock))
        page_items = stock[start_idx:end_idx]

        self.dropdown = StockMarkUsedSelect(page_items)
        self.add_item(self.dropdown)

    def update_select(self):
        self.remove_item(self.dropdown)
        stock = get_stock_accounts(self.filter_type)
        per_page = 10
        self.total_pages = max(1, (len(stock) + per_page - 1) // per_page)
        self.current_page = max(0, min(self.current_page, self.total_pages - 1))
        start_idx = self.current_page * per_page
        end_idx = min(start_idx + per_page, len(stock))
        page_items = stock[start_idx:end_idx]
        self.dropdown = StockMarkUsedSelect(page_items)
        self.add_item(self.dropdown)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_owner_only(interaction.user, interaction.guild):
            return True
        await interaction.response.send_message("⛔ Security Error: Only the bot owner can view private CVS stock.", ephemeral=True)
        return False

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        else:
            self.current_page = self.total_pages - 1
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, emoji="▶️", row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        else:
            self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="All Stock", style=discord.ButtonStyle.success, emoji="📋", row=1)
    async def all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_type = "all"
        self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="$4 Off", style=discord.ButtonStyle.secondary, emoji="🎟️", row=2)
    async def four_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_type = "4"
        self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="$3 Off", style=discord.ButtonStyle.secondary, emoji="🎟️", row=2)
    async def three_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_type = "3"
        self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="40% Off", style=discord.ButtonStyle.secondary, emoji="🎟️", row=2)
    async def forty_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_type = "40"
        self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Other ($8/$5)", style=discord.ButtonStyle.secondary, emoji="💰", row=2)
    async def other_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_type = "other"
        self.current_page = 0
        embed, cur_p, tot_p = build_stock_embed(self.current_page, self.filter_type)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)


class MarkCouponUsedModal(discord.ui.Modal):
    coupon_input = discord.ui.TextInput(
        label="Coupon Name / Description Used",
        placeholder="e.g. $4 off $20 Crest, 40% off 1 item, $8 off $40",
        required=True,
        max_length=150
    )
    savings_input = discord.ui.TextInput(
        label="Amount Saved ($) (Optional)",
        placeholder="e.g. 4.00, 8.50",
        required=False,
        max_length=20
    )
    notes_input = discord.ui.TextInput(
        label="Purchase / Register Notes (Optional)",
        placeholder="e.g. Used at self-checkout on Colgate toothpaste",
        required=False,
        max_length=200
    )

    def __init__(self, account_id: int, parent_view: Optional[Any] = None):
        super().__init__(title=f"🏷️ Mark Coupon Used — #{account_id}")
        self.account_id = account_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        c_name = self.coupon_input.value.strip()
        sav_raw = self.savings_input.value.strip().replace("$", "")
        sav_val = None
        if sav_raw:
            try:
                sav_val = float(sav_raw)
            except ValueError:
                pass

        notes = self.notes_input.value.strip() or None
        success, msg, acc = mark_coupon_used(
            account_query=self.account_id,
            coupon_name=c_name,
            savings=sav_val,
            notes=notes,
            user_tag=str(interaction.user)
        )

        if not success or not acc:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        if self.parent_view:
            embed, file = format_account_card(acc)
            try:
                await interaction.response.edit_message(embed=embed, attachments=[file], view=self.parent_view)
                sav_text = f" (Saved ${sav_val:.2f})" if sav_val else ""
                await interaction.followup.send(
                    f"✅ **Coupon Marked Used:** '{c_name}' recorded for Account #{acc['id']} **{acc.get('name')}**!{sav_text}",
                    ephemeral=True
                )
                return
            except Exception:
                pass

        sav_text = f" (Saved ${sav_val:.2f})" if sav_val else ""
        await interaction.response.send_message(
            f"✅ **Coupon Marked Used:** '{c_name}' recorded for Account #{acc['id']} **{acc.get('name')}**!{sav_text}",
            ephemeral=True
        )


class AddCouponModal(discord.ui.Modal):
    coupon_input = discord.ui.TextInput(
        label="Coupon Name / Description to Load",
        placeholder="e.g. $4 off $20 Colgate, 40% off 1 item, $10 CarePass",
        required=True,
        max_length=150
    )
    notes_input = discord.ui.TextInput(
        label="Expiration / Details (Optional)",
        placeholder="e.g. Expires Sep 20, digital sent to card",
        required=False,
        max_length=150
    )

    def __init__(self, account_id: int, parent_view: Optional[Any] = None):
        super().__init__(title=f"➕ Load Coupon — #{account_id}")
        self.account_id = account_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        c_name = self.coupon_input.value.strip()
        details = self.notes_input.value.strip()
        full_desc = f"{c_name} ({details})" if details else c_name

        acc = get_cvs_account(str(self.account_id))
        if not acc:
            await interaction.response.send_message("❌ Account not found.", ephemeral=True)
            return

        acc.setdefault("coupons", []).append(full_desc)
        save_cvs_accounts(cvs_accounts_db)

        if self.parent_view:
            embed, file = format_account_card(acc)
            try:
                await interaction.response.edit_message(embed=embed, attachments=[file], view=self.parent_view)
                await interaction.followup.send(
                    f"✅ **Coupon Added:** Loaded '{full_desc}' to Account #{acc['id']} **{acc.get('name')}**!",
                    ephemeral=True
                )
                return
            except Exception:
                pass

        await interaction.response.send_message(
            f"✅ **Coupon Added:** Loaded '{full_desc}' to Account #{acc['id']} **{acc.get('name')}**!",
            ephemeral=True
        )

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
            title="💳 CVS ExtraCare Barcode",
            description="Scannable barcode generated below for register & self-checkout scanners.",
            color=COLOR_PRIMARY
        )
        embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")

        formatted_card = " ".join([raw_card[i:i+4] for i in range(0, len(raw_card), 4)])
        embed.add_field(name="🔢 ExtraCare Number", value=f"`{formatted_card}`", inline=False)

        if self.name_phone.value.strip():
            embed.add_field(name="👤 Cardholder", value=f"**{self.name_phone.value.strip()}**", inline=True)

        if self.extrabucks.value.strip():
            embed.add_field(name="💰 ExtraBucks Rewards", value=f"**{self.extrabucks.value.strip()}**", inline=True)

        if self.creds.value.strip():
            parts = self.creds.value.strip().split("|")
            email_part = parts[0].strip()
            pass_part = parts[1].strip() if len(parts) > 1 else ""
            val = f"📧 `{email_part}`\n🔑 ||`{pass_part}`||" if pass_part else f"📧 `{email_part}`"
            embed.add_field(name="🔐 Account Credentials", value=val, inline=False)

        if self.coupons_notes.value.strip():
            embed.add_field(name="🎟️ Loaded Coupons & Notes", value=self.coupons_notes.value.strip(), inline=False)

        embed.set_image(url="attachment://cvs_barcode.png")
        embed.set_footer(text="AIO Bot • Barcode Generator")

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

def _do_checkout(
    items: List[Dict[str, Any]],
    coupons: List[Any],
    user_id: Optional[int] = None,
    user_name: Optional[str] = None
) -> tuple:
    """
    Shared checkout helper — records the trip in savings_tracker and returns
    (embed, subtotal, total_due, coupon_spend, net_saved, datetime_now).
    """
    subtotal     = sum(i['price'] for i in items)
    total_due, _ = calculate_best_bundles(items, coupons)
    coupon_spend = sum(coupon_cost(c) for c in coupons)
    net_saved    = (subtotal - total_due) - coupon_spend
    savings_pct  = round((net_saved / subtotal * 100), 1) if subtotal > 0 else 0.0
    now = datetime.now()

    savings_tracker["trip_count"]        += 1
    savings_tracker["total_full_price"]  += subtotal
    savings_tracker["total_paid"]        += total_due
    savings_tracker["total_coupon_cost"] += coupon_spend
    savings_tracker["total_net_saved"]   += net_saved
    
    trip_id = len(savings_tracker.get("trips", [])) + 1
    savings_tracker.setdefault("trips", []).append({
        "id":           trip_id,
        "user_id":      user_id,
        "user_name":    user_name or "Shopper",
        "date":         now.strftime("%Y-%m-%d"),
        "time":         now.strftime("%H:%M:%S"),
        "items":        [{"name": i["name"], "price": i["price"]} for i in items],
        "coupons":      coupons,
        "subtotal":     subtotal,
        "total_due":    total_due,
        "coupon_spend": coupon_spend,
        "net_saved":    net_saved,
        "savings_pct":  savings_pct
    })
    save_savings(savings_tracker)

    shopper_line = f"👤 **Shopper:** {user_name}\n" if user_name else ""
    embed = discord.Embed(
        title="✅ Trip Checkout Complete",
        description=(
            f"{shopper_line}"
            f"🗓️ **{now.strftime('%A, %b %d, %Y @ %I:%M %p')}**\n"
            f"💰 **Net Saved This Trip:** **${net_saved:.2f}** ({savings_pct}% Saved)\n"
            f"📈 **Lifetime Saved:** **${savings_tracker['total_net_saved']:.2f}** across `{savings_tracker['trip_count']}` trip(s)"
        ),
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="🏷️ Full Retail", value=f"**${subtotal:.2f}**", inline=True)
    embed.add_field(name="💵 Register Paid", value=f"**${total_due:.2f}**", inline=True)
    embed.add_field(name="🎟️ Coupon Spend", value=f"**${coupon_spend:.2f}**", inline=True)
    embed.set_footer(text="AIO Bot • Savings Tracker")
    return embed, subtotal, total_due, coupon_spend, net_saved, now


def build_cart_embed(user_id: int, notice: Optional[str] = None) -> discord.Embed:
    session = get_session(user_id)
    items = session["items"]
    coupons = session["coupons"]
    subtotal = sum(i['price'] for i in items)

    embed = discord.Embed(
        title="🛒 Shopping Cart & Optimizer",
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    desc_lines = [
        "Active Cart Session • Real-Time Savings Engine"
    ]
    if notice:
        desc_lines.append(f"\n{notice}\n")

    if not items:
        desc_lines.append("\n📭 Cart is empty. Click **Add Items** or type `/add` to start!")
    else:
        desc_lines.append(f"\n**📦 Items in Cart ({len(items)}):**")
        for item in items[:12]:
            desc_lines.append(f"• **{item['name']}** — `${item['price']:.2f}`")
        if len(items) > 12:
            desc_lines.append(f"*...and {len(items) - 12} more item(s)*")

    embed.description = "\n".join(desc_lines)
    coupon_str = ", ".join(f"`{coupon_label(c)}`" for c in coupons) if coupons else "*None loaded*"

    embed.add_field(name="💵 Subtotal", value=f"**${subtotal:.2f}** ({len(items)} items)", inline=True)
    embed.add_field(name="🎟️ Coupons", value=coupon_str, inline=True)
    if items and coupons:
        est_due, _ = calculate_best_bundles(items, coupons)
        saved = max(0.0, subtotal - est_due)
        embed.add_field(name="💰 Est. Due", value=f"**${est_due:.2f}** *(Save ${saved:.2f})*", inline=True)

    embed.set_footer(text="AIO Bot • Use /optimize for checkout plan")
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
        embed   = _do_checkout(items, coupons, user_id=interaction.user.id, user_name=interaction.user.display_name)[0]
        reset_session(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Clear Cart", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        reset_session(interaction.user.id)
        await interaction.response.send_message("🧹 Your cart and coupons have been cleared!", ephemeral=True)


class EmbedSuccessView(discord.ui.View):
    def __init__(self, jump_url: str):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Button(label="Jump to Embed ↗", url=jump_url, style=discord.ButtonStyle.link))


def build_custom_rich_embed(
    title: str,
    description: str,
    author: Union[discord.Member, discord.User],
    color_input: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    image_url: Optional[str] = None,
    footer_text: Optional[str] = None,
    guild: Optional[discord.Guild] = None
) -> discord.Embed:
    resolved_color = resolve_color(color_input) if color_input else discord.Color(0x9b59b6)
    if not resolved_color:
        resolved_color = discord.Color(0x9b59b6)

    embed = discord.Embed(
        title=title,
        description=description,
        color=resolved_color,
        timestamp=datetime.now(timezone.utc)
    )

    # Sleek author header with user's avatar
    author_name = getattr(author, "display_name", str(author))
    author_icon = author.display_avatar.url if hasattr(author, "display_avatar") else None
    embed.set_author(name=author_name, icon_url=author_icon)

    # Optional thumbnail & large banner image
    if thumbnail_url and thumbnail_url.strip().startswith(('http://', 'https://')):
        embed.set_thumbnail(url=thumbnail_url.strip())
    if image_url and image_url.strip().startswith(('http://', 'https://')):
        embed.set_image(url=image_url.strip())

    # Aesthetic footer
    server_name = guild.name if guild else "Community Announcement"
    if footer_text and footer_text.strip():
        embed.set_footer(text=f"{footer_text.strip()} • {server_name}")
    else:
        embed.set_footer(text=f"{server_name} • Official Broadcast")

    return embed


def build_embed_success_card(target_channel: discord.TextChannel, sent_msg: discord.Message, embed: discord.Embed, ping: Optional[str] = None) -> discord.Embed:
    hex_color = f"#{embed.color.value:06X}" if embed.color else "#9B59B6"
    char_count = len(embed.description or "") + len(embed.title or "")
    card = discord.Embed(
        title="✨ Custom Embed Broadcasted!",
        description=(
            f"Your embed has been deployed to {target_channel.mention}!\n\n"
            f"• 🎯 **Target Channel:** {target_channel.mention}\n"
            f"• 🎨 **Theme Accent:** `{hex_color}`\n"
            f"• 📏 **Content Length:** `{char_count}` chars\n"
            f"• 🔔 **Audience Mention:** `{ping if ping else 'None'}`\n"
            f"• 🔗 **Live Message:** [Click to Jump to Embed ↗]({sent_msg.jump_url})"
        ),
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    card.set_footer(text="AIO Embed Studio • Broadcast Confirmed")
    return card


class EmbedBuilderModal(discord.ui.Modal, title="🎨 Rich Embed Designer Studio"):
    embed_title = discord.ui.TextInput(
        label="🏷️ Embed Title",
        placeholder="e.g. ⚡ SUMMER EVENT LIVE! / 📢 Server Announcement",
        required=True,
        max_length=256
    )
    embed_desc = discord.ui.TextInput(
        label="📝 Description & Content (Supports Markdown)",
        style=discord.TextStyle.paragraph,
        placeholder="> 🚀 Enter your message, bullet points, links, or announcement here...",
        required=True,
        max_length=4000
    )
    embed_color = discord.ui.TextInput(
        label="🎨 Theme Color (Hex or Name)",
        placeholder="e.g. purple, gold, cyan, emerald, crimson, blue, #ff007f",
        default="purple",
        required=False,
        max_length=30
    )
    embed_image = discord.ui.TextInput(
        label="🖼️ Banner Image URL (Optional)",
        placeholder="https://media.giphy.com/... or https://i.imgur.com/....png",
        required=False,
        max_length=500
    )
    embed_footer = discord.ui.TextInput(
        label="📌 Custom Footer Note (Optional)",
        placeholder="e.g. Server Staff • Click links above for details",
        required=False,
        max_length=200
    )

    def __init__(self, target_channel: discord.TextChannel, ping: Optional[str] = None):
        super().__init__()
        self.target_channel = target_channel
        self.ping = ping

    async def on_submit(self, interaction: discord.Interaction):
        embed = build_custom_rich_embed(
            title=self.embed_title.value.strip(),
            description=self.embed_desc.value.strip(),
            author=interaction.user,
            color_input=self.embed_color.value.strip() if self.embed_color.value else None,
            image_url=self.embed_image.value.strip() if self.embed_image.value else None,
            footer_text=self.embed_footer.value.strip() if self.embed_footer.value else None,
            guild=interaction.guild
        )

        content = self.ping if self.ping in ("@everyone", "@here") else None

        try:
            sent_msg = await self.target_channel.send(content=content, embed=embed)
            card = build_embed_success_card(self.target_channel, sent_msg, embed, content)
            view = EmbedSuccessView(sent_msg.jump_url)
            await interaction.response.send_message(embed=card, view=view, ephemeral=True)
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
            embed.set_footer(text=f"AIO Bot • Blackjack • {self.player.display_name}")
        else:
            embed.set_footer(text=f"AIO Bot • Blackjack • {self.player.display_name}")
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
        embed = discord.Embed(title="🔴 Connect 4 Arena", color=COLOR_PRIMARY)
        embed.description = f"**Player 1 (🔴):** {self.p1.mention}\n**Player 2 (🟡):** {self.p2.mention if self.p2 else p2_name}\n\n" + render_connect4_board(self.board)
        if status_msg:
            embed.add_field(name="Status", value=status_msg, inline=False)
        else:
            current = self.p1.mention if self.turn == self.p1.id else (self.p2.mention if self.p2 else "AIO Bot 🤖")
            piece = "🔴" if self.turn == self.p1.id else "🟡"
            embed.add_field(name="Turn", value=f"{piece} {current}'s turn to drop!", inline=False)
        embed.set_footer(text="AIO Bot • Connect 4")
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
                title="🧠 Trivia • " + ("🎉 Correct!" if is_correct else "❌ Incorrect!"),
                color=COLOR_SUCCESS if is_correct else COLOR_ERROR
            )
            embed.add_field(name="Question", value=self.q_data["q"], inline=False)
            embed.add_field(
                name="Correct Answer",
                value=f"**{chr(65+correct_idx)}. {self.q_data['options'][correct_idx]}**",
                inline=True
            )
            embed.add_field(name="Did You Know?", value=f"{self.q_data.get('info', 'Great knowledge!')}{coin_reward_str}", inline=False)
            embed.set_footer(text=f"AIO Bot • Played by {self.user.display_name}")

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
                new_embed.set_footer(text="AIO Bot • 45s Timer")
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
            embed = discord.Embed(title="🪨 Rock-Paper-Scissors", color=COLOR_PRIMARY)
            embed.add_field(name=f"👤 {self.p1.display_name}", value=f"Picked **{c1}**", inline=True)
            embed.add_field(name=f"👤 {self.p2.display_name}", value=f"Picked **{c2}**", inline=True)
            embed.add_field(name="Result", value=outcome, inline=False)
            embed.set_footer(text="AIO Bot • RPS Duel")
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
            embed = discord.Embed(title="🪨 Rock-Paper-Scissors", color=COLOR_PRIMARY)
            embed.add_field(name=f"👤 {self.p1.display_name}", value=f"Picked **{choice}**", inline=True)
            embed.add_field(name="🤖 AIO Bot", value=f"Picked **{bot_choice}**", inline=True)
            embed.add_field(name="Result", value=outcome, inline=False)
            embed.set_footer(text="AIO Bot • RPS Duel")
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
                title="🎰 3x3 Slots",
                description=f"{grid_text}\n\n{status_text}",
                color=color_val
            )
            emb.add_field(name="💰 Stake", value=f"**{self.bet:,} 🪙**", inline=True)
            emb.add_field(name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
            emb.set_footer(text=f"AIO Bot • Spun by {self.user.display_name}")
            return emb

        try:
            # Reel animation step 1: All 3 columns spinning
            await interaction.response.edit_message(embed=make_spin_embed(format_3x3_grid(grid, 0), "*Spinning reels...*"), view=None)
            await asyncio.sleep(0.9)

            # Reel animation step 2: Column 1 stops
            await interaction.message.edit(embed=make_spin_embed(format_3x3_grid(grid, 1), "*Reel 1 locked... Reels 2 & 3 spinning...*"))
            await asyncio.sleep(0.8)

            # Reel animation step 3: Column 2 stops
            await interaction.message.edit(embed=make_spin_embed(format_3x3_grid(grid, 2), "*Reels 1 & 2 locked... Final reel spinning...*"))
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
                    f"🎉 **WINNER!**\n{hits_str}\n💰 Won: **+{winnings:,} 🪙**!",
                    color
                )
                final_embed.set_field_at(1, name="👛 Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
            else:
                final_embed = make_spin_embed(
                    format_3x3_grid(grid, 3),
                    f"💀 **No matching lines!** Lost **{self.bet:,} 🪙**",
                    COLOR_ERROR
                )

            final_embed.set_footer(text=f"AIO Bot • Spun by {self.user.display_name}")
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
    def __init__(self, author_perms: discord.Permissions, is_owner: bool, is_cvs: bool = True):
        self.author_perms = author_perms
        self.is_owner = is_owner
        self.is_cvs = is_cvs
        options = []
        if is_cvs:
            options.append(discord.SelectOption(label="Coupon Optimizer", value="coupons", description="Smart cart calculation & coupon bundling", emoji="🛍️"))
        options.append(discord.SelectOption(label="Server Moderation", value="mod", description="Server control, anti-raid, filters & mod cases", emoji="🛡️"))
        options.append(discord.SelectOption(label="Ticket Support", value="tickets", description="Interactive support tickets, claims & transcripts", emoji="🎫"))
        if is_cvs:
            options.append(discord.SelectOption(label="Games & Arcade", value="games", description="Blackjack, Connect 4, Trivia, Slots, RPS & Dice", emoji="🎮"))
        options.append(discord.SelectOption(label="Embeds & Utilities", value="utils", description="Custom rich embeds, latency & diagnostics", emoji="🎨"))
        if is_owner:
            options.append(discord.SelectOption(label="Owner Commands", value="owner", description="Operator management and owner tools", emoji="👑"))
        super().__init__(placeholder="📖 Select a command category to view...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        embed = discord.Embed(color=COLOR_PRIMARY)
        if cat == "coupons" and self.is_cvs:
            embed.title = "🛍️ Coupon Optimizer Guide"
            embed.description = "Save maximum money at the register with optimal coupon bundles."
            embed.add_field(name="Interactive Panel", value="`/panel` or `!panel` — open the interactive button & modal shopping interface", inline=False)
            embed.add_field(name="Load Coupons", value="`/coupons` or `!coupons [val1] [val2] ...` (e.g. `!coupons 8 8 5 half` or `/coupons 8 8 5 half`)", inline=False)
            embed.add_field(name="Add Items", value="`/add` or `!add [item] [price] ...` (e.g. `!add Fairlife 4.49 Shampoo 6.59` or `/add ...`)", inline=False)
            embed.add_field(name="Calculate Strategy", value="`/optimize` or `!optimize` — displays the best transaction bundles & advice", inline=False)
            embed.add_field(name="Checkout & History", value="`/checkout` or `!checkout` — save trip & get receipt\n`/savings` or `!savings` — lifetime stats\n`/history` or `!history` — view past trips", inline=False)
            embed.add_field(name="Instant Calculator", value="`/calc` or `!calc [items] | [coupons]` (e.g. `!calc Fairlife 4.49, Shampoo 6.59 | 8 5` or `/calc ...`)", inline=False)
            embed.add_field(name="Cart Management", value="`/cart` — view current cart\n`/undo` — remove last item added\n`/remove [name]` — remove item by name\n`/clear` — wipe cart & coupons", inline=False)
        elif cat == "tickets":
            embed.title = "🎫 Support Ticket System"
            embed.description = "Member inquiry, customer service, and support ticket management tools."
            embed.add_field(name="Interactive Panel", value="`/ticketpanel` (or `/panel`) — deploy interactive panel with a 1-click **📩 Open Ticket** button", inline=False)
            embed.add_field(name="Open Ticket", value="`/ticket` or `!ticket` — open a dedicated private ticket channel directly", inline=False)
            embed.add_field(name="Close Ticket", value="`/close` or `!close` — close the active ticket channel", inline=False)
            embed.add_field(name="Claim Ticket", value="`/claim` or `!claim` — staff claim responsibility for the ticket", inline=False)
            embed.add_field(name="Ticket Transcript", value="`/transcript` or `!transcript` — generate a full text log of the conversation", inline=False)
            embed.add_field(name="Automated Setup", value="`/setup-tickets` — automatically create the support ticket categories and logs", inline=False)
        elif cat == "mod":
            embed.title = "🛡️ Server Moderation Suite"
            embed.description = "Administrative security, anti-raid, and discipline tools. Works with `!` or `/`."
            embed.add_field(name="Server Lockdown & Anti-Raid", value="`/lockdown [action: on/off] [reason]` — emergency lockdown for all server text channels", inline=False)
            embed.add_field(name="Auto-Mod Word Filter", value="`/filter add [word]` / `/filter remove [word]` / `/filter list` — automatic word censor & warning trigger", inline=False)
            embed.add_field(name="Case & Incident Logs", value="`/modlogs [@member]` — view all historical infractions\n`/case [id]` — look up detailed case file", inline=False)
            embed.add_field(name="Staff Private Notes", value="`/note add [@member] [note]` / `/note view` / `/note clear` — staff internal records", inline=False)
            embed.add_field(name="Member Discipline", value="`/kick` or `!kick [@member] [reason]`\n`/ban` or `!ban [@member] [reason]`\n`/unban` or `!unban [user_id_or_name]`\n`/timeout` or `!timeout [@member] [duration]` (e.g. `10m`, `1h`, `1d`)\n`/untimeout` or `!untimeout [@member]`", inline=False)
            embed.add_field(name="Role Management", value="`/giverole [@member] [@role]` (or `/role give`) — grant a role to a member\n`/removerole [@member] [@role]` (or `/role remove`) — revoke a role\n`/fixroles` — audit role hierarchy & repair bot positioning", inline=False)
            embed.add_field(name="Warnings System", value="`/warn` or `!warn [@member] [reason]` — log a warning\n`/warnings` or `!warnings [@member]` — view warning record\n`/clearwarnings` or `!clearwarnings [@member]` — wipe records", inline=False)
            embed.add_field(name="Channel & Message Management", value="`/modpanel` or `!modpanel` — interactive menu\n`/ticketpanel` or `!tickets` — deploy interactive support ticket panel\n`/dm [user] [msg]` or `!dm` — direct message member from bot\n`/nukechannel` or `!nukechannel` (alias `/nuke`) — recreate & wipe channel\n`/purge [amount] [member] [channel]` — bulk delete\n`/lock` & `/unlock` / `/slowmode [sec]`", inline=False)
        elif cat == "games" and self.is_cvs:
            embed.title = "🎮 Arcade, Casino & Economy"
            embed.description = "Interactive mini-games and full coin economy system powered by Discord buttons."
            embed.add_field(name="🪙 Coin Economy & Banking", value="`/balance` or `!bal [@member]` — check coin wallet\n`/daily` or `!daily` — claim daily 250 free coins (24h cooldown)\n`/pay` or `!pay [@member] [amount]` — transfer coins\n`/leaderboard` or `!top` — top 10 richest members", inline=False)
            embed.add_field(name="🃏 Blackjack / 21", value="`/blackjack [bet]` or `!blackjack` — play 21 against dealer with interactive Hit, Stand & Double Down buttons", inline=False)
            embed.add_field(name="🔴 Connect 4", value="`/connect4 [@opponent]` or `!connect4` — 7-column interactive drop board against friends or smart Bot AI", inline=False)
            embed.add_field(name="🧠 Trivia Quiz Challenge", value="`/trivia [category: general/tech/gaming/science]` or `!trivia` — 4-choice timed quiz challenge (+50 🪙 per win)", inline=False)
            embed.add_field(name="🎰 High-Roller Slots", value="`/slots [bet] [rounds]` or `!slots` — spinning slot machine with up to 50x Jackpot multipliers & automated multi-round spins", inline=False)
            embed.add_field(name="🪨 Rock-Paper-Scissors", value="`/rps [choice] [@opponent]` or `!rps` — secret choice duel against friends or the bot", inline=False)
            embed.add_field(name="🪙 Coinflip & Dice Roller", value="`/coinflip [heads/tails] [bet]` — animated flip & betting\n`/roll [dice]` — tabletop dice roller (e.g. `2d6`, `1d20+5`, `100`)", inline=False)
        elif cat == "utils":
            embed.title = "🎨 Embeds & Utilities"
            embed.description = "Creative, diagnostic, and communication utility tools for server staff and members."
            embed.add_field(name="Welcome System", value="`/testwelcome [@member]` — preview the new member welcome card\n`/setup-welcome` — set up or configure the `#👋-welcome` channel", inline=False)
            embed.add_field(name="Bot Announcement & Echo", value="`/say` or `!say [text]` — repost text and attached photos/images through the bot", inline=False)
            embed.add_field(name="Custom Embed Creator", value="`/embed` or `!embed` — open interactive modal to design & publish rich embeds with titles, images, colors, and footers", inline=False)
            embed.add_field(name="Server & Member Info", value="`/serverinfo` or `!serverinfo` — server stats, boosts, channels, and roles\n`/userinfo` or `!userinfo [@member]` — member details, account age, join date, permissions", inline=False)
            embed.add_field(name="Bot Status", value="`/ping` or `!ping` — bot latency\n`/about` or `!about` — system info", inline=False)
        elif cat == "owner":
            embed.title = "👑 Operator Commands"
            embed.description = "Administrative architecture and bot owner management commands."
            embed.add_field(
                name="Server Architecture & Channel Cleanup",
                value=(
                    "`/formatserver` (or `!setupserver`) — organize full server layout with categories, channels, and roles (shields `#form-automation`)\n"
                    "`/deletechannels` (or `!clearchannels`) — delete previous or leftover unformatted channels (shields `#form-automation`)"
                ),
                inline=False
            )
            if self.is_cvs:
                embed.add_field(name="Private Optimizer Hub", value="`/setup` — create `#🛒-coupon-optimizer` hub\n`[🛒 Open Private Optimizer Room]` — instant personal room for shopping & savings", inline=False)
                embed.add_field(name="CVS Accounts Database", value="`/accounts [query]` (or `!accounts`, `!cards`) — browse imported CVS ExtraCare accounts with barcode scans, search, pagination & custom card formatter", inline=False)
                embed.add_field(name="Database Management", value="`/delete-last-trip` (or `!undotrip`) — delete last recorded trip and revert lifetime savings stats", inline=False)
                embed.add_field(name="CPU Benchmark & Stress Test", value="`/run-stress-test` (or `!stresstest`, `!benchmark`) — benchmark algorithm latency across permutation graphs", inline=False)

        embed.set_footer(text="AIO Bot • Use ! or / for commands")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpMenuView(discord.ui.View):
    def __init__(self, author_perms: discord.Permissions, is_owner: bool, is_cvs: bool = True):
        super().__init__(timeout=None)
        self.add_item(HelpCategorySelect(author_perms, is_owner, is_cvs))


class VouchModal(discord.ui.Modal, title="Submit a Review / Vouch"):
    rating_input = discord.ui.TextInput(
        label="Rating (1 to 5 Stars)",
        placeholder="5",
        default="5",
        min_length=1,
        max_length=1,
        required=True
    )
    comment_input = discord.ui.TextInput(
        label="Your Review / Feedback",
        placeholder="How was your order and customer service experience?",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )

    def __init__(self, guild_id: int, staff_id: Optional[int] = None):
        super().__init__()
        self.guild_id = guild_id
        self.staff_id = staff_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.rating_input.value.strip())
            rating = max(1, min(5, val))
        except ValueError:
            rating = 5

        comment = self.comment_input.value.strip()
        vouch = add_vouch(
            guild_id=self.guild_id,
            user_id=interaction.user.id,
            user_name=str(interaction.user),
            rating=rating,
            comment=comment,
            staff_id=self.staff_id
        )

        guild = bot.get_guild(self.guild_id) if self.guild_id else interaction.guild
        if guild:
            ch = get_vouches_channel(guild)
            if ch:
                embed = build_vouch_embed(vouch, interaction.user)
                try:
                    await ch.send(embed=embed)
                except Exception as e:
                    print(f"⚠️ Error posting vouch to channel: {e}", file=sys.stderr)

        await interaction.response.send_message(
            f"⭐ **Thank you for your feedback!** Your {rating}/5 star review has been posted to our reviews channel.",
            ephemeral=True
        )


class TicketReviewLaunchView(discord.ui.View):
    def __init__(self, guild_id: int = 0, staff_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.staff_id = staff_id

    @discord.ui.button(label="Leave a Review", style=discord.ButtonStyle.success, emoji="⭐", custom_id="aio_ticket_leave_review_btn")
    async def btn_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_guild_id = self.guild_id or (interaction.guild.id if interaction.guild else 0)
        await interaction.response.send_modal(VouchModal(guild_id=target_guild_id, staff_id=self.staff_id))


class GiveawayEntryView(discord.ui.View):
    def __init__(self, count: int = 0):
        super().__init__(timeout=None)
        self.btn_enter.label = f"🎉 Enter ({count})"

    @discord.ui.button(label="🎉 Enter (0)", style=discord.ButtonStyle.primary, custom_id="aio_giveaway_enter_btn")
    async def btn_enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Giveaways can only be entered inside server channels.", ephemeral=True)
            return

        msg_id_str = str(interaction.message.id) if interaction.message else ""
        giveaway = giveaways_db.get(msg_id_str)
        if not giveaway:
            for k, v in giveaways_db.items():
                if v.get("channel_id") == interaction.channel_id and not v.get("ended"):
                    giveaway = v
                    msg_id_str = k
                    break

        if not giveaway:
            await interaction.response.send_message("❌ This giveaway is no longer active in our records.", ephemeral=True)
            return

        if giveaway.get("ended", False):
            await interaction.response.send_message("⏳ This giveaway has already ended!", ephemeral=True)
            return

        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts >= giveaway.get("end_time", 0):
            await interaction.response.send_message("⏳ This giveaway has concluded and is drawing winners.", ephemeral=True)
            return

        eligible, reason = check_giveaway_eligibility(giveaway, interaction.user)
        if not eligible:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        participants = giveaway.setdefault("participants", [])
        if interaction.user.id in participants:
            participants.remove(interaction.user.id)
            save_giveaways()
            button.label = f"🎉 Enter ({len(participants)})"
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
            await interaction.followup.send("❌ You left the giveaway.", ephemeral=True)
        else:
            participants.append(interaction.user.id)
            save_giveaways()
            button.label = f"🎉 Enter ({len(participants)})"
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
            await interaction.followup.send("🎉 **You're entered!** Best of luck!", ephemeral=True)


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

        info = tickets_db.get("tickets", {}).get(str(channel.id), {})
        t_id = info.get("id", 0)
        owner_id = info.get("owner_id", 0)

        is_optimizer = is_coupon_optimizer_channel(channel)
        if not is_optimizer:
            info_name = info.get("channel_name", "")
            if info_name and is_coupon_optimizer_channel(info_name):
                is_optimizer = True

        if not is_optimizer:
            # 1. Compile conversation transcript
            messages = []
            try:
                messages = [m async for m in channel.history(limit=500, oldest_first=True)]
                transcript_txt = format_ticket_transcript(messages, t_id, owner_id)
            except Exception as e:
                print(f"⚠️ Error compiling ticket transcript: {e}", file=sys.stderr)
                transcript_txt = f"AIO BOT TRANSCRIPT\nTicket #{t_id:04d}\nError generating transcript: {e}"
            file_bytes = transcript_txt.encode("utf-8")

            # 2. Archive transcript to #📁-ticket-logs
            if channel.guild:
                log_ch = get_ticket_logs_channel(channel.guild)
                if not log_ch:
                    staff_cat = discord.utils.get(channel.guild.categories, name="🛡️ STAFF ZONE")
                    founder_r = get_founder_role(channel.guild)
                    mod_r = get_moderator_role(channel.guild)
                    overwrites = {
                        channel.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        channel.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
                    }
                    if founder_r:
                        overwrites[founder_r] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
                    if mod_r:
                        overwrites[mod_r] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
                    try:
                        log_ch = await channel.guild.create_text_channel("📁-ticket-logs", category=staff_cat, overwrites=overwrites)
                    except Exception:
                        log_ch = None
                if log_ch:
                    try:
                        log_file = discord.File(io.BytesIO(file_bytes), filename=f"transcript-ticket-{t_id:04d}.txt")
                        log_embed = discord.Embed(
                            title=f"📁 Ticket #{t_id:04d} Closed",
                            description="Support ticket closed and conversation history archived.",
                            color=COLOR_PRIMARY,
                            timestamp=datetime.now(timezone.utc)
                        )
                        log_embed.add_field(name="🏷️ Channel", value=f"`#{channel.name}`", inline=True)
                        log_embed.add_field(name="👤 Customer", value=f"<@{owner_id}>", inline=True)
                        log_embed.add_field(name="🛡️ Closed By", value=interaction.user.mention, inline=True)
                        log_embed.add_field(name="💬 Messages", value=f"`{len(messages)}`", inline=True)
                        log_embed.add_field(name="📂 Transcript", value=f"`transcript-ticket-{t_id:04d}.txt`", inline=True)
                        log_embed.set_footer(text="AIO Bot • Ticket Archives", icon_url=channel.guild.icon.url if channel.guild and channel.guild.icon else None)
                        await log_ch.send(embed=log_embed, file=log_file)
                    except Exception as e:
                        print(f"⚠️ Error posting to ticket-logs: {e}", file=sys.stderr)

            # 3. Direct message customer with transcript and review button
            if channel.guild and owner_id:
                try:
                    owner = channel.guild.get_member(owner_id)
                    if not owner:
                        owner = await bot.fetch_user(owner_id)
                    if owner and not getattr(owner, "bot", False):
                        dm_file = discord.File(io.BytesIO(file_bytes), filename=f"transcript-ticket-{t_id:04d}.txt")
                        dm_embed = discord.Embed(
                            title=f"🎟️ Ticket #{t_id:04d} Closed • {channel.guild.name}",
                            description=(
                                "Thank you for contacting our team! Your ticket has concluded.\n\n"
                                "• **Transcript Attached:** Full archive of your conversation is attached below.\n"
                                "• **Feedback:** Click **Leave a Review** below to rate your experience."
                            ),
                            color=COLOR_SUCCESS,
                            timestamp=datetime.now(timezone.utc)
                        )
                        dm_embed.set_footer(text=f"AIO Bot • {channel.guild.name} Support", icon_url=channel.guild.icon.url if channel.guild and channel.guild.icon else None)
                        await owner.send(
                            embed=dm_embed,
                            file=dm_file,
                            view=TicketReviewLaunchView(guild_id=channel.guild.id, staff_id=interaction.user.id)
                        )
                except Exception as e:
                    print(f"ℹ️ Could not DM ticket owner {owner_id}: {e}", file=sys.stderr)

        close_ticket_record(channel.id)
        action_label = "Coupon room closed" if is_optimizer else "Ticket closed"
        await interaction.followup.send(f"🔒 **{action_label} by {interaction.user.mention}.** Channel deleting in 5 seconds...")
        await asyncio.sleep(5)
        if not is_protected_channel(channel):
            try:
                reason_txt = f"Coupon room closed by {interaction.user}" if is_optimizer else f"Support ticket closed by {interaction.user}"
                await channel.delete(reason=reason_txt)
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
        if is_coupon_optimizer_channel(interaction.channel):
            await interaction.response.send_message("ℹ️ Transcripts are disabled for coupon optimizer channels.", ephemeral=True)
            return
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
                f"Welcome {interaction.user.mention}! Support staff has been notified.\n\n"
                "**What to do next:**\n"
                "• Describe your issue or question below.\n"
                "• Upload relevant screenshots or details.\n"
                "• A team member will assist you shortly."
            ),
            color=COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 Opened By", value=f"{interaction.user.mention} • `{interaction.user.id}`", inline=True)
        embed.add_field(name="⏰ Opened", value=f"<t:{int(time.time())}:R>", inline=True)
        embed.add_field(name="📌 Status", value="`🟢 Open`", inline=True)
        embed.set_footer(text="AIO Bot • Support Center")

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
        brand_slug = "tacobell" if "taco" in self.brand.lower() else re.sub(r'[^a-zA-Z0-9]', '', self.brand).lower()[:10] or "food"
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

        instructions = (
            "**1️⃣ Account Info:** Staff will provide the account email and payment handle.\n"
            "**2️⃣ App Login:** Enter the email into the **Taco Bell app** and tap **Send Code**.\n"
            "**3️⃣ Instant OTP:** Ping staff here — they will immediately retrieve your OTP login code!"
        )

        embed = discord.Embed(
            title=f"🌮 {self.brand} Order #{ticket_num:04d}",
            description=(
                f"Welcome {interaction.user.mention}! Support staff has been notified.\n\n"
                f"**Order Details:**\n"
                f"• 📦 **Item:** **{self.brand} Preloaded Account(s)**\n"
                f"• 🔢 **Quantity:** **{qty}** account(s) — `${self.price:.2f}` each\n"
                f"• 💰 **Estimated Total:** **${total_est:.2f}**\n"
                f"• 📝 **Payment Note:** `{notes_val}`\n\n"
                f"**How This Works:**\n"
                f"{instructions}"
            ),
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 Customer", value=f"{interaction.user.mention} • `{interaction.user.id}`", inline=True)
        embed.add_field(name="⏰ Opened", value=f"<t:{int(time.time())}:R>", inline=True)
        embed.add_field(name="📌 Status", value="`🟢 Awaiting Staff`", inline=True)
        embed.set_footer(text="AIO Bot • Order Processing")

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
        title="🛍️ Rewards Store • Coming Soon",
        description=(
            "🚧 **Shop Updating — New Methods Coming Soon!**\n\n"
            "The previous rewards method was recently patched. We are actively working on and testing fresh new food and rewards deals!\n\n"
            "✨ **What to Expect:**\n"
            "• New working rewards methods\n"
            "• High-value accounts and discounts\n"
            "• Instant support ticket delivery\n\n"
            "📢 *Keep an eye on announcements for when stock and new methods drop!*"
        ),
        color=0xFFA500,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name="⏳ Current Status",
        value="*Previous method patched. Developing and testing replacement methods — coming soon!*",
        inline=False
    )
    embed.set_footer(text="AIO Rewards Store • New methods coming soon")
    return embed


class FoodAccountPurchaseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Coming Soon", style=discord.ButtonStyle.secondary, emoji="⏳", disabled=True, custom_id="aio_shop_coming_soon_btn")
    async def btn_coming_soon(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⏳ New rewards methods and stock are currently in testing. Stay tuned to announcements!", ephemeral=True)


# --- SHOP STATUS SYSTEM ---

def get_updated_status_channel_name(current_name: str, is_open: bool) -> str:
    """
    Computes the updated channel name reflecting open/closed status.
    Swaps 🔴 with 🟢 (and vice versa), and swaps 'closed' with 'open' (and vice versa).
    """
    target_emoji = "🟢" if is_open else "🔴"
    other_emoji = "🔴" if is_open else "🟢"
    name = current_name.strip()

    if other_emoji in name:
        name = name.replace(other_emoji, target_emoji)
    elif target_emoji not in name:
        name = f"{target_emoji}-{name.lstrip('-')}"

    if is_open:
        if "closed" in name:
            name = name.replace("closed", "open")
        elif "close" in name:
            name = name.replace("close", "open")
        elif not any(k in name for k in ("open", "status")):
            name = f"{name}-open"
    else:
        if "open" in name:
            name = name.replace("open", "closed")
        elif not any(k in name for k in ("closed", "status")):
            name = f"{name}-closed"

    while "--" in name:
        name = name.replace("--", "-")
    return name.strip("-")


def find_food_rewards_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Finds the food rewards store channel in the guild."""
    for c in guild.text_channels:
        cname = c.name.lower()
        if "food" in cname or "rewards" in cname:
            return c
    for c in guild.text_channels:
        cname = c.name.lower()
        if "shop" in cname and not any(k in cname for k in ("status", "open", "closed", "🟢", "🔴")):
            return c
    return None


def build_shop_status_embed(
    is_open: bool,
    author_name: str,
    message: Optional[str] = None,
    shop_ch_mention: Optional[str] = None
) -> discord.Embed:
    """Builds a concise notification embed announcing whether the shop is OPEN or CLOSED, linking to the shop channel."""
    shop_link = shop_ch_mention or "#🌮-food-rewards"
    if is_open:
        embed = discord.Embed(
            title="🟢 STORE IS NOW OPEN",
            description=(
                "The shop is open and taking orders!\n\n"
                f"Visit {shop_link} to view the menu and place your order."
            ),
            color=COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        if message and message.strip():
            embed.add_field(name="Notice", value=message.strip(), inline=False)
        embed.set_footer(text=f"AIO Store • Opened by {author_name}")
    else:
        embed = discord.Embed(
            title="🔴 STORE IS CURRENTLY CLOSED",
            description=(
                "The shop is currently closed and not taking new orders.\n\n"
                f"Active orders are being fulfilled. We will announce in {shop_link} when we reopen."
            ),
            color=COLOR_ERROR,
            timestamp=datetime.now(timezone.utc)
        )
        if message and message.strip():
            embed.add_field(name="Notice", value=message.strip(), inline=False)
        embed.set_footer(text=f"AIO Store • Closed by {author_name}")

    return embed


def find_shop_status_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Finds the designated shop status channel in the guild.
    Strictly excludes moderation logs, ticket logs, staff chat, and general channels.
    """
    # 1. Exact or dedicated shop status channel names
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if cname in ("🟢-shop-open", "🔴-shop-closed", "🟢-shop-status", "🔴-shop-status", "🟢-status", "🔴-status", "shop-status", "store-status", "shop-open", "shop-closed"):
            return ch

    # 2. Channels with shop-open, shop-closed, shop-status, store-status
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if any(k in cname for k in ("shop-open", "shop-closed", "shop-status", "store-status")):
            return ch

    # 3. Channels starting with 🟢 or 🔴 and containing status/shop/store
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if ("🟢" in cname or "🔴" in cname) and any(k in cname for k in ("shop", "status", "store")):
            if not any(ign in cname for ign in ("mod-log", "ticket-log", "staff", "audit", "general")):
                return ch

    # 4. General status channel (excluding staff / logs)
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if "status" in cname and not any(ign in cname for ign in ("mod-log", "ticket-log", "staff", "audit", "general")):
            return ch

    return None


def find_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Finds the designated support ticket panel channel in the guild.
    Strictly excludes shop status channels (#🟢-shop-open, #🔴-shop-closed),
    store channels, coupon channels, logs, and staff channels.
    """
    if not guild or not hasattr(guild, "text_channels"):
        return None

    # 1. Exact match for standard blueprint ticket panel channel
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if cname in ("📩-open-a-ticket", "open-a-ticket", "tickets", "ticket-panel", "support-tickets"):
            return ch

    # 2. Match channels containing ticket keywords while strictly filtering out shop/status/store/etc
    for ch in guild.text_channels:
        cname = ch.name.lower()
        if any(k in cname for k in ("shop", "status", "store", "food", "reward", "coupon", "optimizer", "log", "staff", "mod")):
            continue
        if "ticket" in cname or "open-a-ticket" in cname:
            return ch

    return None


async def update_shop_status(
    guild: discord.Guild,
    is_open: bool,
    author: Union[discord.Member, discord.User],
    message: Optional[str] = None,
    ping_everyone: bool = False
) -> Tuple[bool, str, Optional[discord.TextChannel], discord.Embed]:
    """
    Core engine to update shop status:
    1. Finds the dedicated status channel. If none exists, creates #🟢-shop-open (or #🔴-shop-closed).
    2. Renames ONLY that channel with green (🟢) or red (🔴).
    3. Purges previous status messages (e.g. replacing the OPEN message with CLOSED message).
    4. Sends the announcement embed linking to the shop channel.
    Returns (success, result_message, target_channel, embed).
    """
    ch = find_shop_status_channel(guild)
    if not ch:
        # Automatically create the shop status channel so staff never has to manually configure it
        cat = None
        for c in guild.categories:
            if "savings" in c.name.lower() or "rewards" in c.name.lower():
                cat = c
                break
        if not cat:
            for c in guild.categories:
                if "info" in c.name.lower():
                    cat = c
                    break

        init_name = "🟢-shop-open" if is_open else "🔴-shop-closed"
        try:
            ch = await guild.create_text_channel(
                init_name,
                category=cat,
                topic="Live shop opening status and operational hours. Check here to see if orders are being accepted!"
            )
        except Exception as e:
            return False, f"❌ Could not create status channel #{init_name}: {e}", None, None

    if ch:
        await apply_read_only_overwrites(ch)

    shop_ch = find_food_rewards_channel(guild)
    shop_mention = shop_ch.mention if shop_ch else "#🌮-food-rewards"

    embed = build_shop_status_embed(
        is_open=is_open,
        author_name=getattr(author, "display_name", str(author)),
        message=message,
        shop_ch_mention=shop_mention
    )

    old_name = ch.name
    new_name = get_updated_status_channel_name(old_name, is_open)
    rename_msg = ""

    if new_name != old_name:
        try:
            await ch.edit(name=new_name, reason=f"Shop status updated to {'OPEN' if is_open else 'CLOSED'} by {author}")
            rename_msg = f" and renamed channel to #{new_name}"
        except discord.RateLimited:
            rename_msg = " (⚠️ Channel rename rate-limited by Discord, embed posted successfully)"
        except discord.Forbidden:
            rename_msg = " (⚠️ Bot lacks permission to rename channel, embed posted successfully)"
        except Exception as e:
            rename_msg = f" (⚠️ Could not rename channel: {e})"

    # Purge previous messages in the status channel so only the single latest status is displayed
    try:
        if hasattr(ch, "purge"):
            await ch.purge(limit=25)
        elif hasattr(ch, "history"):
            async for m in ch.history(limit=10):
                await m.delete()
    except Exception:
        pass

    content = "@everyone" if ping_everyone else None
    await ch.send(content=content, embed=embed)
    status_str = "OPEN 🟢" if is_open else "CLOSED 🔴"
    summary = f"✅ Shop status updated to **{status_str}** in {ch.mention}{rename_msg}!"
    return True, summary, ch, embed


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
            {"name": "🎉-giveaways", "type": "text", "topic": "Official server giveaways and rewards! Enter active drops below."},
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
            {"name": "🟢-shop-open", "type": "text", "topic": "Live shop opening status and operational hours. Check here to see if orders are being accepted!"},
            {"name": "🌮-food-rewards", "type": "text", "topic": "Official rewards store — new methods coming soon!"},
            {"name": "⭐-vouches", "type": "text", "topic": "Customer vouches, reviews, feedback, and 5-star ratings."},
            {"name": "🏷️-deals-and-savings", "type": "text", "topic": "Share latest store deals, coupons, and discounts."}
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
            {"name": "📁-ticket-logs", "type": "text", "topic": "Archived support ticket logs and conversation transcripts."},
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
        if any(s in ch_name.lower() for s in ("shop-status", "store-status", "shop-open", "shop-closed", "🟢", "🔴")):
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
        try:
            case_id = log_mod_case(guild.id, "Warning", str(member), str(interaction.user), self.reason.value, f"Active warning count: {count}")
            embed = discord.Embed(title="⚠️ Warning Issued", color=COLOR_WARN)
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Warning Count", value=f"#{count}", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=self.reason.value, inline=False)
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            embed.set_footer(text="AIO Bot • Moderation")
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to warn this member.", ephemeral=True)


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
            embed.add_field(name="Duration", value=self.duration.value, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=r_text, inline=False)
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            embed.set_footer(text="AIO Bot • Moderation")
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
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            embed.add_field(name="Reason", value=r_text, inline=False)
            embed.set_footer(text="AIO Bot • Moderation")
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
            embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
            embed.add_field(name="Reason", value=r_text, inline=False)
            embed.set_footer(text="AIO Bot • Moderation")
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
        embed.set_footer(text="AIO Bot • Direct Message")

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
        confirm_embed.add_field(name="Recipient", value=f"{target} (`{target.id}`)", inline=True)
        confirm_embed.add_field(name="Sent By", value=interaction.user.mention if not is_anon else "*Anonymous Staff*", inline=True)
        confirm_embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        confirm_embed.add_field(name="Message", value=msg_text[:1000], inline=False)
        confirm_embed.set_footer(text="AIO Bot • Moderation")
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


class ModAddOrderModal(discord.ui.Modal, title="➕ Record Completed Order"):
    customer = discord.ui.TextInput(
        label="Customer (Mention, ID, or Username)",
        placeholder="e.g. @customer or 1234567890",
        required=True
    )
    price = discord.ui.TextInput(
        label="Price / Order Amount",
        placeholder="e.g. 10.00 or $15",
        required=True
    )
    item = discord.ui.TextInput(
        label="Item / Brand",
        placeholder="e.g. Taco Bell, 2x Accounts",
        default="Taco Bell",
        required=True
    )
    notes = discord.ui.TextInput(
        label="Notes (Optional)",
        placeholder="e.g. Paid via CashApp, manual backfill",
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        clean_price = self.price.value.replace("$", "").replace(",", "").strip()
        try:
            val = float(clean_price)
            if val < 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Invalid price. Please enter a valid dollar amount (e.g. `10.00` or `$15`).", ephemeral=True)
            return

        rec = record_manual_order(
            guild_id=guild.id if guild else 0,
            customer_input=self.customer.value,
            price=val,
            item=self.item.value,
            completed_by_id=interaction.user.id,
            completed_by_name=str(interaction.user),
            notes=self.notes.value or None,
            guild=guild
        )

        cust_display = f"<@{rec['customer_id']}>" if rec.get('customer_id') else rec.get('customer_name', 'Customer')
        embed = discord.Embed(
            title="✅ Order Recorded",
            description=f"Order #{rec['order_id']:02d} added to sales tracker.",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Order ID", value=f"`#{rec['order_id']:02d}`", inline=True)
        embed.add_field(name="Customer", value=cust_display, inline=True)
        embed.add_field(name="Item / Brand", value=f"**{rec['brand']}**", inline=True)
        embed.add_field(name="Amount", value=f"**${rec['amount']:.2f}**", inline=True)
        embed.add_field(name="Logged By", value=interaction.user.mention, inline=True)
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value.strip(), inline=False)

        embed.set_footer(text="AIO Sales Tracker • Use /orderstats to view stats")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            await interaction.channel.send(embed=embed)
        except Exception:
            pass


class ModOpenShopModal(discord.ui.Modal, title="🟢 Open Shop"):
    message_input = discord.ui.TextInput(
        label="Optional Announcement / Note",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Taking orders for Taco Bell! Deliveries ready.",
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, summary, ch, _ = await update_shop_status(
            guild=interaction.guild,
            is_open=True,
            author=interaction.user,
            message=self.message_input.value.strip() if self.message_input.value else None
        )
        await interaction.followup.send(summary, ephemeral=True)


class ModCloseShopModal(discord.ui.Modal, title="🔴 Close Shop"):
    message_input = discord.ui.TextInput(
        label="Optional Announcement / Note",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Closed for the evening! Orders will reopen tomorrow at 11 AM.",
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, summary, ch, _ = await update_shop_status(
            guild=interaction.guild,
            is_open=False,
            author=interaction.user,
            message=self.message_input.value.strip() if self.message_input.value else None
        )
        await interaction.followup.send(summary, ephemeral=True)


class ModShopStatusModal(discord.ui.Modal, title="🏪 Update Shop Status"):
    status_input = discord.ui.TextInput(
        label="Shop Status (Open or Closed)",
        placeholder="Type 'open' or 'closed'",
        required=True,
        max_length=20
    )
    message_input = discord.ui.TextInput(
        label="Optional Staff Announcement / Note",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Taking orders for Taco Bell! Deliveries ready.",
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw_status = self.status_input.value.strip().lower()
        if raw_status in ("open", "opened", "on", "yes", "true", "start"):
            is_open = True
        elif raw_status in ("closed", "close", "off", "no", "false", "stop"):
            is_open = False
        else:
            await interaction.response.send_message(
                "❌ Invalid status. Please type either `open` or `closed`.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        success, summary, ch, _ = await update_shop_status(
            guild=interaction.guild,
            is_open=is_open,
            author=interaction.user,
            message=self.message_input.value or None
        )
        await interaction.followup.send(summary, ephemeral=True)


# --- COUPON OPTIMIZER HUB & PRIVATE ROOM VIEWS ---

def build_coupon_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛒 CVS Coupon Optimizer Hub",
        description=(
            "Stack manufacturer and store coupons, optimize cashier scanning order, and maximize savings!\n\n"
            "Click **Open Private Optimizer Room** below to create your private shopping channel."
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/c/cd/CVS_Pharmacy_logo.svg/320px-CVS_Pharmacy_logo.svg.png")
    embed.add_field(
        name="Key Features",
        value=(
            "• **Smart Stacking:** Combines manufacturer and store digital coupons\n"
            "• **Cashier Sequencing:** Step-by-step ring-up instructions\n"
            "• **Threshold Optimization:** Hit dollar-off spend requirements\n"
            "• **Private & Isolated:** Your cart and savings calculations stay confidential"
        ),
        inline=False
    )
    embed.set_footer(text="AIO Bot • Coupon Optimizer")
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
                title=f"🛒 {interaction.user.display_name}'s Private Room",
                description=(
                    f"Welcome {interaction.user.mention}! This is your private shopping workspace.\n\n"
                    "• **Add Items:** Build your cart with items and prices\n"
                    "• **Load Coupons:** Load manufacturer and store coupons\n"
                    "• **Optimize Plan:** Calculate cashier scan sequence & savings\n"
                    "• **Undo Last:** Remove the most recent item\n"
                    "• **Checkout:** Save your completed trip summary\n"
                    "• **Close Room:** Delete this private channel when finished"
                ),
                color=COLOR_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            welcome_embed.set_footer(text="AIO Bot • Private CVS Session")

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
        embed = _do_checkout(items, coupons, user_id=interaction.user.id, user_name=interaction.user.display_name)[0]
        reset_session(interaction.user.id)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Clear Cart", style=discord.ButtonStyle.secondary, emoji="🧹", custom_id="croom_clear", row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        reset_session(interaction.user.id)
        await interaction.response.send_message("🧹 Your cart and coupons have been cleared!", ephemeral=True)

    @discord.ui.button(label="Close Room", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="croom_close", row=2)
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_protected_channel(interaction.channel):
            await interaction.response.send_message("🛡️ **Protected Channel:** This channel is protected and cannot be closed or deleted!", ephemeral=True)
            return
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

def build_staff_modpanel_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    is_guest = (guild is not None and not is_cvs_guild(guild))
    if not is_guest:
        desc = (
            "Centralized server moderation, security, and administration console.\n\n"
            "**Member Discipline:**\n"
            "• Warn, Timeout, Kick *(Admin Only)*, Ban *(Admin Only)*, Purge\n\n"
            "**Channel & Server Security:**\n"
            "• Lock, Unlock, Slowmode, Server Lockdown *(Admin Only)*\n\n"
            "**Store & Billing:**\n"
            "• Create Invoice, Add Order, Order Stats, DM Member\n\n"
            "**Panels & Shop Controls:**\n"
            "• Refresh Store, Refresh Tickets, Refresh Hub, Server Info\n"
            "• 🟢 Open Shop / 🔴 Close Shop"
        )
    else:
        desc = (
            "Centralized server moderation, security, and administration console.\n\n"
            "**Member Discipline:**\n"
            "• Warn, Timeout, Kick *(Admin Only)*, Ban *(Admin Only)*, Purge\n\n"
            "**Channel & Server Security:**\n"
            "• Lock, Unlock, Slowmode, Server Lockdown *(Admin Only)*\n\n"
            "**Server & Ticket Tools:**\n"
            "• Refresh Tickets, Direct Message Member, Server Info"
        )
    embed = discord.Embed(
        title="🎛️ Staff Control Center",
        description=desc,
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="AIO Bot • Staff Control Center")
    return embed


def build_ticket_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Support & Order Tickets",
        description=(
            "Need help, have a question, or want to place an order?\n\n"
            "Click **Open a Ticket** below to create a private support channel."
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name="Guidelines",
        value=(
            "• Please describe your request or order upon opening\n"
            "• Staff will respond as quickly as possible\n"
            "• One active ticket per member at a time"
        ),
        inline=False
    )
    embed.set_footer(text="AIO Bot • Support Center")
    return embed


def build_rules_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    guild_name = guild.name if guild else "Community"
    embed = discord.Embed(
        title=f"📜 {guild_name} • Official Server Rules",
        description=(
            "> Welcome to the server! Please take a moment to read and follow our community guidelines to ensure a safe, respectful, and enjoyable experience for everyone.\n\n"
            "*By remaining in this server, you agree to follow all rules listed below.*"
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="1️⃣ Respect & Civility",
        value="Treat all members and staff with respect. Harassment, hate speech, slurs, toxicity, personal attacks, and excessive profanity are strictly prohibited.",
        inline=False
    )
    embed.add_field(
        name="2️⃣ No Spam or Advertising",
        value="No spamming, copypastas, mass-mentioning, or self-promotion. Unsolicited direct messaging (DM advertising) to members will result in an immediate ban.",
        inline=False
    )
    embed.add_field(
        name="3️⃣ Store & Ticket Etiquette",
        value="Open order and support tickets only when you are ready to make a purchase or require help. Please do not create troll tickets or spam staff pings.",
        inline=False
    )
    embed.add_field(
        name="4️⃣ Account & Delivery Security",
        value="Do not publicly post account credentials, passwords, or one-time codes in public channels. Keep all transaction details inside your private order ticket.",
        inline=False
    )
    embed.add_field(
        name="5️⃣ Use Appropriate Channels",
        value="Keep discussions in their designated channels (e.g. general discussion in `#💬-general-chat`, bot commands in `#🤖-bot-commands`, feedback in `#⭐-vouches`).",
        inline=False
    )
    embed.add_field(
        name="6️⃣ Discord Terms of Service",
        value="All members must abide by the official [Discord Terms of Service](https://discord.com/terms) and [Community Guidelines](https://discord.com/guidelines).",
        inline=False
    )
    embed.add_field(
        name="⚖️ Enforcement & Staff Discretion",
        value="Staff members and administrators reserve the right to warn, timeout, kick, or ban any member who violates these rules or disrupts the server.",
        inline=False
    )

    embed.set_footer(text=f"{guild_name} • Thank you for being a valued part of our community!")
    return embed


class StaffModPanelButtonView(discord.ui.View):
    """Persistent button-driven moderation, billing, and channel control center."""
    def __init__(self, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        self.guild = guild
        if guild is not None and not is_cvs_guild(guild):
            store_button_ids = {
                "modpanel_invoice",
                "modpanel_addorder",
                "modpanel_orderstats",
                "modpanel_refresh_food",
                "modpanel_refresh_coupon",
                "modpanel_open_shop",
                "modpanel_close_shop",
                "modpanel_clear_slash_dupes"
            }
            self._children = [item for item in self._children if getattr(item, "custom_id", None) not in store_button_ids]
            for item in self._children:
                cid = getattr(item, "custom_id", "")
                if cid in ("modpanel_warn", "modpanel_timeout", "modpanel_kick", "modpanel_ban", "modpanel_purge"):
                    item.row = 0
                elif cid in ("modpanel_lock", "modpanel_unlock", "modpanel_slowmode", "modpanel_lockdown"):
                    item.row = 1
                elif cid in ("modpanel_refresh_tickets", "modpanel_dm", "modpanel_serverinfo"):
                    item.row = 2

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
        embed.set_footer(text="AIO Bot • Moderation")
        await interaction.followup.send(embed=embed)

    # --- ROW 2: STORE, BILLING & ROLES ---
    @discord.ui.button(label="Create Invoice", style=discord.ButtonStyle.success, emoji="💵", custom_id="modpanel_invoice", row=2)
    async def btn_invoice(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store & billing features are not enabled in this server.", ephemeral=True)
            return
        await interaction.response.send_modal(ModInvoiceModal())

    @discord.ui.button(label="Add Order", style=discord.ButtonStyle.success, emoji="➕", custom_id="modpanel_addorder", row=2)
    async def btn_addorder(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store & billing features are not enabled in this server.", ephemeral=True)
            return
        await interaction.response.send_modal(ModAddOrderModal())

    @discord.ui.button(label="Order Stats", style=discord.ButtonStyle.primary, emoji="📈", custom_id="modpanel_orderstats", row=2)
    async def btn_orderstats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store & billing features are not enabled in this server.", ephemeral=True)
            return
        embed = build_order_stats_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="DM Member", style=discord.ButtonStyle.primary, emoji="📬", custom_id="modpanel_dm", row=2)
    async def btn_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModDMModal())

    @discord.ui.button(label="Refresh Store", style=discord.ButtonStyle.success, emoji="🌮", custom_id="modpanel_refresh_food", row=2)
    async def btn_refresh_food(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store features are not enabled in this server.", ephemeral=True)
            return
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can refresh the store channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        ch = find_food_rewards_channel(guild)
        if ch:
            if ch.name != "🌮-food-rewards" and ("pizza" in ch.name.lower() or "🌮🍕" in ch.name):
                try:
                    await ch.edit(name="🌮-food-rewards", topic="Official rewards store — new methods coming soon!", reason="Updated food store channel name")
                except Exception:
                    pass
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
        ch = find_ticket_panel_channel(guild)
        if ch:
            name = await refresh_channel_content(ch, interaction.user.id)
            await interaction.followup.send(f"✅ Refreshed ticket panel in {ch.mention}!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Could not find ticket panel channel in this server.", ephemeral=True)

    @discord.ui.button(label="Refresh Hub", style=discord.ButtonStyle.primary, emoji="🛒", custom_id="modpanel_refresh_coupon", row=3)
    async def btn_refresh_coupon(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Coupon hub features are not enabled in this server.", ephemeral=True)
            return
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

    @discord.ui.button(label="Open Shop", style=discord.ButtonStyle.success, emoji="🟢", custom_id="modpanel_open_shop", row=3)
    async def btn_open_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store features are not enabled in this server.", ephemeral=True)
            return
        await interaction.response.send_modal(ModOpenShopModal())

    @discord.ui.button(label="Close Shop", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="modpanel_close_shop", row=3)
    async def btn_close_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ Store features are not enabled in this server.", ephemeral=True)
            return
        await interaction.response.send_modal(ModCloseShopModal())

    @discord.ui.button(label="Clear Slash Dupes", style=discord.ButtonStyle.secondary, emoji="🧹", custom_id="modpanel_clear_slash_dupes", row=4)
    async def btn_clear_slash_dupes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_cvs_guild(interaction.guild):
            await interaction.response.send_message("⛔ This feature is not enabled in this server.", ephemeral=True)
            return
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("⛔ **Admin Only**: Only Server Founders and Administrators can sync commands.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            if is_cvs_guild(guild):
                await bot.tree.sync(guild=guild)
            else:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            await bot.tree.sync()
            await interaction.followup.send(
                "✅ **Slash Commands Successfully Synced!**\n"
                "• Private guild tools synced to authorized servers.\n"
                "• Global moderation and ticket commands updated.\n"
                "• *(Tip: If duplicate commands still show in your Discord client, press `Ctrl+R` or restart Discord to refresh the client cache.)*",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error syncing slash commands: `{e}`", ephemeral=True)


# --- SERVER-ISOLATED ACCOUNT & COUPON DISPENSER UI ---

class AddAccountModal(discord.ui.Modal, title="Stock Dispenser Accounts"):
    accounts_input = discord.ui.TextInput(
        label="Account & Coupon Details",
        placeholder="Paste account info, phone, barcodes, and coupons...\nSeparate multiple accounts with '---' or blank lines.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        entries = parse_dispenser_entries(self.accounts_input.value)
        if not entries:
            await interaction.response.send_message("❌ No valid accounts provided.", ephemeral=True)
            return
        added = add_guild_dispenser_accounts(self.guild_id, entries, interaction.user)
        stats = get_guild_dispenser_stats(self.guild_id)
        embed = discord.Embed(
            title="✅ Dispenser Stock Added",
            description=f"Successfully loaded **{added}** new account(s) into this server's dispenser pool!",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="📦 Available In Stock", value=f"**{stats['available']}** accounts", inline=True)
        embed.add_field(name="🏷️ Total Stocked", value=f"**{stats['total']}** accounts", inline=True)
        embed.set_footer(text="AIO Bot • Account Dispenser")
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_dispenser_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    guild_name = guild.name if guild else "Server"
    embed = discord.Embed(
        title=f"🛒 {guild_name} • CVS Accounts & Coupons Store",
        description=(
            "Welcome to our server's CVS account and coupon store!\n\n"
            "**How to Purchase:**\n"
            "• Accounts are sold directly by server staff.\n"
            "• Click **🛒 How to Buy** below or open an order ticket to complete your purchase!\n"
            "• Staff will verify your order and dispense your account directly to you.\n\n"
            "💡 *Click **📦 View Stock** below to check live inventory!*"
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"{guild_name} • Account Store • Open a ticket to buy")
    return embed


class ServerDispenserLaunchView(discord.ui.View):
    """Persistent button view for the public account store / dispenser."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="How to Buy", style=discord.ButtonStyle.success, emoji="🛒", custom_id="dispenser_buy_btn", row=0)
    async def btn_buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        ch = find_ticket_panel_channel(guild)
        ch_mention = ch.mention if ch else "the support ticket channel"
        embed = discord.Embed(
            title="🛒 How to Purchase an Account",
            description=(
                "All accounts are for sale and delivered securely by server staff upon payment.\n\n"
                f"**Step 1:** Head to {ch_mention} and open an order ticket.\n"
                "**Step 2:** Let staff know which account/coupon bundle you want.\n"
                "**Step 3:** Once confirmed, staff will instantly dispense your account details directly to you!"
            ),
            color=COLOR_PRIMARY
        )
        embed.set_footer(text="AIO Bot • Account Store")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="View Stock", style=discord.ButtonStyle.secondary, emoji="📦", custom_id="dispenser_stock_btn", row=0)
    async def btn_stock(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        stats = get_guild_dispenser_stats(guild.id)
        embed = discord.Embed(
            title=f"📦 {guild.name} • Dispenser Stock",
            color=COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Available In Stock", value=f"**{stats['available']}** accounts", inline=True)
        embed.add_field(name="Total Sold / Dispensed", value=f"**{stats['dispensed']}** accounts", inline=True)
        embed.add_field(name="Total Loaded", value=f"**{stats['total']}** accounts", inline=True)
        embed.set_footer(text="AIO Bot • Account Store")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Legacy claim button fallback - prevent any member from taking free accounts
    @discord.ui.button(label="Claim Account", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="dispenser_claim_btn", row=0)
    async def btn_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = find_ticket_panel_channel(interaction.guild)
        ch_mention = ch.mention if ch else "the support ticket channel"
        await interaction.response.send_message(
            f"⛔ **Accounts are for sale only!** Members cannot freely claim accounts.\n"
            f"Please open an order ticket in {ch_mention} to purchase an account from staff.",
            ephemeral=True
        )


class DispensedAccountView(discord.ui.View):
    """Interactive view attached to dispensed account embeds allowing customer or staff to delete once used."""
    def __init__(self, buyer_id: Optional[int] = None, account_id: Optional[int] = None, guild_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.account_id = account_id
        self.guild_id = guild_id

    @discord.ui.button(label="Mark as Used", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="dispense_mark_used_btn")
    async def btn_mark_used(self, interaction: discord.Interaction, button: discord.ui.Button):
        acc_id = self.account_id
        buyer_id = self.buyer_id

        # If not set in instance (e.g. persistent view across restart), extract from message
        if interaction.message and interaction.message.embeds:
            emb = interaction.message.embeds[0]
            if not acc_id and emb.title and "#" in emb.title:
                m = re.search(r'#(\d+)', emb.title)
                if m:
                    try:
                        acc_id = int(m.group(1))
                    except Exception:
                        pass
            if not buyer_id:
                for f in emb.fields:
                    if f.name and "customer" in f.name.lower():
                        m_user = re.search(r'<@!?(\d+)>', f.value)
                        if m_user:
                            try:
                                buyer_id = int(m_user.group(1))
                            except Exception:
                                pass

        is_staff = is_staff_member(interaction.user) or is_admin_member(interaction.user)
        is_buyer = (buyer_id is not None and interaction.user.id == buyer_id)

        if not is_staff and not is_buyer and buyer_id is not None:
            await interaction.response.send_message(
                "⛔ **Access Denied**: Only the customer who received this account or server staff can mark it as used.",
                ephemeral=True
            )
            return

        # Clean from database so dispensed accounts are not saved permanently
        guild = interaction.guild
        gid = str(self.guild_id or (guild.id if guild else ""))
        if gid and gid in server_dispensers_db:
            accs = server_dispensers_db[gid].get("accounts", [])
            if acc_id:
                server_dispensers_db[gid]["accounts"] = [a for a in accs if a.get("id") != acc_id]
            else:
                server_dispensers_db[gid]["accounts"] = [a for a in accs if not a.get("dispensed", False)]
            save_server_dispensers()

        # Delete message from ticket
        try:
            await interaction.message.delete()
            await interaction.response.send_message(
                "✅ **Account marked as used!** Credentials have been permanently deleted.",
                ephemeral=True
            )
            try:
                await interaction.channel.send(f"🗑️ *Account marked as used by {interaction.user.mention}.*")
            except Exception:
                pass
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Could not delete message: {e}", ephemeral=True)


async def refresh_channel_content(channel: discord.TextChannel, author_id: int, clear_history: bool = True) -> str:
    """
    Clears channel messages (if clear_history=True) and posts the latest
    updated embed/buttons for recognized blueprint channels (Food Rewards, Ticket Panel, Coupon Optimizer, Mod Panel, Shop Status).
    """
    if is_protected_channel(channel):
        raise ValueError("Protected channel (#form-automation) cannot be reset or cleared.")

    if clear_history:
        try:
            await channel.purge(limit=100)
        except Exception as e:
            print(f"⚠️ Notice on channel purge #{channel.name}: {e}", file=sys.stderr)

    ch_name = channel.name.lower()

    # 1. Dedicated Shop Status channel check - strictly NEVER post support ticket panels here
    if any(k in ch_name for k in ("shop-open", "shop-closed", "shop-status", "store-status")) or (("🟢" in ch_name or "🔴" in ch_name) and any(k in ch_name for k in ("shop", "status", "store"))):
        is_open = "open" in ch_name or "🟢" in ch_name
        shop_ch = find_food_rewards_channel(channel.guild)
        shop_mention = shop_ch.mention if shop_ch else "#🌮-food-rewards"
        status_embed = build_shop_status_embed(
            is_open=is_open,
            author_name="Server Staff",
            shop_ch_mention=shop_mention
        )
        await channel.send(embed=status_embed)
        return f"Shop Status ({'OPEN 🟢' if is_open else 'CLOSED 🔴'})"

    elif "food" in ch_name or "rewards" in ch_name:
        if channel.name != "🌮-food-rewards" and ("pizza" in ch_name or "🌮🍕" in channel.name):
            try:
                await channel.edit(name="🌮-food-rewards", topic="Official rewards store — new methods coming soon!")
            except Exception:
                pass
        food_embed = build_food_accounts_embed()
        await channel.send(embed=food_embed, view=FoodAccountPurchaseView())
        return "🛍️ Rewards Store (Coming Soon)"

    elif ("ticket" in ch_name or "open-a-ticket" in ch_name) and not any(k in ch_name for k in ("shop", "status", "store", "log", "mod", "coupon", "optimizer")):
        panel_embed = build_ticket_panel_embed()
        await channel.send(embed=panel_embed, view=TicketLaunchView())
        return "🎫 Support & Order Ticket Panel"

    elif "coupon" in ch_name or "optimizer" in ch_name:
        hub_embed = build_coupon_hub_embed()
        await channel.send(embed=hub_embed, view=CouponHubLaunchView())
        return "🛒 CVS Coupon Optimizer Hub"

    elif "mod-panel" in ch_name or "modpanel" in ch_name:
        guild = getattr(channel, "guild", None)
        mod_embed = build_staff_modpanel_embed(guild)
        await channel.send(embed=mod_embed, view=StaffModPanelButtonView(guild))
        return "🎛️ Staff Control Center & Moderation Panel"

    elif "dispenser" in ch_name or "free-account" in ch_name:
        guild = getattr(channel, "guild", None)
        disp_embed = build_dispenser_embed(guild)
        await channel.send(embed=disp_embed, view=ServerDispenserLaunchView())
        return "🎁 Account & Coupon Dispenser"

    elif "rule" in ch_name:
        rules_embed = build_rules_embed(getattr(channel, "guild", None))
        await channel.send(embed=rules_embed)
        return "📜 Server Rules & Guidelines"

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
            cat_overwrites[guild.default_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                send_messages_in_threads=False,
                create_public_threads=False,
                create_private_threads=False,
                add_reactions=True,
                read_message_history=True
            )
            cat_overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True
            )
            if founder_role:
                cat_overwrites[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)
            if mod_role:
                cat_overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True)

        if not cat:
            cat = await guild.create_category(cat_name, overwrites=cat_overwrites)
            created_cats += 1
        elif section.get("staff_only") or section.get("private") or section.get("read_only"):
            try:
                await cat.edit(overwrites=cat_overwrites)
            except Exception:
                pass

        for ch_def in section["channels"]:
            ch_name = ch_def["name"]
            ch_type = ch_def["type"]
            is_ch_read_only = bool(section.get("read_only") or ch_def.get("read_only") or is_read_only_channel_name(ch_name))

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
                    if is_ch_read_only:
                        await apply_read_only_overwrites(existing, founder_role, mod_role)
                    # Refresh existing blueprint panel channels with latest info
                    if ch_name in ("📩-open-a-ticket", "🛒-coupon-optimizer", "🌮-food-rewards", "🌮🍕-food-rewards", "🎛️-mod-panel"):
                        try:
                            await refresh_channel_content(existing, author.id, clear_history=clean_old)
                        except Exception as e:
                            print(f"⚠️ Error refreshing existing channel {ch_name}: {e}", file=sys.stderr)
                else:
                    ch_overwrites = {}
                    if section.get("private") or section.get("staff_only"):
                        ch_overwrites = dict(cat_overwrites)
                    elif is_ch_read_only:
                        ch_overwrites = dict(cat_overwrites)
                    new_ch = await guild.create_text_channel(
                        name=ch_name,
                        category=cat,
                        topic=ch_def.get("topic", ""),
                        overwrites=ch_overwrites
                    )
                    created_channels += 1

                    if is_ch_read_only:
                        await apply_read_only_overwrites(new_ch, founder_role, mod_role)

                    if ch_name in ("📩-open-a-ticket", "🛒-coupon-optimizer", "🌮-food-rewards", "🌮🍕-food-rewards", "🎛️-mod-panel"):
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
    food_mention = get_channel_mention(guild, "🌮-food-rewards", get_channel_mention(guild, "🌮🍕-food-rewards", "#🌮-food-rewards"))
    mod_mention = get_channel_mention(guild, "🎛️-mod-panel", "#🎛️-mod-panel")
    fa_mention = get_channel_mention(guild, "form-automation", "#form-automation")

    desc = (
        f"Server layout organized with official categories and channels!\n\n"
        f"• Categories Created: **{created_cats}**\n"
        f"• Channels Positioned: **{created_channels}**\n"
    )
    if clean_old:
        desc += f"• Channels Cleaned: **{deleted_count}** old channels removed\n"
    desc += (
        f"• Safeguard: {fa_mention} is preserved and untouched.\n"
        f"• Private CVS: {opt_mention}\n"
        f"• Support Tickets: {ticket_mention}\n"
        f"• Food Store: {food_mention}\n"
        f"• Staff Panel: {mod_mention}"
    )

    summary_embed = discord.Embed(
        title="🏗️ Server Layout Formatted Successfully",
        description=desc,
        color=COLOR_SUCCESS
    )
    summary_embed.set_footer(text="AIO Bot • Server Architecture")
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
                "Select channels from the dropdown below to delete.\n\n"
                f"• Leftover/Old Channels Detected: **{len(old_channels)}**\n"
                f"• Total Deletable Channels: **{total_channels}** (Page {self.page+1}/{max_pages})\n"
                "• Safeguard: `#form-automation` is strictly protected."
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

        embed.set_footer(text="AIO Bot • Channel Management")
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
            title="🧹 Selected Channels Deleted",
            description=(
                f"Successfully deleted **{deleted}** channel(s) & categories.\n\n"
                "• Safeguard: `#form-automation` is untouched.\n"
                "• Server channels updated!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot • Server Cleanup")

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
            title="🧹 Leftover Channels Purged",
            description=(
                f"Purged **{deleted}** old channel(s) & categories.\n\n"
                "• Safeguard: `#form-automation` is untouched.\n"
                "• Server is now clean and organized!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot • Server Cleanup")

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
                "• Safeguard: `#form-automation` remains 100% protected.\n"
                "• Run `/formatserver` anytime to redeploy the official layout!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot • Server Cleanup")

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
            title="🧹 Channels Deleted",
            description=(
                f"Cleaned up **{deleted}** channel(s) & categories.\n\n"
                "• Safeguard: `#form-automation` is untouched.\n"
                "• Server channels are clean and organized!"
            ),
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot • Server Cleanup")

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
    embed = discord.Embed(
        title=f"📊 Server Information • {guild.name}",
        description="Server statistics and configuration overview.",
        color=COLOR_INFO,
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    owner = guild.owner.mention if guild.owner else f"<@{guild.owner_id}>"
    created_ts = int(guild.created_at.timestamp())
    embed.add_field(name="Owner", value=f"{owner}\n`ID: {guild.id}`", inline=True)
    embed.add_field(name="Created", value=f"<t:{created_ts}:D>\n(<t:{created_ts}:R>)", inline=True)
    embed.add_field(name="Boosts", value=f"Tier {guild.premium_tier}\n{guild.premium_subscription_count} boosts", inline=True)
    embed.add_field(name="Members", value=f"**{guild.member_count:,}**", inline=True)
    embed.add_field(name="Channels", value=f"{len(guild.text_channels)} text • {len(guild.voice_channels)} voice", inline=True)
    embed.add_field(name="Roles & Emojis", value=f"{len(guild.roles)} roles • {len(guild.emojis)} emojis", inline=True)
    embed.set_footer(text="AIO Bot • Server Info", icon_url=guild.icon.url if guild.icon else None)
    return embed

def build_userinfo_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"👤 Member Profile • {member.display_name}",
        description=f"Identity details and roles for {member.mention}.",
        color=member.color if member.color.value != 0 else COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    badge = " (Bot)" if member.bot else ""
    created_ts = int(member.created_at.timestamp())
    joined_ts = int(member.joined_at.timestamp()) if member.joined_at else None
    joined_str = f"<t:{joined_ts}:D> (<t:{joined_ts}:R>)" if joined_ts else "Unknown"

    embed.add_field(name="Identity", value=f"{member.mention}{badge}\n`{member.id}`", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{created_ts}:D>\n(<t:{created_ts}:R>)", inline=True)
    embed.add_field(name="Server Joined", value=f"{joined_str}", inline=True)
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    role_str = " ".join(roles[:10]) if roles else "*No assigned roles*"
    if len(roles) > 10:
        role_str += f" *(+{len(roles)-10} more)*"
    embed.add_field(name=f"Roles ({len(roles)})", value=role_str, inline=False)
    embed.set_footer(text="AIO Bot • Member Profile", icon_url=member.guild.icon.url if member.guild and member.guild.icon else None)
    return embed

# --- GIVEAWAYS BACKGROUND RUNNER ---
async def finish_giveaway(msg_id_str: str) -> None:
    giveaway = giveaways_db.get(msg_id_str)
    if not giveaway or giveaway.get("ended", False):
        return

    giveaway["ended"] = True
    save_giveaways()

    channel_id = giveaway.get("channel_id")
    channel = bot.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    participants = list(set(giveaway.get("participants", [])))
    winners_count = max(1, giveaway.get("winners_count", 1))
    prize = giveaway.get("prize", "Prize")

    if participants:
        chosen_winners = random.sample(participants, min(winners_count, len(participants)))
    else:
        chosen_winners = []

    giveaway["winners"] = chosen_winners
    save_giveaways()

    if channel:
        try:
            msg = await channel.fetch_message(int(msg_id_str))
        except Exception:
            msg = None

        if chosen_winners:
            winners_pings = ", ".join(f"<@{uid}>" for uid in chosen_winners)
            winners_desc = "\n".join(f"• <@{uid}>" for uid in chosen_winners)
        else:
            winners_pings = "No valid entries."
            winners_desc = "*No one entered this giveaway.*"

        if msg and msg.embeds:
            ended_embed = discord.Embed(
                title=f"🎉 Giveaway Ended: {prize}",
                description=(
                    f"• Prize: **{prize}**\n"
                    f"• Host: <@{giveaway.get('host_id')}>\n\n"
                    f"**Winner(s):**\n{winners_desc}"
                ),
                color=0x2B2D31,
                timestamp=datetime.now(timezone.utc)
            )
            ended_embed.set_footer(text="AIO Bot • Giveaway Concluded")
            disabled_view = GiveawayEntryView(len(participants))
            for child in disabled_view.children:
                child.disabled = True
            try:
                await msg.edit(embed=ended_embed, view=disabled_view)
            except Exception:
                pass

        if chosen_winners:
            try:
                await channel.send(
                    f"🎊 **Congratulations {winners_pings}!** You won the giveaway for **{prize}**! 🎁\n*Please open a ticket or contact staff to claim your prize.*"
                )
            except Exception:
                pass


@tasks.loop(seconds=15)
async def check_giveaways_loop():
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for msg_id_str, g in list(giveaways_db.items()):
        if g.get("ended", False):
            continue
        end_ts = g.get("end_time", 0)
        if now_ts >= end_ts:
            try:
                await finish_giveaway(msg_id_str)
            except Exception as e:
                print(f"⚠️ Error concluding giveaway {msg_id_str}: {e}", file=sys.stderr)


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
        bot.add_view(TicketReviewLaunchView())
        bot.add_view(GiveawayEntryView())
        bot.add_view(ServerDispenserLaunchView())
        bot.add_view(DispensedAccountView())
        print('✅ Persistent interactive views registered successfully.', flush=True)
    except Exception as e:
        print(f"ℹ️ Note on persistent views registration: {e}", file=sys.stderr, flush=True)

    # Start background giveaway monitoring loop
    try:
        if not check_giveaways_loop.is_running():
            check_giveaways_loop.start()
    except Exception as e:
        print(f"⚠️ Note on giveaways background loop: {e}", file=sys.stderr, flush=True)

    # Application slash command tree sync
    try:
        for g in bot.guilds:
            try:
                if is_cvs_guild(g):
                    synced_g = await bot.tree.sync(guild=g)
                    print(f'🔒 Synced {len(synced_g)} private CVS/optimizer commands to authorized server: {g.name} ({g.id})', flush=True)
                else:
                    bot.tree.clear_commands(guild=g)
                    await bot.tree.sync(guild=g)
                    print(f'🧹 Purged private guild commands for guest server: {g.name} ({g.id})', flush=True)
            except Exception as ge:
                print(f'ℹ️ Guild slash sync notice for {g.name}: {ge}', file=sys.stderr, flush=True)

        synced = await bot.tree.sync()
        print(f'🌐 Synced {len(synced)} global application slash command(s) with zero duplicates.', flush=True)
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

    # Auto-repair channels and outdated panels on startup (ONLY for authorized CVS/primary servers)
    for g in bot.guilds:
        if not is_cvs_guild(g):
            continue
        try:
            for ch in g.text_channels:
                cname = ch.name.lower()
                if ("mod" in cname and "log" in cname) and (any(k in cname for k in ("open", "closed", "🟢", "🔴")) or ch.name != "📜-mod-logs"):
                    try:
                        await ch.edit(name="📜-mod-logs", reason="Auto-repairing corrupted mod-logs channel name on startup")
                        print(f"🔧 Auto-repaired channel #{ch.name} -> #📜-mod-logs in {g.name}", flush=True)
                    except Exception as ce:
                        print(f"ℹ️ Could not auto-rename #{ch.name} in {g.name}: {ce}", file=sys.stderr, flush=True)
                elif ("ticket" in cname and "log" in cname) and (any(k in cname for k in ("open", "closed", "🟢", "🔴")) or ch.name != "📁-ticket-logs"):
                    try:
                        await ch.edit(name="📁-ticket-logs", reason="Auto-repairing corrupted ticket-logs channel name on startup")
                        print(f"🔧 Auto-repaired channel #{ch.name} -> #📁-ticket-logs in {g.name}", flush=True)
                    except Exception as ce:
                        print(f"ℹ️ Could not auto-rename #{ch.name} in {g.name}: {ce}", file=sys.stderr, flush=True)
                elif ("receipt" in cname and "brag" in cname) or cname in ("receipt-brags", "🧾-receipt-brags"):
                    vouches_ch = discord.utils.get(g.text_channels, name="⭐-vouches")
                    if not vouches_ch:
                        try:
                            await ch.edit(name="⭐-vouches", topic="Customer vouches, reviews, feedback, and 5-star ratings.", reason="Auto-renaming #receipt-brags to #⭐-vouches on startup")
                            print(f"🔧 Auto-renamed channel #{ch.name} -> #⭐-vouches in {g.name}", flush=True)
                        except Exception as ce:
                            print(f"ℹ️ Could not auto-rename #{ch.name} in {g.name}: {ce}", file=sys.stderr, flush=True)

            # Auto-rename legacy store channel and refresh outdated embeds
            food_ch = find_food_rewards_channel(g)
            if food_ch:
                if food_ch.name != "🌮-food-rewards" and ("pizza" in food_ch.name.lower() or "🌮🍕" in food_ch.name):
                    try:
                        await food_ch.edit(name="🌮-food-rewards", topic="Official rewards store — new methods coming soon!", reason="Auto-updating food store on startup")
                        print(f"🔧 Auto-renamed channel #{food_ch.name} -> #🌮-food-rewards in {g.name}", flush=True)
                    except Exception:
                        pass

                # Scan messages in food_ch for outdated Taco Bell or Pizza Hut mentions and auto-refresh
                try:
                    outdated_found = False
                    async for msg in food_ch.history(limit=10):
                        if msg.author == bot.user and msg.embeds:
                            for emb in msg.embeds:
                                emb_text = f"{emb.title} {emb.description} {' '.join(f.name + ' ' + f.value for f in emb.fields)}".lower()
                                if "taco bell" in emb_text or "chalupa" in emb_text or "pizza" in emb_text or "delivery is preferred" in emb_text:
                                    outdated_found = True
                                    break
                        if outdated_found:
                            break
                    if outdated_found:
                        print(f"🔄 Outdated store embed found in #{food_ch.name} ({g.name}) - auto-refreshing...", flush=True)
                        await refresh_channel_content(food_ch, bot.user.id if bot.user else 0, clear_history=True)
                        print(f"✅ Auto-refreshed store panel in #{food_ch.name} with Coming Soon embed.", flush=True)
                except Exception as fe:
                    print(f"ℹ️ Notice on food store embed scan in {g.name}: {fe}", file=sys.stderr, flush=True)

            # Audit and enforce read-only permissions on announcements, rules, welcome, giveaways, store, etc.
            try:
                locked = await audit_and_enforce_read_only_channels(g)
                if locked:
                    print(f"🔒 Auto-enforced read-only permissions on {len(locked)} board channel(s) in {g.name}: {', '.join('#' + c for c in locked)}", flush=True)
            except Exception as le:
                print(f"ℹ️ Read-only channel audit notice in {g.name}: {le}", file=sys.stderr, flush=True)
        except Exception as ge:
            print(f"ℹ️ Auto-repair channel scan notice in {g.name}: {ge}", file=sys.stderr, flush=True)

    # Auto-leave any unauthorized guilds so the bot only stays in Cody's server
    for g in list(bot.guilds):
        if not is_cvs_guild(g):
            print(f"🚪 Auto-leaving unauthorized guild: {g.name} ({g.id})", flush=True)
            try:
                await g.leave()
            except Exception as e:
                print(f"⚠️ Error leaving unauthorized guild {g.name}: {e}", file=sys.stderr, flush=True)

@bot.event
async def on_guild_join(guild: discord.Guild):
    if not is_cvs_guild(guild):
        print(f"🚪 Auto-leaving unauthorized joined guild: {guild.name} ({guild.id})", flush=True)
        try:
            await guild.leave()
        except Exception as e:
            print(f"⚠️ Error auto-leaving unauthorized guild {guild.name}: {e}", file=sys.stderr, flush=True)

@bot.event
async def on_member_join(member: discord.Member):
    guild = getattr(member, "guild", None)
    if not guild or getattr(member, "bot", False):
        return

    welcome_ch = get_welcome_channel(guild)
    if welcome_ch:
        try:
            embed = build_welcome_embed(member)
            await welcome_ch.send(content=f"👋 Welcome to {guild.name}, {member.mention}!", embed=embed)
        except Exception as e:
            print(f"⚠️ Error sending welcome message for {member}: {e}", file=sys.stderr)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    is_staff = (
        is_staff_or_admin(message.author) or
        getattr(message.author.guild_permissions, "manage_messages", False) or
        getattr(message.author.guild_permissions, "administrator", False)
    )
    channel_protected = is_protected_channel(message.channel)

    # Auto-Mod Protection Shield (Non-staff & Non-protected channels)
    if not is_staff and not channel_protected:
        # 1. Discord Invite Link Blocker
        if automod_config_db.get("invites_blocked", True):
            invite_pattern = r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/[a-zA-Z0-9_-]+"
            if re.search(invite_pattern, message.content, re.IGNORECASE):
                try:
                    await message.delete()
                    case_id = log_mod_case(
                        guild_id=message.guild.id,
                        action="AutoMod Invite Blocker",
                        target=str(message.author),
                        moderator="AIO AutoMod 🛡️",
                        reason="Posted unauthorized Discord invite link",
                        details=f"Content: {message.content[:100]}"
                    )
                    await message.channel.send(
                        f"⚠️ {message.author.mention}, posting Discord server invite links is not permitted.",
                        delete_after=6
                    )
                    log_ch = get_mod_logs_channel(message.guild)
                    if log_ch:
                        alert_embed = discord.Embed(
                            title="🛡️ AutoMod: Invite Link Removed",
                            color=COLOR_ERROR,
                            timestamp=datetime.now(timezone.utc)
                        )
                        alert_embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
                        alert_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                        alert_embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
                        alert_embed.add_field(name="Deleted Content", value=f"`{message.content[:300]}`", inline=False)
                        alert_embed.set_footer(text="AIO Bot • AutoMod")
                        await log_ch.send(embed=alert_embed)
                    return
                except Exception as e:
                    print(f"⚠️ Error handling invite block: {e}", file=sys.stderr)

        # 2. Phishing & Scam Link Blocker
        if automod_config_db.get("scams_blocked", True):
            scam_pattern = r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)*[a-zA-Z0-9-]*(?:discorcl|dlscord|disord|discord-app|discord-nitro|nitro-gift|steamcommunlty|steamcommunity-trade|gift-nitro)[a-zA-Z0-9-]*\.[a-zA-Z]{2,}"
            if re.search(scam_pattern, message.content, re.IGNORECASE):
                try:
                    await message.delete()
                    case_id = log_mod_case(
                        guild_id=message.guild.id,
                        action="AutoMod Scam Blocker",
                        target=str(message.author),
                        moderator="AIO AutoMod 🛡️",
                        reason="Posted suspected phishing/Nitro scam link",
                        details=f"Content: {message.content[:100]}"
                    )
                    await message.channel.send(
                        f"🛡️ {message.author.mention}, suspicious or malicious links are strictly prohibited.",
                        delete_after=6
                    )
                    log_ch = get_mod_logs_channel(message.guild)
                    if log_ch:
                        alert_embed = discord.Embed(
                            title="🚨 AutoMod: Scam Link Removed",
                            color=COLOR_ERROR,
                            timestamp=datetime.now(timezone.utc)
                        )
                        alert_embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
                        alert_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                        alert_embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
                        alert_embed.add_field(name="Flagged Link", value=f"`{message.content[:300]}`", inline=False)
                        alert_embed.set_footer(text="AIO Bot • AutoMod")
                        await log_ch.send(embed=alert_embed)
                    return
                except Exception as e:
                    print(f"⚠️ Error handling scam block: {e}", file=sys.stderr)

    # 3. Auto-Mod Word Filter Inspection
    filter_words = get_filter_words(message.guild.id)
    if filter_words and not is_staff and not channel_protected:
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
                        title="🛡️ Filter Triggered",
                        description=f"{message.author.mention}, your message contained a blacklisted word and was removed.",
                        color=COLOR_ERROR
                    )
                    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
                    embed.add_field(name="Warning Count", value=str(len(warnings_db[key])), inline=True)
                    embed.set_footer(text="AIO Bot • AutoMod")
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
            title="👻 Ghost Ping Detected",
            description="A message mentioning members was deleted.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Pinged Users", value=" ".join(m.mention for m in actual_mentions), inline=True)
        embed.add_field(name="Message Content", value=message.content[:500] if message.content else "*[No text content]*", inline=False)
        embed.set_footer(text="AIO Bot • AutoMod")
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
    embed = build_staff_modpanel_embed(ctx.guild)
    view = StaffModPanelButtonView(ctx.guild)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="help", description="Show the AIO Bot interactive help menu")
async def help_command(ctx):
    await safely_delete_message(ctx)
    author_perms = ctx.channel.permissions_for(ctx.author) if ctx.guild else discord.Permissions.none()
    is_owner = is_primary_bot_owner(ctx.author) or await bot.is_owner(ctx.author)
    is_cvs = is_cvs_guild(ctx.guild)
    embed = discord.Embed(
        title="📖 AIO Bot — Command Center",
        description="Select a category from the dropdown menu below to view command guides.",
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    if is_cvs:
        embed.add_field(name="Coupon Optimizer", value="• Optimal checkout bundles & savings.", inline=True)
    embed.add_field(name="Moderation Suite", value="• Anti-raid, filters, cases & staff tools.", inline=True)
    embed.add_field(name="Ticket System", value="• Support tickets, claims & transcripts.", inline=True)
    if is_cvs:
        embed.add_field(name="Games & Economy", value="• Blackjack, Slots, Connect 4, Trivia & bank.", inline=True)
    embed.set_footer(text="AIO Bot • Commands work with / or !")
    view = HelpMenuView(author_perms, is_owner, is_cvs)
    await ctx.send(embed=embed, view=view)

# --- EMBED CREATOR ---

@bot.hybrid_command(
    name="embed",
    aliases=["createembed", "embeddesigner", "richembed"],
    description="Design and broadcast sleek, modern rich embeds to any channel"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    channel="Target channel to send the embed to (defaults to current channel)",
    title="Optional embed title (leave blank to open interactive designer modal)",
    description="Optional embed body text (supports markdown)",
    color="Theme color preset or hex code (e.g. purple, gold, cyan, #ff007f)",
    image="Optional large banner/hero image URL",
    thumbnail="Optional top-right thumbnail image URL",
    footer="Optional footer note",
    ping="Optional announcement ping"
)
@app_commands.choices(
    color=[
        app_commands.Choice(name="🟣 Cyber Violet", value="purple"),
        app_commands.Choice(name="⚡ Electric Blue", value="blue"),
        app_commands.Choice(name="🟢 Emerald Green", value="green"),
        app_commands.Choice(name="🟡 Luxury Gold", value="gold"),
        app_commands.Choice(name="🔴 Crimson Flame", value="red"),
        app_commands.Choice(name="⚫ Midnight Stealth", value="dark gray"),
        app_commands.Choice(name="🌸 Neon Pink", value="pink"),
        app_commands.Choice(name="🌊 Cyan Wave", value="cyan"),
    ],
    ping=[
        app_commands.Choice(name="None", value="none"),
        app_commands.Choice(name="📢 @everyone", value="@everyone"),
        app_commands.Choice(name="🔔 @here", value="@here"),
    ]
)
async def create_embed_cmd(
    ctx: commands.Context,
    channel: Optional[discord.TextChannel] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    color: Optional[str] = None,
    image: Optional[str] = None,
    thumbnail: Optional[str] = None,
    footer: Optional[str] = None,
    ping: Optional[str] = None
):
    target_channel = channel or ctx.channel
    if is_protected_channel(target_channel):
        await ctx.send("🛡️ Cannot send embeds to protected channels.", delete_after=6)
        return

    # If title and description are omitted and called via slash command, open the sleek visual modal
    if ctx.interaction and not (title and description):
        modal = EmbedBuilderModal(target_channel, ping=ping if ping in ("@everyone", "@here") else None)
        await ctx.interaction.response.send_modal(modal)
        return

    # Otherwise, build and send the embed directly
    await safely_delete_message(ctx)
    if not title or not description:
        await ctx.send("🎨 **Tip:** Provide both `title` and `description` to post directly, or run `/embed` with no arguments to open the interactive designer modal!", delete_after=8)
        return

    embed = build_custom_rich_embed(
        title=title,
        description=description,
        author=ctx.author,
        color_input=color,
        thumbnail_url=thumbnail,
        image_url=image,
        footer_text=footer,
        guild=ctx.guild
    )

    content = ping if ping in ("@everyone", "@here") else None
    try:
        sent_msg = await target_channel.send(content=content, embed=embed)
        card = build_embed_success_card(target_channel, sent_msg, embed, content)
        view = EmbedSuccessView(sent_msg.jump_url)
        await ctx.send(embed=card, view=view, ephemeral=True, delete_after=12 if not ctx.interaction else None)
    except discord.HTTPException as e:
        await ctx.send(f"❌ Failed to broadcast embed: {e}", delete_after=8)

# --- MODERATION & CHANNEL CONTROLS ---

@bot.hybrid_command(name="nukechannel", aliases=["nuke", "nuke-channel", "clonewipe"], description="Duplicates and replaces this channel to completely wipe it clean")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(channel="Text channel to wipe and recreate (defaults to current channel)")
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
        description=f"Channel wiped and recreated by {ctx.author.mention}.",
        color=COLOR_ERROR,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Channel", value=f"#{new_channel.name}", inline=True)
    embed.add_field(name="Time", value=f"<t:{ts}:R>", inline=True)
    embed.set_footer(text="AIO Bot • Channel Control")
    embed.set_image(url="https://media.giphy.com/media/HhTXt43zEJbNYTX32f/giphy.gif")
    await new_channel.send(embed=embed)

    # If the nuked channel was a recognized blueprint panel, restore its interactive panel
    try:
        await refresh_channel_content(new_channel, ctx.author.id, clear_history=False)
    except Exception:
        pass

@bot.hybrid_command(name="purge", aliases=["clean", "clear_messages", "prune"], description="Bulk delete messages (optional member or channel filter)")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    amount="Number of messages to delete (1-100)",
    member="Optional user filter to only delete their messages",
    channel="Optional channel to purge (defaults to current channel)"
)
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
@app_commands.describe(
    user="User mention, username, or User ID",
    message="The direct message text to send",
    anonymous="Send anonymously as server staff (defaults to False)"
)
async def dm_command(ctx: commands.Context, user: str, *, message: str, anonymous: Optional[bool] = False):
    await safely_delete_message(ctx)
    guild = ctx.guild
    target = await resolve_user_or_member(guild, user.strip())
    if not target:
        await ctx.send(f"❌ Could not find user `{user}`. Please provide a valid mention (@user), username, or User ID.", delete_after=8)
        return

    msg_text = message.strip()
    embed = discord.Embed(
        title=f"📬 Message from {guild.name if guild else 'Server Staff'}",
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
    embed.set_footer(text="AIO Bot • Direct Message")

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
    confirm_embed.set_footer(text="AIO Bot • Moderation")
    await ctx.send(embed=confirm_embed, delete_after=12)

@bot.hybrid_command(name="kick", description="Kick a member from the server")
@commands.guild_only()
@commands.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
@app_commands.describe(member="The server member to kick", reason="Reason for kicking the member")
async def kick_member(ctx, member: discord.Member, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot kick a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.kick(reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Kick", str(member), str(ctx.author), reason or "No reason provided")
        embed = discord.Embed(
            title="👢 Member Kicked",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="AIO Bot • Moderation")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to kick this user.", delete_after=6)

@bot.hybrid_command(name="ban", description="Ban a member from the server")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
@app_commands.describe(
    member="The server member to ban",
    delete_message_days="Number of days of message history to delete (0-7)",
    reason="Reason for banning the member"
)
async def ban_member(ctx, member: discord.Member, delete_message_days: Optional[int] = 0, *, reason: Optional[str] = "No reason provided"):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot ban a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.ban(delete_message_days=min(delete_message_days or 0, 7), reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Ban", str(member), str(ctx.author), reason or "No reason provided", f"Purged {delete_message_days}d messages")
        embed = discord.Embed(
            title="🔨 Member Banned",
            color=COLOR_ERROR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="AIO Bot • Moderation")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to ban this user.", delete_after=6)

@bot.hybrid_command(name="unban", description="Unban a user by their user ID or Username#1234")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
@app_commands.describe(user_query="User ID or Username#1234 of the banned user")
async def unban_user(ctx, *, user_query: str):
    await safely_delete_message(ctx)
    clean_query = user_query.strip().lstrip("<@!").rstrip(">")
    bans = [entry async for entry in ctx.guild.bans()]
    target_user = None

    for ban_entry in bans:
        u = ban_entry.user
        if (
            str(u.id) == clean_query
            or u.name.lower() == clean_query.lower()
            or f"{u.name}#{u.discriminator}" == clean_query
            or (hasattr(u, "global_name") and u.global_name and u.global_name.lower() == clean_query.lower())
        ):
            target_user = u
            break

    if not target_user:
        await ctx.send(f"❌ Could not find a banned user matching `{user_query}`.", delete_after=6)
        return

    try:
        await ctx.guild.unban(target_user, reason=f"Unbanned by {ctx.author}")
        case_id = log_mod_case(ctx.guild.id, "Unban", str(target_user), str(ctx.author), "Unbanned user")
        embed = discord.Embed(
            title="🕊️ Member Unbanned",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User", value=f"**{target_user}** (`{target_user.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        embed.set_footer(text="AIO Bot • Moderation")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot lacks permission to unban users.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error unbanning user: {e}", delete_after=6)

@bot.hybrid_command(name="timeout", aliases=["mute"], description="Timeout/mute a member for a set duration (e.g. 5m, 1h, 1d)")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(
    member="The server member to timeout",
    duration="Duration of timeout (e.g. 10m, 2h, 1d)",
    reason="Reason for the timeout"
)
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
        embed = discord.Embed(
            title="🔇 Member Timed Out",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="AIO Bot • Moderation")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to timeout this user.", delete_after=6)

@bot.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove timeout from a member")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
@app_commands.describe(member="The server member to remove timeout from")
async def untimeout_member(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send("⛔ You cannot untimeout a member with an equal or higher role than you.", delete_after=6)
        return
    try:
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        case_id = log_mod_case(ctx.guild.id, "Untimeout", str(member), str(ctx.author), "Timeout removed")
        embed = discord.Embed(
            title="🔊 Timeout Removed",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
        embed.set_footer(text="AIO Bot • Moderation")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Bot is missing permissions to untimeout this user.", delete_after=6)

@bot.hybrid_command(name="warn", description="Issue an official warning to a member")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="The server member to warn", reason="Reason for the official warning")
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
    embed = discord.Embed(
        title="⚠️ Warning Issued",
        color=COLOR_WARN,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Warning Count", value=f"#{count}", inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="AIO Bot • Moderation")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="warnings", description="View warnings logged for a member")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="The server member whose warnings to view")
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
@app_commands.describe(member="The server member whose warnings to clear")
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
@app_commands.describe(channel="Channel to lock (defaults to current channel)")
async def lock_channel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    if is_protected_channel(target):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` is strictly protected and cannot be locked.", delete_after=6)
        return
    try:
        await target.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Locked by {ctx.author}")
        await ctx.send(f"🔒 **{target.mention}** is now locked.", delete_after=6)
    except discord.Forbidden:
        await ctx.send(f"❌ Bot is missing permissions to lock {target.mention}.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error locking {target.mention}: {e}", delete_after=6)

@bot.hybrid_command(name="unlock", description="Unlock a channel to allow regular members to send messages")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to unlock (defaults to current channel)")
async def unlock_channel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    if is_protected_channel(target):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` is strictly protected and cannot be modified.", delete_after=6)
        return
    try:
        await target.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Unlocked by {ctx.author}")
        await ctx.send(f"🔓 **{target.mention}** is now unlocked.", delete_after=6)
    except discord.Forbidden:
        await ctx.send(f"❌ Bot is missing permissions to unlock {target.mention}.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error unlocking {target.mention}: {e}", delete_after=6)

@bot.hybrid_command(name="slowmode", description="Set channel slowmode cooldown (e.g. 5s, 1m, 0)")
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(
    duration="Cooldown duration (e.g. 5s, 30s, 2m, 0 to disable)",
    channel="Target channel (defaults to current channel)"
)
async def set_slowmode(ctx, duration: str, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    if is_protected_channel(target):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` is strictly protected and slowmode cannot be changed.", delete_after=6)
        return
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

    try:
        await target.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author}")
        if seconds == 0:
            await ctx.send(f"⚡ Slowmode disabled for {target.mention}.", delete_after=6)
        else:
            await ctx.send(f"⏳ Set slowmode for {target.mention} to **{seconds}s**.", delete_after=6)
    except discord.Forbidden:
        await ctx.send(f"❌ Bot is missing permissions to edit slowmode on {target.mention}.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error updating slowmode on {target.mention}: {e}", delete_after=6)

@bot.hybrid_command(name="serverinfo", description="Display detailed server stats and information")
@commands.guild_only()
async def server_info(ctx):
    await safely_delete_message(ctx)
    embed = build_serverinfo_embed(ctx.guild)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo", description="Display detailed member information")
@commands.guild_only()
@app_commands.describe(member="The server member whose information to view (defaults to yourself)")
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
        title="🧾 Step-by-Step Checkout Strategy",
        description=f"Split items into **{tx_count} transaction(s)** for maximum register savings.",
        color=COLOR_SUCCESS if total_savings > 0 else 0x3498DB,
        timestamp=datetime.now(timezone.utc)
    )

    if not coupons:
        item_lines = "\n".join([f"• **{i['name']}**: `${i['price']:.2f}`" for i in items])
        embed.add_field(
            name="Single Transaction (No Coupons)",
            value=f"{item_lines}\n\n**Total Due: ${total_due:.2f}**",
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
                    name=f"Step {idx+1}: {len(group_items)} Item(s)",
                    value=(
                        f"**Items:** {item_bullets}\n"
                        f"**Scan:** `{coupon_label(coupon_val)}`\n"
                        f"Subtotal: `${group_sub:.2f}` ➔ **Due: ${due:.2f}** *(Saved ${saved_amt:.2f})*"
                    ),
                    inline=False
                )

    # Cart Summary
    embed.add_field(
        name="Checkout Summary",
        value=(
            f"• Retail Subtotal: `${full_subtotal:.2f}`\n"
            f"• Total Discounts: `-${total_savings:.2f}` ({savings_pct:.0f}% OFF)\n"
            f"• **Total Due at Register: ${total_due:.2f}**"
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
                f"➔ Due: **${trial_due:.2f}** *(+${best_net_benefit:.2f} net)*"
            )
            working_coupons.append(best_candidate)
            working_due = trial_due

        if suggestions:
            embed.add_field(
                name="Extra Savings Opportunities",
                value="Save even more by purchasing these coupons:\n" + "\n".join(suggestions),
                inline=False
            )

    embed.set_footer(text="AIO Bot • Click Checkout below to save trip")
    return embed

# --- COUPON OPTIMIZER COMMANDS ---

@bot.hybrid_command(name="add", description="Add items to your cart (e.g. Fairlife Milk 4.49, Pantene Shampoo 6.59)")
@app_commands.describe(items="Item name and price pairs separated by commas (e.g. Fairlife Milk 4.49, Shampoo 6.59)")
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
@app_commands.describe(values="Space-separated coupon dollar values or 'half' (e.g. 8 8 5 half)")
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
        title="🧠 Calculating Strategy...",
        description=f"*Analyzing **{len(items)} items** (${sum(i['price'] for i in items):.2f}) and **{len(coupons)} coupons**...*\n\n`[████████░░] Finding optimal bundles...`",
        color=COLOR_INFO
    )
    msg = await ctx.send(embed=thinking_embed)
    await asyncio.sleep(1.4)

    embed = build_strategy_embed(items, coupons)
    view = QuickCartActionView(ctx.author.id)
    await msg.edit(embed=embed, view=view)

@bot.hybrid_command(name="calc", aliases=["quickcalc"], description="Instant 1-step calculation without saving a cart (e.g. Fairlife 4.49, Shampoo 6.59 | 8 5)")
@app_commands.describe(query="Items and prices, followed by | and coupon values (e.g. Fairlife 4.49, Shampoo 6.59 | 8 5)")
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
        title="🧮 Calculating Strategy...",
        description=f"*Parsing & optimizing **{len(items)} items** with **{len(coupons)} coupons**...*",
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
@app_commands.describe(item_name="Name of the item to remove from your cart")
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

    embed, subtotal, total_due, coupon_spend, net_saved, now = _do_checkout(items, coupons, user_id=ctx.author.id, user_name=ctx.author.display_name)
    await ctx.send(embed=embed)

    # Send DM receipt
    try:
        item_str   = "\n".join(f"• **{i['name']}**: ${i['price']:.2f}" for i in items[:15]) or "No items."
        if len(items) > 15:
            item_str += f"\n*...and {len(items)-15} more items*"
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None"
        dm = discord.Embed(title="🧾 Trip Receipt", color=COLOR_SUCCESS)
        dm.description = (
            f"🗓️ **{now.strftime('%A, %b %d, %Y @ %I:%M %p')}**\n"
            f"💰 **Net Saved:** **${net_saved:.2f}**"
        )
        dm.add_field(name="🏷️ Full Retail",   value=f"${subtotal:.2f}",     inline=True)
        dm.add_field(name="💵 Register Paid", value=f"${total_due:.2f}",     inline=True)
        dm.add_field(name="🎟️ Coupon Cost",   value=f"${coupon_spend:.2f}",  inline=True)
        dm.add_field(name="🛒 Items Purchased", value=item_str, inline=False)
        if coupons:
            dm.add_field(name="🎟️ Coupons Used", value=coupon_str, inline=False)
        dm.set_footer(text="AIO Bot • Savings Tracker")
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
@app_commands.describe(num_items="Number of simulated cart items (4-30, defaults to 16)", num_coupons="Number of simulated coupons (1-10, defaults to 5)")
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
        title="⚡ Optimizer Benchmark",
        description="Initializing combinatorial benchmark engine...",
        color=COLOR_INFO
    )
    embed.add_field(
        name="Phase 1 • Cart Synthesis",
        value=f"Synthesizing **{n_items} items** (${full_price:.2f}) & **{len(test_coupons)} coupons**\n`[██░░░░░░░░] 20%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot • Benchmark (Phase 1/4)")
    msg = await ctx.send(embed=embed)

    await asyncio.sleep(1.4)

    # Step 2: Pruning Tree & Combinatorial Permutations
    embed.description = "Constructing branch-and-bound pruning tree & bounding constraints..."
    embed.set_field_at(
        0,
        name="Phase 2 • Exploration Graph",
        value=f"Mapping tree with **{2**min(n_items, 14):,} permutations** across {len(test_coupons)} coupon groups\n`[█████░░░░░] 50%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot • Benchmark (Phase 2/4)")
    await msg.edit(embed=embed)

    await asyncio.sleep(1.4)

    # Step 3: Real benchmark computation
    start_time = time.perf_counter()
    total_due, bundling = calculate_best_bundles(test_items, test_coupons)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    saved = full_price - total_due
    pct = (saved / full_price * 100) if full_price > 0 else 0

    embed.description = "Evaluating multi-pass backtracking algorithms & cache efficiency..."
    embed.set_field_at(
        0,
        name="Phase 3 • Combinatorial Benchmark Execution",
        value=f"⚡ **Latency Measured:** `{elapsed_ms:.2f} ms`\n🎟️ **Active Bundles:** {len(bundling)} optimal registers created\n`[████████░░] 80%`",
        inline=False
    )
    embed.set_footer(text="AIO Bot • Benchmark (Phase 3/4)")
    await msg.edit(embed=embed)

    await asyncio.sleep(1.4)

    # Step 4: Final Comprehensive Telemetry Report
    final_embed = discord.Embed(
        title="⚡ Optimizer Benchmark Results",
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
    final_embed.set_footer(text="AIO Bot • Benchmark Complete")
    await msg.edit(embed=final_embed)

@bot.hybrid_command(name="savings", description="View your lifetime savings stats")
async def view_savings(ctx):
    await safely_delete_message(ctx)
    s = savings_tracker
    embed = discord.Embed(title="💰 Lifetime Savings Tracker", color=COLOR_SUCCESS)
    if s.get("trip_count", 0) == 0:
        embed.description = "No trips checked out yet. Run `/checkout` after optimizing to track your savings!"
    else:
        trip_count = s["trip_count"]
        avg_saved = s["total_net_saved"] / trip_count if trip_count > 0 else 0.0
        pct_saved = ((s['total_net_saved'] / s['total_full_price']) * 100) if s.get('total_full_price', 0) > 0 else 0
        embed.description = (
            f"**Total Saved: ${s['total_net_saved']:.2f}**\n"
            f"Across **{trip_count} trip(s)** • Average **${avg_saved:.2f}/trip** ({pct_saved:.0f}% savings rate)"
        )
        embed.add_field(name="Retail Value", value=f"${s['total_full_price']:.2f}", inline=True)
        embed.add_field(name="Total Paid", value=f"${s['total_paid']:.2f}", inline=True)
        embed.add_field(name="Coupon Cost", value=f"${s['total_coupon_cost']:.2f}", inline=True)
        embed.set_footer(text="AIO Bot • Use /history for individual trips")
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
@app_commands.describe(start="Filter by start date (YYYY-MM-DD)", end="Optional end date (YYYY-MM-DD)")
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
            f"**{trip.get('date', '—')}** ({trip.get('time', '—')}) • Paid **${trip.get('total_due', 0.0):.2f}** *(Saved ${trip.get('net_saved', 0.0):.2f})*\n"
            f"• *{item_names}*"
        )
    embed.description = "\n\n".join(desc_lines)
    embed.set_footer(text=f"AIO Bot • Showing {len(matches[-8:])} of {len(trips)} trips")
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
    embed = discord.Embed(title="🗑️ Trip Deleted", color=COLOR_WARN)
    embed.add_field(name="Trip Date", value=f"{removed.get('date', '—')} @ {removed.get('time', '—')}", inline=False)
    embed.add_field(name="Items Removed", value=item_names, inline=False)
    embed.add_field(name="Paid (Reverted)", value=f"${removed.get('total_due', 0.0):.2f}", inline=True)
    embed.add_field(name="Savings (Reverted)", value=f"${removed.get('net_saved', 0.0):.2f}", inline=True)
    embed.add_field(
        name="Updated Lifetime Saved",
        value=f"**${savings_tracker['total_net_saved']:.2f}** ({savings_tracker['trip_count']} trips)",
        inline=False
    )
    embed.set_footer(text="AIO Bot • Trip Reverted")
    await ctx.send(embed=embed)



# --- SHOPPER TRIPS & INTELLIGENCE DASHBOARD ---

def assess_trip_performance(trip: Dict[str, Any]) -> Tuple[str, str, int]:
    """Evaluates how effectively coupons performed on a trip. Returns (badge, analysis, color)."""
    subtotal = trip.get("subtotal", 0.0)
    net_saved = trip.get("net_saved", 0.0)
    savings_pct = trip.get("savings_pct")
    if savings_pct is None and subtotal > 0:
        savings_pct = round((net_saved / subtotal * 100), 1)
    savings_pct = savings_pct or 0.0

    if savings_pct >= 75:
        return (
            f"🔥 **Phenomenal Deal!** (`{savings_pct}%` Saved)",
            "Coupons stacked and bundled with peak efficiency! Out-of-pocket register payment was slashed to a minimum.",
            COLOR_SUCCESS
        )
    elif savings_pct >= 50:
        return (
            f"✅ **Great Deal!** (`{savings_pct}%` Saved)",
            "Strong coupon coverage. The optimizer successfully reduced the majority of the retail bill.",
            0x2ECC71
        )
    elif savings_pct >= 25:
        return (
            f"👍 **Moderate Savings** (`{savings_pct}%` Saved)",
            "Coupons worked and saved money, though there may be room for higher-value bundle stacking.",
            0xF1C40F
        )
    else:
        return (
            f"⚠️ **Low Optimization** (`{savings_pct}%` Saved)",
            "Discount was relatively modest compared to retail subtotal. Consider stacking higher percentage or dollar-off coupons.",
            COLOR_WARN
        )

def get_trips_overview_stats() -> Dict[str, Any]:
    """Aggregates all shopper trip history, calculating aggregate and per-shopper performance."""
    trips = savings_tracker.get("trips", [])
    total_trips = len(trips)
    total_retail = sum(t.get("subtotal", 0.0) for t in trips)
    total_paid = sum(t.get("total_due", 0.0) for t in trips)
    total_coupon_cost = sum(t.get("coupon_spend", 0.0) for t in trips)
    total_net_saved = sum(t.get("net_saved", 0.0) for t in trips)
    overall_savings_pct = round((total_net_saved / total_retail * 100), 1) if total_retail > 0 else 0.0

    shoppers: Dict[str, Dict[str, Any]] = {}
    for t in trips:
        uid = str(t.get("user_id") or t.get("user_name") or "unknown")
        name = t.get("user_name") or "Shopper"
        if uid not in shoppers:
            shoppers[uid] = {
                "key": uid,
                "name": name,
                "user_id": t.get("user_id"),
                "trips_count": 0,
                "retail": 0.0,
                "paid": 0.0,
                "saved": 0.0,
                "trips": [],
                "last_date": t.get("date", "—")
            }
        shoppers[uid]["trips_count"] += 1
        shoppers[uid]["retail"] += t.get("subtotal", 0.0)
        shoppers[uid]["paid"] += t.get("total_due", 0.0)
        shoppers[uid]["saved"] += t.get("net_saved", 0.0)
        shoppers[uid]["trips"].append(t)
        shoppers[uid]["last_date"] = t.get("date", "—")

    return {
        "total_trips": total_trips,
        "total_retail": total_retail,
        "total_paid": total_paid,
        "total_coupon_cost": total_coupon_cost,
        "total_net_saved": total_net_saved,
        "overall_savings_pct": overall_savings_pct,
        "shoppers": shoppers
    }

def build_trips_overview_embed() -> discord.Embed:
    stats = get_trips_overview_stats()
    trips = savings_tracker.get("trips", [])
    embed = discord.Embed(
        title="📊 Shopper Couponing Intelligence & Trips Dashboard",
        description=(
            "Live server analytics tracking member couponing performance, register savings, "
            "and bundle efficiency across all checked-out trips."
        ),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="🛒 Total Trips Logged",
        value=f"**`{stats['total_trips']}`** trips across **`{len(stats['shoppers'])}`** shopper(s)",
        inline=True
    )
    embed.add_field(
        name="💰 Lifetime Net Saved",
        value=f"**`${stats['total_net_saved']:.2f}`** *(**{stats['overall_savings_pct']}%** saved)*",
        inline=True
    )
    embed.add_field(
        name="🏷️ Retail vs. Paid",
        value=f"Retail: `${stats['total_retail']:.2f}`\nPaid: `${stats['total_paid']:.2f}`",
        inline=True
    )

    if stats["shoppers"]:
        sorted_shoppers = sorted(stats["shoppers"].values(), key=lambda s: s["saved"], reverse=True)
        leaderboard_lines = []
        for rank, s in enumerate(sorted_shoppers[:8], 1):
            pct = round(s["saved"] / s["retail"] * 100, 1) if s["retail"] > 0 else 0.0
            mention_or_name = f"<@{s['user_id']}>" if s["user_id"] else f"**{s['name']}**"
            leaderboard_lines.append(
                f"**#{rank}** {mention_or_name} — **${s['saved']:.2f}** saved (`{s['trips_count']}` trips, `{pct}%` avg)"
            )
        embed.add_field(
            name="🏆 Top Shoppers Leaderboard",
            value="\n".join(leaderboard_lines),
            inline=False
        )
    else:
        embed.add_field(name="🏆 Top Shoppers Leaderboard", value="*No shopper trips recorded yet.*", inline=False)

    if trips:
        last = trips[-1]
        badge, note, _ = assess_trip_performance(last)
        last_shopper = f"<@{last['user_id']}>" if last.get("user_id") else f"**{last.get('user_name', 'Shopper')}**"
        item_summary = ", ".join(i["name"] for i in last.get("items", [])[:3]) or "Items"
        if len(last.get("items", [])) > 3:
            item_summary += f" +{len(last['items'])-3} more"
        embed.add_field(
            name="⚡ Latest Trip Activity",
            value=(
                f"• **Shopper:** {last_shopper} on `{last.get('date', '—')}`\n"
                f"• **Items:** {item_summary}\n"
                f"• **Performance:** {badge}\n"
                f"• **Paid:** `${last.get('total_due', 0.0):.2f}` | **Net Saved:** `${last.get('net_saved', 0.0):.2f}`"
            ),
            inline=False
        )

    embed.set_footer(text="AIO Bot • Couponing Analytics • Use buttons below to browse individual trips")
    return embed

def build_trip_detail_embed(index: int) -> discord.Embed:
    trips = savings_tracker.get("trips", [])
    if not trips or index < 0 or index >= len(trips):
        return discord.Embed(title="📭 No Trip Data", description="No recorded trip matches this index.", color=COLOR_WARN)

    trip = trips[index]
    badge, note, color = assess_trip_performance(trip)
    shopper_str = f"<@{trip['user_id']}> (`{trip.get('user_name', 'Shopper')}`)" if trip.get("user_id") else f"**{trip.get('user_name', 'Shopper')}**"

    embed = discord.Embed(
        title=f"🧾 Trip Audit • Trip #{trip.get('id', index+1)} of {len(trips)}",
        description=f"👤 **Shopper:** {shopper_str}\n🗓️ **Date:** `{trip.get('date', '—')}` @ `{trip.get('time', '—')}`",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="📊 Couponing Effectiveness", value=f"{badge}\n*{note}*", inline=False)

    embed.add_field(name="🏷️ Full Retail", value=f"**${trip.get('subtotal', 0.0):.2f}**", inline=True)
    embed.add_field(name="💵 Register Paid", value=f"**${trip.get('total_due', 0.0):.2f}**", inline=True)
    embed.add_field(name="💰 Net Saved", value=f"**${trip.get('net_saved', 0.0):.2f}**", inline=True)

    items = trip.get("items", [])
    if items:
        item_lines = [f"• **{it['name']}** — `${it['price']:.2f}`" for it in items[:12]]
        if len(items) > 12:
            item_lines.append(f"*...and {len(items)-12} more items*")
        embed.add_field(name=f"📦 Items in Cart ({len(items)})", value="\n".join(item_lines), inline=False)

    coupons = trip.get("coupons", [])
    if coupons:
        coupon_lines = [f"• {coupon_label(c)}" for c in coupons]
        embed.add_field(name=f"🎟️ Coupons Applied ({len(coupons)})", value="\n".join(coupon_lines), inline=True)
        embed.add_field(name="💸 Coupon Cost", value=f"**${trip.get('coupon_spend', 0.0):.2f}**", inline=True)

    embed.set_footer(text=f"AIO Bot • Trip Index {index + 1} of {len(trips)}")
    return embed

def build_shopper_stats_embed(shopper_key: str, guild: Optional[discord.Guild] = None) -> discord.Embed:
    stats = get_trips_overview_stats()
    shopper = stats["shoppers"].get(shopper_key)
    if shopper:
        user_mention = f"<@{shopper['user_id']}>" if shopper.get("user_id") else f"**{shopper['name']}**"
        pct = round(shopper["saved"] / shopper["retail"] * 100, 1) if shopper["retail"] > 0 else 0.0

        embed = discord.Embed(
            title=f"👤 Shopper Profile • {shopper['name']}",
            description=f"Personal couponing dossier for {user_mention}.",
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="🛒 Completed Trips", value=f"**`{shopper['trips_count']}`** trip(s)", inline=True)
        embed.add_field(name="💰 Lifetime Net Saved", value=f"**`${shopper['saved']:.2f}`**", inline=True)
        embed.add_field(name="📈 Average Efficiency", value=f"**`{pct}%`** saved", inline=True)
        embed.add_field(name="🏷️ Total Retail Optimized", value=f"${shopper['retail']:.2f}", inline=True)
        embed.add_field(name="💵 Total Out-of-Pocket Paid", value=f"${shopper['paid']:.2f}", inline=True)
        embed.add_field(name="🗓️ Last Trip Date", value=f"`{shopper['last_date']}`", inline=True)

        trips_list = shopper.get("trips", [])
        if trips_list:
            lines = []
            for t in trips_list[-5:]:
                t_pct = t.get("savings_pct", 0.0)
                lines.append(f"• `{t.get('date')}` — Paid **${t.get('total_due', 0.0):.2f}**, Saved **${t.get('net_saved', 0.0):.2f}** ({t_pct}%)")
            embed.add_field(name="📜 Recent Trips", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"AIO Bot • Shopper Intelligence • {shopper['name']}")
        return embed

    # If no trip history, display profile for any server member
    member = None
    if guild and shopper_key.isdigit():
        member = guild.get_member(int(shopper_key))

    name = member.display_name if member else f"Member {shopper_key}"
    user_mention = member.mention if member else f"<@{shopper_key}>"

    session = get_session(int(shopper_key)) if shopper_key.isdigit() else {"items": [], "coupons": []}
    cart_items = session.get("items", [])
    cart_coupons = session.get("coupons", [])

    embed = discord.Embed(
        title=f"👤 Shopper Profile • {name}",
        description=f"Server member couponing status for {user_mention}.",
        color=COLOR_INFO,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="🛒 Completed Trips", value="`0` trips logged", inline=True)
    embed.add_field(name="💰 Lifetime Saved", value="$0.00", inline=True)
    embed.add_field(name="📈 Optimization Efficiency", value="N/A", inline=True)

    cart_desc = f"**{len(cart_items)} item(s)** loaded, **{len(cart_coupons)} coupon(s)** active" if (cart_items or cart_coupons) else "*Cart is currently empty*"
    embed.add_field(name="🛒 Active Shopping Cart", value=cart_desc, inline=False)
    embed.add_field(
        name="ℹ️ Note",
        value="This member has not checked out any trips yet. When they optimize and run `/checkout`, their savings and bundle rating will appear here.",
        inline=False
    )
    embed.set_footer(text=f"AIO Bot • Shopper Intelligence • {name}")
    return embed


class ShopperFilterSelect(discord.ui.Select):
    def __init__(self, guild: Optional[discord.Guild], shoppers: List[Dict[str, Any]], current_key: Optional[str] = None):
        options = [
            discord.SelectOption(
                label="📊 All Shoppers Overview",
                value="overview",
                description="View global server analytics and leaderboard",
                emoji="🌐",
                default=(current_key is None or current_key == "overview")
            )
        ]
        added_ids = set()
        for s in shoppers[:15]:
            if s.get("user_id"):
                added_ids.add(str(s["user_id"]))
            lbl = s['name'][:25]
            desc = f"{s['trips_count']} trip(s) • ${s['saved']:.2f} saved"[:50]
            options.append(
                discord.SelectOption(
                    label=lbl,
                    value=s['key'],
                    description=desc,
                    emoji="🏆",
                    default=(current_key == s['key'])
                )
            )

        if guild:
            for m in guild.members:
                if not m.bot and str(m.id) not in added_ids and len(options) < 25:
                    options.append(
                        discord.SelectOption(
                            label=m.display_name[:25],
                            value=str(m.id),
                            description=f"@{m.name} • 0 trips logged"[:50],
                            emoji="👤",
                            default=(current_key == str(m.id))
                        )
                    )

        super().__init__(placeholder="👤 Select any member on the server to view stats...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        view: "TripsDashboardView" = self.view
        if chosen == "overview":
            view.view_mode = "overview"
            view.selected_shopper_key = None
            embed = build_trips_overview_embed()
        else:
            view.view_mode = "shopper"
            view.selected_shopper_key = chosen
            embed = build_shopper_stats_embed(chosen, interaction.guild)
        view.update_components()
        await interaction.response.edit_message(embed=embed, view=view)


class TripsDashboardView(discord.ui.View):
    def __init__(self, admin_user: discord.Member, initial_mode: str = "overview", trip_idx: int = 0, shopper_key: Optional[str] = None, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=180)
        self.admin_user = admin_user
        self.guild = guild or admin_user.guild
        self.view_mode = initial_mode
        self.current_trip_idx = trip_idx
        self.selected_shopper_key = shopper_key
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.admin_user.id or await is_owner_only(interaction.user, interaction.guild) or is_staff_or_admin(interaction.user):
            return True
        await interaction.response.send_message("⛔ Only server staff or the bot owner can interact with this dashboard.", ephemeral=True)
        return False

    def update_components(self):
        self.clear_items()
        trips = savings_tracker.get("trips", [])
        stats = get_trips_overview_stats()
        shoppers = list(stats["shoppers"].values())

        self.add_item(ShopperFilterSelect(self.guild, shoppers, self.selected_shopper_key))

        btn_overview = discord.ui.Button(
            label="Overview",
            style=discord.ButtonStyle.primary if self.view_mode == "overview" else discord.ButtonStyle.secondary,
            emoji="📊",
            row=1
        )
        btn_overview.callback = self.btn_overview_click
        self.add_item(btn_overview)

        btn_feed = discord.ui.Button(
            label="Trips Feed",
            style=discord.ButtonStyle.primary if self.view_mode == "feed" else discord.ButtonStyle.secondary,
            emoji="📜",
            row=1
        )
        btn_feed.callback = self.btn_feed_click
        self.add_item(btn_feed)

        if self.view_mode == "feed" and trips:
            btn_prev = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
            btn_prev.callback = self.btn_prev_click
            self.add_item(btn_prev)

            btn_next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️", row=1)
            btn_next.callback = self.btn_next_click
            self.add_item(btn_next)

        btn_refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
        btn_refresh.callback = self.btn_refresh_click
        self.add_item(btn_refresh)

    async def btn_overview_click(self, interaction: discord.Interaction):
        self.view_mode = "overview"
        self.selected_shopper_key = None
        self.update_components()
        embed = build_trips_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_feed_click(self, interaction: discord.Interaction):
        self.view_mode = "feed"
        trips = savings_tracker.get("trips", [])
        if trips:
            self.current_trip_idx = max(0, min(self.current_trip_idx, len(trips) - 1))
            embed = build_trip_detail_embed(self.current_trip_idx)
        else:
            embed = discord.Embed(title="📭 No Trips Logged", description="No checked-out trips logged yet.", color=COLOR_WARN)
        self.update_components()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_prev_click(self, interaction: discord.Interaction):
        trips = savings_tracker.get("trips", [])
        if trips:
            self.current_trip_idx = (self.current_trip_idx - 1) % len(trips)
            embed = build_trip_detail_embed(self.current_trip_idx)
            self.update_components()
            await interaction.response.edit_message(embed=embed, view=self)

    async def btn_next_click(self, interaction: discord.Interaction):
        trips = savings_tracker.get("trips", [])
        if trips:
            self.current_trip_idx = (self.current_trip_idx + 1) % len(trips)
            embed = build_trip_detail_embed(self.current_trip_idx)
            self.update_components()
            await interaction.response.edit_message(embed=embed, view=self)

    async def btn_refresh_click(self, interaction: discord.Interaction):
        self.update_components()
        if self.view_mode == "overview":
            embed = build_trips_overview_embed()
        elif self.view_mode == "feed":
            embed = build_trip_detail_embed(self.current_trip_idx)
        elif self.view_mode == "shopper" and self.selected_shopper_key:
            embed = build_shopper_stats_embed(self.selected_shopper_key, self.guild)
        else:
            embed = build_trips_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)


@bot.hybrid_command(
    name="trips",
    aliases=["tripsdashboard", "tripdashboard", "shopperstats", "couponstats", "tripstats"],
    description="Admin dashboard to view other shoppers' couponing trips, savings, and performance stats"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(shopper="Optional user to filter stats for")
async def trips_dashboard_cmd(ctx: commands.Context, shopper: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Permission Denied: Only server staff or the bot owner can view shopper analytics.", delete_after=6)
        return

    if ctx.guild and not ctx.guild.chunked:
        try:
            await ctx.guild.chunk()
        except Exception:
            pass

    if shopper:
        shopper_key = str(shopper.id)
        embed = build_shopper_stats_embed(shopper_key, ctx.guild)
        view = TripsDashboardView(admin_user=ctx.author, initial_mode="shopper", shopper_key=shopper_key, guild=ctx.guild)
    else:
        embed = build_trips_overview_embed()
        view = TripsDashboardView(admin_user=ctx.author, initial_mode="overview", guild=ctx.guild)

    await ctx.send(embed=embed, view=view)


# --- TARGETED MASS DM SYSTEM WITH DROPDOWN USER SELECT & ALL MEMBERS ---

class MassDMModal(discord.ui.Modal, title="✍️ Targeted Mass DM Composer"):
    dm_title = discord.ui.TextInput(
        label="🏷️ Announcement Title",
        placeholder="e.g. Exclusive CVS Deal Alert! 🎟️",
        default="Important Server Announcement",
        max_length=150,
        required=True
    )
    dm_content = discord.ui.TextInput(
        label="📝 Message Body (Markdown Supported)",
        style=discord.TextStyle.paragraph,
        placeholder="Type your announcement, coupons, or custom note here...",
        max_length=2000,
        required=True
    )

    def __init__(self, parent_view: "MassDMView"):
        super().__init__()
        self.parent_view = parent_view
        if self.parent_view.current_title:
            self.dm_title.default = self.parent_view.current_title
        if self.parent_view.current_message:
            self.dm_content.default = self.parent_view.current_message

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.current_title = self.dm_title.value.strip()
        self.parent_view.current_message = self.dm_content.value.strip()
        embed = self.parent_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class MassDMMemberSelect(discord.ui.Select):
    def __init__(self, page_members: List[discord.Member], selected_ids: Set[int], page: int, total_pages: int, total_count: int):
        options = []
        for m in page_members:
            is_selected = m.id in selected_ids
            options.append(
                discord.SelectOption(
                    label=m.display_name[:25],
                    description=f"@{m.name}"[:50],
                    value=str(m.id),
                    default=is_selected,
                    emoji="👤"
                )
            )
        super().__init__(
            placeholder=f"👥 Select members (Pg {page+1}/{total_pages} • {total_count} members total)...",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        current_page_ids = {int(opt.value) for opt in self.options}
        chosen_ids = {int(v) for v in self.values}
        for uid in current_page_ids:
            if uid in chosen_ids:
                self.view.selected_user_ids.add(uid)
            else:
                self.view.selected_user_ids.discard(uid)

        self.view.update_components()
        embed = self.view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.view)


class MassDMRoleSelect(discord.ui.Select):
    def __init__(self, roles: List[discord.Role]):
        options = []
        for r in roles[:25]:
            if r.name == "@everyone" or len(r.members) == 0:
                continue
            options.append(
                discord.SelectOption(
                    label=f"@{r.name}"[:25],
                    value=str(r.id),
                    description=f"{len([m for m in r.members if not m.bot])} member(s)",
                    emoji="🎭"
                )
            )
        if not options:
            options.append(discord.SelectOption(label="No custom roles with members", value="none"))
        super().__init__(placeholder="🎭 Quick-select all members in a role...", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return
        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)
        if role:
            for m in role.members:
                if not m.bot:
                    self.view.selected_user_ids.add(m.id)
        self.view.update_components()
        embed = self.view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.view)


class MassDMView(discord.ui.View):
    def __init__(
        self,
        sender: discord.Member,
        all_members: Optional[List[discord.Member]] = None,
        initial_title: Optional[str] = None,
        initial_message: Optional[str] = None
    ):
        super().__init__(timeout=300)
        self.sender = sender
        self.guild = getattr(sender, "guild", None)
        if all_members is None:
            if self.guild and hasattr(self.guild, "members"):
                self.all_members = [m for m in self.guild.members if not getattr(m, "bot", False)]
            else:
                self.all_members = []
        else:
            self.all_members = all_members
        self.current_title = initial_title or "Important Announcement"
        self.current_message = initial_message or ""
        self.selected_user_ids: Set[int] = set()
        self.current_page = 0
        self.per_page = 25
        self.total_pages = max(1, (len(self.all_members) + self.per_page - 1) // self.per_page)
        self.update_components()

    @property
    def user_select(self):
        for item in self.children:
            if isinstance(item, MassDMMemberSelect):
                return item
        return None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.sender.id or await is_owner_only(interaction.user, interaction.guild):
            return True
        await interaction.response.send_message("⛔ Only the staff member who initiated this broadcast can control it.", ephemeral=True)
        return False

    def update_components(self):
        self.clear_items()
        start = self.current_page * self.per_page
        end = min(start + self.per_page, len(self.all_members))
        page_members = self.all_members[start:end]

        if page_members:
            self.add_item(MassDMMemberSelect(page_members, self.selected_user_ids, self.current_page, self.total_pages, len(self.all_members)))

        if self.guild and self.guild.roles:
            self.add_item(MassDMRoleSelect(self.guild.roles))

        btn_select_all = discord.ui.Button(
            label=f"Select ALL ({len(self.all_members)})",
            style=discord.ButtonStyle.secondary,
            emoji="👥",
            row=2
        )
        btn_select_all.callback = self.btn_select_all_click
        self.add_item(btn_select_all)

        btn_clear = discord.ui.Button(
            label="Clear Selection",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            row=2
        )
        btn_clear.callback = self.btn_clear_click
        self.add_item(btn_clear)

        if self.total_pages > 1:
            btn_prev = discord.ui.Button(label="Prev Page", style=discord.ButtonStyle.secondary, emoji="◀️", row=2)
            btn_prev.callback = self.btn_prev_page_click
            self.add_item(btn_prev)

            btn_next = discord.ui.Button(label="Next Page", style=discord.ButtonStyle.secondary, emoji="▶️", row=2)
            btn_next.callback = self.btn_next_page_click
            self.add_item(btn_next)

        btn_msg = discord.ui.Button(label="Set Message", style=discord.ButtonStyle.primary, emoji="✍️", row=3)
        btn_msg.callback = self.btn_set_message_click
        self.add_item(btn_msg)

        btn_send = discord.ui.Button(label="Send Direct Messages", style=discord.ButtonStyle.success, emoji="🚀", row=3)
        btn_send.callback = self.btn_send_click
        self.add_item(btn_send)

        btn_cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️", row=3)
        btn_cancel.callback = self.btn_cancel_click
        self.add_item(btn_cancel)

    async def btn_select_all_click(self, interaction: discord.Interaction):
        for m in self.all_members:
            self.selected_user_ids.add(m.id)
        self.update_components()
        embed = self.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_clear_click(self, interaction: discord.Interaction):
        self.selected_user_ids.clear()
        self.update_components()
        embed = self.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_prev_page_click(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % self.total_pages
        self.update_components()
        embed = self.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_next_page_click(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % self.total_pages
        self.update_components()
        embed = self.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def btn_set_message_click(self, interaction: discord.Interaction):
        modal = MassDMModal(self)
        await interaction.response.send_modal(modal)

    async def btn_send_click(self, interaction: discord.Interaction):
        selected_members = [m for m in self.all_members if m.id in self.selected_user_ids]
        if not selected_members:
            await interaction.response.send_message("❌ Please select at least one recipient from the dropdown above or click 'Select ALL'!", ephemeral=True)
            return
        if not self.current_message:
            await interaction.response.send_message("❌ Please set a message body using the **Set Message** button before sending!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        sending_embed = discord.Embed(
            title="⏳ Dispatching Direct Messages...",
            description=f"Sending messages to **{len(selected_members)}** member(s). Please wait...",
            color=COLOR_PRIMARY
        )
        await interaction.response.edit_message(embed=sending_embed, view=self)

        success_users = []
        failed_users = []

        guild = interaction.guild
        for user in selected_members:
            dm_embed = discord.Embed(
                title=f"📬 {self.current_title}",
                description=self.current_message,
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc)
            )
            if guild and guild.icon:
                dm_embed.set_author(name=f"{guild.name} Official Broadcast", icon_url=guild.icon.url)
            else:
                dm_embed.set_author(name="Official Server Broadcast")
            dm_embed.set_footer(text=f"Sent by {self.sender.display_name} • Direct Announcement")

            try:
                await user.send(embed=dm_embed)
                success_users.append(user)
            except Exception as e:
                failed_users.append((user, str(e)))
            await asyncio.sleep(0.4)

        status_color = COLOR_SUCCESS if not failed_users else (COLOR_WARN if success_users else COLOR_ERROR)
        result_embed = discord.Embed(
            title="✅ Targeted Mass DM Complete" if not failed_users else "⚠️ Targeted Mass DM Completed with Warnings",
            description="Direct messages have finished sending to your selected recipient list.",
            color=status_color,
            timestamp=datetime.now(timezone.utc)
        )
        result_embed.add_field(name="📨 Successfully Delivered", value=f"**{len(success_users)}** member(s)", inline=True)
        result_embed.add_field(name="❌ Failed Deliveries", value=f"**{len(failed_users)}** member(s)", inline=True)

        if failed_users:
            fail_lines = [f"• {u.mention} (`{u.display_name}`): DMs closed or blocked" for u, _ in failed_users[:8]]
            result_embed.add_field(name="⚠️ Failed Recipients", value="\n".join(fail_lines), inline=False)

        result_embed.set_footer(text=f"Broadcast finished • Total processed: {len(selected_members)}")
        await interaction.message.edit(embed=result_embed, view=None)

    async def btn_cancel_click(self, interaction: discord.Interaction):
        cancel_embed = discord.Embed(title="🚫 Broadcast Cancelled", description="The targeted Mass DM was cancelled and no messages were sent.", color=COLOR_WARN)
        await interaction.response.edit_message(embed=cancel_embed, view=None)

    def build_preview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📬 Targeted Mass DM Studio",
            description=(
                f"Use the interactive dropdown menu below to select from **{len(self.all_members)} server members**, "
                "or click **Select ALL** to broadcast to everyone at once."
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )

        if not self.selected_user_ids:
            recipients_text = f"*(None selected yet — select members from dropdown below or click 'Select ALL ({len(self.all_members)})')*"
        else:
            selected_objs = [m for m in self.all_members if m.id in self.selected_user_ids]
            names = [f"• {u.mention} (`{u.display_name}`)" for u in selected_objs[:10]]
            if len(selected_objs) > 10:
                names.append(f"*...and {len(selected_objs)-10} more*")
            recipients_text = f"**{len(selected_objs)} of {len(self.all_members)} Member(s) Selected:**\n" + "\n".join(names)

        embed.add_field(name="👥 Target Recipients", value=recipients_text, inline=False)
        embed.add_field(name="🏷️ Message Title", value=f"`{self.current_title}`", inline=True)

        preview_body = self.current_message if self.current_message else "*(No message entered yet — click 'Set Message' below)*"
        if len(preview_body) > 600:
            preview_body = preview_body[:597] + "..."
        embed.add_field(name="📝 Message Content Preview", value=preview_body, inline=False)
        embed.set_footer(text=f"AIO Bot Broadcast Studio • Page {self.current_page+1}/{self.total_pages} • Initiated by {self.sender.display_name}")
        return embed


@bot.hybrid_command(
    name="massdm",
    aliases=["sendmassdm", "dmusers", "dmselect", "bulkdm"],
    description="Send a targeted DM to selected server members using an interactive dropdown picker"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(message="Optional pre-filled message text")
async def massdm_cmd(ctx: commands.Context, message: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Permission Denied: Only server staff or the bot owner can send mass DMs.", delete_after=6)
        return

    all_members = [m for m in ctx.guild.members if not m.bot]
    if len(all_members) < 5:
        try:
            if not ctx.guild.chunked:
                await ctx.guild.chunk()
            all_members = [m for m in ctx.guild.members if not m.bot]
            if len(all_members) < 5:
                all_members = [m async for m in ctx.guild.fetch_members(limit=None) if not m.bot]
        except Exception:
            pass

    view = MassDMView(sender=ctx.author, all_members=all_members, initial_message=message)
    embed = view.build_preview_embed()
    await ctx.send(embed=embed, view=view)






# --- ADVANCED MODERATION & SECURITY ---



@bot.hybrid_command(name="lockdown", description="Emergency server lockdown: toggle message permissions across all text channels")
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(action="Turn lockdown on or off", reason="Reason for emergency lockdown")
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
        title=f"🚨 Server Lockdown {'Activated' if lock else 'Deactivated'}",
        description=f"Server message permissions are now **{'LOCKED' if lock else 'UNLOCKED'}**.",
        color=COLOR_ERROR if lock else COLOR_SUCCESS
    )
    embed.add_field(name="Channels", value=f"**{changed_count}** updated", inline=True)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Case ID", value=f"`#CASE-{case_id:04d}`", inline=True)
    embed.add_field(name="Reason", value=reason or "Emergency Protocol", inline=False)
    embed.set_footer(text="AIO Bot • Server Security")
    await progress_msg.edit(content=None, embed=embed)


@bot.hybrid_command(name="filter", description="Manage the server Auto-Mod blacklisted words")
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(action="Filter action: add, remove, list, or clear", word="Word or phrase to filter")
async def filter_cmd(ctx, action: Literal["add", "remove", "list", "clear"], *, word: Optional[str] = None):
    await safely_delete_message(ctx)
    gid = ctx.guild.id
    action = action.lower()

    if action == "list":
        words = get_filter_words(gid)
        if not words:
            await ctx.send("📋 Auto-Mod filter is currently empty. Add words with `/filter add [word]`.", delete_after=8)
            return
        embed = discord.Embed(title=f"🛡️ Filtered Words ({len(words)})", color=COLOR_INFO)
        embed.description = ", ".join(f"`{w}`" for w in words)
        embed.set_footer(text="AIO Bot • AutoMod Filter")
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
@app_commands.describe(member="Member whose moderation case history to view")
async def modlogs_cmd(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    cases = [c for c in mod_cases_db.get("cases", []) if c.get("guild_id") == str(ctx.guild.id) and str(member.id) in str(c.get("target")) or str(member) == str(c.get("target"))]

    if not cases:
        await ctx.send(f"✨ No moderation case records found for **{member.display_name}**.", delete_after=8)
        return

    embed = discord.Embed(title=f"📜 Modlogs • {member.display_name} ({len(cases)})", color=COLOR_INFO)
    embed.set_thumbnail(url=member.display_avatar.url)
    for c in cases[-8:]:
        embed.add_field(
            name=f"`#CASE-{c['case_id']:04d}` • {c['action']} ({c['timestamp']})",
            value=f"**Reason:** {c['reason']}\n**Mod:** {c['moderator']}",
            inline=False
        )
    embed.set_footer(text="AIO Bot • Moderation Logs")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="case", description="View specific details for a moderation case ID")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(case_id="Case ID number (e.g. 1)")
async def case_cmd(ctx, case_id: int):
    await safely_delete_message(ctx)
    cases = mod_cases_db.get("cases", [])
    found = next((c for c in cases if c.get("case_id") == case_id and c.get("guild_id") == str(ctx.guild.id)), None)

    if not found:
        await ctx.send(f"❌ Case `#CASE-{case_id:04d}` not found in this server.", delete_after=6)
        return

    embed = discord.Embed(title=f"📁 Case File #{found['case_id']:04d}", color=COLOR_PRIMARY)
    embed.add_field(name="Action", value=f"**{found['action']}**", inline=True)
    embed.add_field(name="Target User", value=str(found['target']), inline=True)
    embed.add_field(name="Moderator", value=str(found['moderator']), inline=True)
    embed.add_field(name="Reason", value=str(found['reason']), inline=False)
    if found.get("details") and found["details"] != "None":
        embed.add_field(name="Details", value=found["details"], inline=False)
    embed.add_field(name="Timestamp", value=found['timestamp'], inline=True)
    embed.set_footer(text="AIO Bot • Moderation Case")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="note", description="Manage internal staff notes on members")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    action="Note action: add, view, or clear",
    member="Target server member",
    note="Content of note to attach (required for add)"
)
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
        embed = discord.Embed(title=f"📝 Staff Notes • {member.display_name} ({len(notes)})", color=COLOR_WARN)
        embed.set_thumbnail(url=member.display_avatar.url)
        for idx, n in enumerate(notes, 1):
            embed.add_field(
                name=f"Note #{idx} • {n['timestamp']}",
                value=f"**Text:** {n['note']}\n**By:** {n['moderator']}",
                inline=False
            )
        embed.set_footer(text="AIO Bot • Staff Notes")
        await ctx.send(embed=embed)

    elif action == "clear":
        count = clear_mod_notes(gid, uid)
        await ctx.send(f"🧹 Cleared **{count}** staff note(s) for **{member.display_name}**.", delete_after=8)



# --- INTERACTIVE MINI-GAMES ---

@bot.hybrid_command(name="blackjack", aliases=["bj", "21"], description="Play an interactive game of 21 against the Dealer")
@app_commands.describe(bet="Amount of coins to wager (defaults to 50)")
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
@app_commands.describe(opponent="Server member to challenge (leave empty to play against AI)")
async def connect4_cmd(ctx, opponent: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if opponent and opponent.id == ctx.author.id:
        await ctx.send("❌ You cannot play Connect 4 against yourself!", delete_after=6)
        return
    view = Connect4View(p1=ctx.author, p2=opponent)
    embed = view.build_embed()
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="trivia", aliases=["quiz"], description="Test your knowledge in a 4-choice timed trivia challenge")
@app_commands.describe(category="Trivia topic category: general, tech, gaming, or science")
async def trivia_cmd(ctx, category: Optional[Literal["general", "tech", "gaming", "science"]] = "general"):
    await safely_delete_message(ctx)
    cat = (category or "general").lower()
    pool = TRIVIA_QUESTIONS.get(cat, TRIVIA_QUESTIONS["general"])
    question_data = secrets.choice(pool)

    view = TriviaView(user=ctx.author, question_data=question_data)
    embed = discord.Embed(
        title=f"🧠 Trivia Challenge • {cat.capitalize()}",
        description=f"**{question_data['q']}**\n\nSelect the correct option below (45s timer):",
        color=COLOR_PRIMARY
    )
    for idx, opt in enumerate(question_data["options"]):
        embed.add_field(name=f"Option {chr(65+idx)}", value=opt, inline=True)
    embed.set_footer(text="AIO Bot • 45s Timer • Earn +50 🪙 per win")
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="slots",
    aliases=["slot", "spin"],
    description="Spin the 3x3 high-roller slot machine (supports single spin or multi-round auto-spins)"
)
@commands.guild_only()
@app_commands.describe(bet="Coins to bet per spin (default 10)", rounds="Number of auto-spins to run (1 to 20, default 1)")
async def slots_cmd(ctx, bet: Optional[int] = 10, rounds: Optional[int] = 1):
    await safely_delete_message(ctx)
    stake = max(1, int(bet or 10))
    total_rounds = max(1, min(20, int(rounds or 1)))

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
        title_prefix = f"🎰 3x3 Slots — Round {round_num}/{total_rounds}" if total_rounds > 1 else "🎰 3x3 Slots"

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
            emb.set_footer(text=f"AIO Bot • Round {round_num}/{total_rounds} • {ctx.author.display_name}")
            return emb

        try:
            # Step 1: All 3 columns spinning
            frame1 = build_reel_frame(format_3x3_grid(grid, 0), "*Spinning reels...*")
            if msg is None:
                msg = await ctx.send(embed=frame1)
            else:
                await msg.edit(embed=frame1)
            await asyncio.sleep(0.9)

            # Step 2: Column 1 stops
            frame2 = build_reel_frame(format_3x3_grid(grid, 1), "*Reel 1 locked... Reels 2 & 3 spinning...*")
            await msg.edit(embed=frame2)
            await asyncio.sleep(0.8)

            # Step 3: Column 2 stops
            frame3 = build_reel_frame(format_3x3_grid(grid, 2), "*Reels 1 & 2 locked... Final reel spinning...*")
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
                final_frame.set_footer(text=f"AIO Bot • Spun by {ctx.author.display_name}")
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
        title=f"🎰 3x3 Slots — {len(history_lines)} Rounds Completed",
        description=f"Auto-spin session finished for {ctx.author.mention}!\n\n**Spin Log:**\n" + "\n".join(history_lines[-8:]),
        color=final_color
    )
    summary_embed.add_field(name="💰 Total Bet", value=f"**{total_spent:,} 🪙**", inline=True)
    summary_embed.add_field(name="🏆 Total Won", value=f"**{total_won:,} 🪙**", inline=True)
    summary_embed.add_field(name="📈 Net Outcome", value=f"**{net_str} 🪙**", inline=True)
    summary_embed.add_field(name="🎯 Wins / Losses", value=f"**{wins_count}W - {len(history_lines)-wins_count}L**", inline=True)
    summary_embed.add_field(name="🌟 Best Combo", value=f"**{best_payout_title}**", inline=True)
    summary_embed.add_field(name="👛 Final Balance", value=f"**{cur_bal:,} 🪙**", inline=True)
    summary_embed.set_footer(text=f"AIO Bot • Completed {len(history_lines)} spins")

    try:
        view = SlotsSpinView(user=ctx.author, bet=stake)
        await msg.edit(embed=summary_embed, view=view)
    except Exception:
        pass


@bot.hybrid_command(name="rps", description="Play Rock-Paper-Scissors against a friend or the bot")
@app_commands.describe(opponent="Server member to duel (leave empty to play against bot)")
async def rps_cmd(ctx, opponent: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    if opponent and opponent.id == ctx.author.id:
        await ctx.send("❌ You cannot duel yourself in RPS!", delete_after=6)
        return
    view = RPSView(p1=ctx.author, p2=opponent)
    opp_str = opponent.mention if opponent else "AIO Bot 🤖"
    embed = discord.Embed(
        title="🪨 Rock-Paper-Scissors",
        description=f"**{ctx.author.mention}** challenges **{opp_str}** to a duel!\n\nClick your choice below:",
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(name="coinflip", aliases=["flip", "coin"], description="Flip a coin with animated call and streak result")
@app_commands.describe(choice="Call heads or tails", bet="Coins to wager")
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
    anim_embed.description = "🪙 *Flipping coin...*\n\n`[ 🪙 🔄 🪙 🔄 🪙 ]`"
    anim_embed.set_footer(text=f"AIO Bot • Flipped for {ctx.author.display_name}")
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

    embed.set_footer(text="AIO Bot • Coinflip")
    await msg.edit(embed=embed)


# --- COIN ECONOMY COMMANDS ---

@bot.hybrid_command(name="balance", aliases=["bal", "coins"], description="Check your or another member's coin balance")
@app_commands.describe(member="Member whose coin balance to check (defaults to yourself)")
async def balance_cmd(ctx, member: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    target = member or ctx.author
    bal = get_user_coins(target.id)
    embed = discord.Embed(
        title=f"🪙 Coin Balance • {target.display_name}",
        color=COLOR_WARN
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Wallet", value=f"**{bal:,} 🪙 coins**", inline=True)
    embed.set_footer(text="AIO Bot • Economy")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="daily", description="Claim your daily allowance of 250 coins")
async def daily_cmd(ctx):
    await safely_delete_message(ctx)
    success, reward_or_bal, remaining = claim_daily_coins(ctx.author.id)
    if success:
        bal = get_user_coins(ctx.author.id)
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed",
            description=f"You received **+{reward_or_bal:,} 🪙 coins**!\n\nBalance: **{bal:,} 🪙**",
            color=COLOR_SUCCESS
        )
        embed.set_footer(text="AIO Bot • Daily Reward")
        await ctx.send(embed=embed)
    else:
        hrs = remaining // 3600
        mins = (remaining % 3600) // 60
        secs = remaining % 60
        embed = discord.Embed(
            title="⏳ Daily Cooldown",
            description=f"Please wait **{hrs}h {mins}m {secs}s** before claiming again.\n\nBalance: **{reward_or_bal:,} 🪙**",
            color=COLOR_WARN
        )
        embed.set_footer(text="AIO Bot • Daily Reward")
        await ctx.send(embed=embed, delete_after=10)


@bot.hybrid_command(name="pay", aliases=["give", "transfer"], description="Send coins to another server member")
@app_commands.describe(member="Member to send coins to", amount="Number of coins to transfer")
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
        title="💸 Coins Transferred",
        description=f"{ctx.author.mention} sent **{amount:,} 🪙 coins** to {member.mention}.",
        color=COLOR_SUCCESS
    )
    embed.add_field(name=f"{ctx.author.display_name}", value=f"**{sender_bal:,} 🪙**", inline=True)
    embed.add_field(name=f"{member.display_name}", value=f"**{recipient_bal:,} 🪙**", inline=True)
    embed.set_footer(text="AIO Bot • Economy")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="leaderboard", aliases=["top", "richest", "coinboard"], description="View the top 10 richest coin holders")
async def leaderboard_cmd(ctx):
    await safely_delete_message(ctx)
    top_users = get_coin_leaderboard(limit=10)
    embed = discord.Embed(
        title="🏆 Coin Leaderboard",
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
    embed.set_footer(text=f"AIO Bot • Your Balance: {user_bal:,} 🪙")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="roll", aliases=["dice"], description="Roll dice using tabletop notation (e.g. 2d6, 1d20, 100)")
@app_commands.describe(dice="Tabletop dice formula (e.g. 1d6, 2d20, 100)")
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
            embed.add_field(name="Rolls", value=f"`[{rolls_str}]`", inline=False)
        embed.set_footer(text=f"AIO Bot • Rolled by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("❌ Invalid dice format. Examples: `1d6`, `2d20`, `100`.", delete_after=6)



# --- CVS ACCOUNT BARCODE GENERATOR ---


@bot.hybrid_command(
    name="accounts",
    aliases=["cvsaccounts", "myaccounts", "cards", "cvsaccount", "cvscard", "extracare", "barcode"],
    description="Browse or search imported CVS ExtraCare accounts with barcodes & pagination (Owner Only)"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(query="Optional search term or ExtraCare account number")
async def list_accounts_cmd(ctx, query: Optional[str] = None):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can view private CVS accounts.", delete_after=6)
        return

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


@bot.hybrid_command(
    name="organizecoupons",
    aliases=["couponaccounts", "couponinventory", "couponsbyaccount", "accountcoupons", "sortcoupons"],
    description="Organize and browse CVS accounts grouped by active coupons (Owner Only)"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(coupon="Optional coupon filter (e.g. 4, 3, entire purchase, none)")
async def organize_coupons_cmd(ctx: commands.Context, coupon: Optional[str] = None):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can view private CVS accounts.", delete_after=6)
        return

    if not cvs_accounts_db:
        await ctx.send("📭 No CVS accounts currently loaded.", delete_after=8)
        return

    embed = build_coupon_organizer_embed(coupon)
    view = CouponOrganizerView(selected_category=coupon or "all")
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="stock",
    aliases=["cvsstock", "couponsstock", "activestock", "stockcoupons"],
    description="View live CVS coupon stock (Name, Phone, Coupon, Expiry) with 1-click Mark Used (Owner Only)"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(filter="Optional filter: 4 ($4 off), 3 ($3 off), 40 (40% off), other, or all")
async def stock_cmd(ctx: commands.Context, filter: Optional[str] = "all"):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can view private CVS stock.", delete_after=6)
        return

    if not cvs_accounts_db:
        await ctx.send("📭 No CVS accounts currently loaded in database.", delete_after=8)
        return

    f_type = (filter or "all").lower().strip()
    embed, cur_p, tot_p = build_stock_embed(page=0, filter_type=f_type)
    view = CVSStockView(page=0, filter_type=f_type)
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="used",
    aliases=["usecoupon", "usedcoupon", "markused", "markcouponused", "redeemcoupon"],
    description="Mark a coupon as used/redeemed on a CVS account by name, phone, or ID (Owner Only)"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    account="Cardholder name, phone, or ID (e.g. Corey, Melinda, 2246387825)",
    coupon="Specific coupon used (optional, defaults to active coupon on account)",
    savings="Dollar amount saved by this coupon (optional, e.g. 4.00, 3.00)"
)
async def used_cmd(
    ctx: commands.Context,
    account: str,
    coupon: Optional[str] = None,
    savings: Optional[str] = None
):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can manage private CVS accounts.", delete_after=6)
        return

    if not cvs_accounts_db:
        await ctx.send("📭 No CVS accounts currently loaded.", delete_after=8)
        return

    sav_val = None
    if savings:
        try:
            sav_val = float(savings.replace("$", "").strip())
        except ValueError:
            pass

    success, msg, acc = mark_coupon_used(
        account_query=account,
        coupon_name=coupon or "",
        savings=sav_val,
        user_tag=str(ctx.author)
    )

    if not success or not acc:
        await ctx.send(f"❌ {msg}", delete_after=8)
        return

    used_c = (acc.get("used_coupons") or [{}])[-1].get("coupon", coupon or "Coupon")
    embed = discord.Embed(
        title="✅ Coupon Marked as Used",
        description=f"Coupon **{used_c}** on **{acc.get('name')}** has been successfully redeemed and logged.",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="👤 Cardholder", value=f"**{acc.get('name')}** (`#{acc.get('id')}`)", inline=True)
    phone_fmt = f"({acc['phone'][:3]}) {acc['phone'][3:6]}-{acc['phone'][6:]}" if len(acc.get('phone', '')) == 10 else acc.get('phone', '—')
    embed.add_field(name="📞 Phone", value=f"`{phone_fmt}`", inline=True)
    if sav_val:
        embed.add_field(name="💰 Savings Logged", value=f"**${sav_val:.2f}**", inline=True)
    embed.add_field(name="🔢 ExtraCare Card", value=f"`{acc.get('extraCareNumber', '—')}`", inline=True)

    rem = acc.get("coupons", [])
    if rem:
        embed.add_field(name=f"🎟️ Remaining Active Coupons ({len(rem)})", value="\n".join(f"• {c}" for c in rem[:6]), inline=False)

    embed.set_footer(text=f"AIO Bot • Marked by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="unusecoupon",
    aliases=["undocoupon", "unmarkused"],
    description="Revert a used coupon back to active on a CVS account (Owner Only)"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    coupon="The coupon name/description to revert back to active",
    account="Account ID, Name, or ExtraCare Number (optional)"
)
async def unusecoupon_cmd(ctx: commands.Context, coupon: str, account: Optional[str] = None):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can manage private CVS accounts.", delete_after=6)
        return

    success, msg, acc = unmark_coupon_used(account_query=account or 1, coupon_name=coupon)
    if not success or not acc:
        await ctx.send(f"❌ {msg}", delete_after=8)
        return

    await ctx.send(f"✅ Reverted coupon **'{coupon}'** back to active for Account #{acc['id']} **{acc.get('name')}**!", delete_after=8)


@bot.hybrid_command(
    name="setup-vault",
    aliases=["setupvault", "setupownervault", "setup-owner-vault"],
    description="Set up or verify the strictly private #🔒-owner-vault channel (Owner Only)"
)
@commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup_vault_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not await is_owner_only(ctx.author, ctx.guild):
        await ctx.send("⛔ Security Error: Only the bot owner can configure the private vault.", delete_after=6)
        return
    ch, created = await setup_owner_vault_channel(ctx.guild)
    if ch:
        if created:
            await ctx.send(f"✅ Created private owner vault: {ch.mention} (only visible to you and the bot).", delete_after=8)
        else:
            await ctx.send(f"ℹ️ Private owner vault already active: {ch.mention}.", delete_after=8)
    else:
        await ctx.send("❌ Failed to create private owner vault. Please check bot permissions.", delete_after=8)



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
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
            read_message_history=True
        ),
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
        await apply_read_only_overwrites(existing, founder_role, mod_role)
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
        await apply_read_only_overwrites(new_channel, founder_role, mod_role)
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
        mod_embed = build_staff_modpanel_embed(guild)
        await existing.send(embed=mod_embed, view=StaffModPanelButtonView(guild))
        await ctx.send(f"✅ Staff Control Center & Moderation Panel refreshed at {existing.mention}! All actions are accessible via buttons.", delete_after=8)
        return

    try:
        new_channel = await guild.create_text_channel(
            channel_name,
            category=cat,
            topic="Staff control center: execute moderation, billing, role fixes, and panel refreshes via buttons.",
            overwrites=staff_overwrites
        )
        mod_embed = build_staff_modpanel_embed(guild)
        await new_channel.send(embed=mod_embed, view=StaffModPanelButtonView(guild))
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
    description="Staff command: Set up the #🌮-food-rewards channel with the food accounts store panel"
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

    channel_name = "🌮-food-rewards"
    existing = discord.utils.get(guild.text_channels, name=channel_name) or discord.utils.get(guild.text_channels, name="🌮🍕-food-rewards")
    target_ch = existing
    if not target_ch:
        try:
            target_ch = await guild.create_text_channel(
                channel_name,
                category=cat,
                topic="Official rewards store — new methods coming soon!"
            )
        except Exception as e:
            await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)
            return
    else:
        if target_ch.name != channel_name:
            try:
                await target_ch.edit(name=channel_name, topic="Official rewards store — new methods coming soon!")
            except Exception:
                pass
        try:
            await target_ch.purge(limit=25)
        except Exception:
            pass
    if target_ch:
        await apply_read_only_overwrites(target_ch)

    food_embed = build_food_accounts_embed()
    await target_ch.send(embed=food_embed, view=FoodAccountPurchaseView())
    await ctx.send(f"✅ Rewards Store panel (Coming Soon) ready at {target_ch.mention}!", delete_after=8)

# --- STAFF CHANNEL & SERVER-ISOLATED DISPENSER COMMANDS ---

@bot.hybrid_command(
    name="setup-staff-channel",
    aliases=["setupstaff", "staffchannel", "createstaffchat"],
    description="Create a secure, private staff-only channel with restricted permissions"
)
@commands.guild_only()
async def setup_staff_channel_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Access Denied**: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    cat_name = "🛡️ STAFF ZONE"
    cat = discord.utils.get(guild.categories, name=cat_name)
    if not cat:
        try:
            cat = await guild.create_category(cat_name)
        except Exception:
            pass

    channel_name = "🔒-staff-chat"
    existing = discord.utils.get(guild.text_channels, name=channel_name)

    staff_overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            manage_channels=True
        )
    }
    for role in guild.roles:
        if role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_messages:
            staff_overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        elif any(name in role.name.lower() for name in ["staff", "mod", "admin", "founder", "owner"]):
            staff_overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    if isinstance(ctx.author, discord.Member):
        staff_overwrites[ctx.author] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

    welcome_embed = discord.Embed(
        title="🛡️ Private Staff Coordination Channel",
        description=(
            "Welcome to your server's private staff channel!\n\n"
            "This channel is strictly restricted to server staff and administrators."
        ),
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    welcome_embed.add_field(
        name="🔒 Privacy & Security",
        value="Hidden from `@everyone`. Only members with Staff, Moderator, or Admin permissions can view or send messages here.",
        inline=False
    )
    welcome_embed.add_field(
        name="🎛️ Staff Control Center",
        value="Run `/modpanel` to open the Staff Control Center with quick moderation and management buttons.",
        inline=False
    )
    welcome_embed.add_field(
        name="🎁 Account Dispenser",
        value="Use `/addaccount` to stock your server's account dispenser, and `/dispenserstock` to inspect your inventory.",
        inline=False
    )
    welcome_embed.set_footer(text=f"{guild.name} • Staff Operations")

    if existing:
        try:
            await existing.edit(overwrites=staff_overwrites)
            await existing.send(embed=welcome_embed)
            await ctx.send(f"✅ Private staff channel refreshed at {existing.mention}!", delete_after=8)
            return
        except Exception as e:
            await ctx.send(f"⚠️ Channel exists at {existing.mention}, but encountered an error updating permissions: {e}", delete_after=8)
            return

    try:
        new_channel = await guild.create_text_channel(
            name=channel_name,
            category=cat,
            topic="Private staff-only coordination and administration channel.",
            overwrites=staff_overwrites
        )
        await new_channel.send(embed=welcome_embed)
        await ctx.send(f"✅ Private staff channel created at {new_channel.mention}!", delete_after=8)
    except Exception as e:
        await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)


@bot.hybrid_command(
    name="addaccount",
    aliases=["addcoupons", "stockaccount", "dispenseradd", "addstock"],
    description="Add accounts or coupons to this server's account dispenser pool"
)
@commands.guild_only()
@app_commands.describe(account_info="Optional account details to add directly, or leave blank to open modal")
async def add_account_cmd(ctx: commands.Context, *, account_info: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Access Denied**: Only server staff and administrators can stock the dispenser.", delete_after=6)
        return

    if not account_info:
        if ctx.interaction:
            await ctx.interaction.response.send_modal(AddAccountModal(ctx.guild.id))
            return
        else:
            await ctx.send("ℹ️ Please provide the account details or use `/addaccount` to open the interactive input window.", delete_after=8)
            return

    entries = parse_dispenser_entries(account_info)
    if not entries:
        await ctx.send("❌ No valid account information detected.", delete_after=6)
        return

    added = add_guild_dispenser_accounts(ctx.guild.id, entries, ctx.author)
    stats = get_guild_dispenser_stats(ctx.guild.id)
    embed = discord.Embed(
        title="✅ Dispenser Stock Added",
        description=f"Successfully loaded **{added}** new account(s) into this server's dispenser pool!",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="📦 Available In Stock", value=f"**{stats['available']}** accounts", inline=True)
    embed.add_field(name="🏷️ Total Stocked", value=f"**{stats['total']}** accounts", inline=True)
    embed.set_footer(text="AIO Bot • Account Dispenser")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="dispenser",
    aliases=["dispensershop", "accountdispenser", "getaccount"],
    description="Post the interactive CVS account & coupon dispenser panel"
)
@commands.guild_only()
async def dispenser_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Access Denied**: Only server staff can deploy the dispenser panel.", delete_after=6)
        return

    embed = build_dispenser_embed(ctx.guild)
    view = ServerDispenserLaunchView()
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="dispense",
    aliases=["dispenseaccount", "pullaccount", "dispense-account"],
    description="Staff command: Pull and dispense an account directly into the ticket/channel"
)
@commands.guild_only()
@app_commands.describe(
    customer="The customer/member who bought the account (optional)"
)
async def dispense_cmd(
    ctx: commands.Context,
    customer: Optional[discord.Member] = None
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Access Denied**: Only server staff and administrators can dispense accounts.", delete_after=6)
        return

    guild = ctx.guild
    target_user = customer or ctx.author

    ok, msg, account = dispense_guild_account(guild.id, user=target_user, staff=ctx.author)
    if not ok or not account:
        await ctx.send(msg, delete_after=8)
        return

    content = account.get("content", "").strip()
    account_id = account.get("id", 1)
    stats = get_guild_dispenser_stats(guild.id)

    delivery_embed = discord.Embed(
        title=f"🎁 Dispensed Account #{account_id}",
        description=f"```text\n{content}\n```",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    if customer:
        delivery_embed.add_field(name="👤 Customer", value=customer.mention, inline=True)
    delivery_embed.add_field(name="🛡️ Dispensed By", value=ctx.author.mention, inline=True)
    delivery_embed.add_field(name="📦 Remaining In Stock", value=f"**{stats['available']}** accounts", inline=True)
    delivery_embed.set_footer(text=f"{guild.name} • Account Delivery • ID #{account_id}")

    view = DispensedAccountView(
        buyer_id=customer.id if customer else None,
        account_id=account_id,
        guild_id=guild.id
    )

    mention_text = customer.mention if customer else ""
    await ctx.send(
        content=f"🎉 {mention_text} **Your account details are ready below:**" if mention_text else None,
        embed=delivery_embed,
        view=view
    )


@bot.hybrid_command(
    name="dispenserstock",
    aliases=["dispenserstats", "dispenserlist"],
    description="Check stock levels and dispense logs for this server's dispenser"
)
@commands.guild_only()
async def dispenser_stock_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Access Denied**: Only staff can view dispenser statistics.", delete_after=6)
        return

    stats = get_guild_dispenser_stats(ctx.guild.id)
    embed = discord.Embed(
        title=f"📦 {ctx.guild.name} • Dispenser Inventory & Stats",
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="📦 Available In Stock", value=f"**{stats['available']}** accounts", inline=True)
    embed.add_field(name="🏷️ Total Dispensed", value=f"**{stats['dispensed']}** accounts", inline=True)
    embed.add_field(name="📊 Total Loaded", value=f"**{stats['total']}** accounts", inline=True)

    recent = stats.get("recent_dispensed", [])
    if recent:
        lines = []
        for r in recent:
            user_str = r.get("dispensed_to_name") or f"<@{r.get('dispensed_to')}>"
            time_str = str(r.get("dispensed_at", "Unknown"))[:19].replace("T", " ")
            lines.append(f"• Account #{r.get('id')} ➔ **{user_str}** at `{time_str}`")
        embed.add_field(name="🕒 Recent Claims", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🕒 Recent Claims", value="No accounts have been claimed yet.", inline=False)

    embed.set_footer(text="AIO Bot • Use /addaccount to restock")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="cleardispenser",
    aliases=["resetdispenser"],
    description="Admin command: Reset or clear this server's account dispenser pool"
)
@commands.guild_only()
@app_commands.describe(action="Choose whether to reset claimed status or wipe all accounts")
@app_commands.choices(action=[
    app_commands.Choice(name="Reset Claimed Status (Restock All)", value="reset"),
    app_commands.Choice(name="Wipe Entire Dispenser (Delete All)", value="wipe")
])
async def clear_dispenser_cmd(ctx: commands.Context, action: str = "reset"):
    await safely_delete_message(ctx)
    if not is_admin_member(ctx.author) and not await is_bot_owner_safe(ctx.author):
        await ctx.send("⛔ **Admin Only**: Only server administrators can reset the dispenser.", delete_after=6)
        return

    dispenser = get_guild_dispenser(ctx.guild.id)
    if action == "wipe":
        dispenser["accounts"] = []
        save_server_dispensers()
        await ctx.send(f"🗑️ All dispenser accounts for **{ctx.guild.name}** have been completely deleted.", delete_after=8)
    else:
        for a in dispenser.get("accounts", []):
            a["dispensed"] = False
            a["dispensed_to"] = None
            a["dispensed_to_name"] = None
            a["dispensed_at"] = None
        save_server_dispensers()
        count = len(dispenser.get("accounts", []))
        await ctx.send(f"🔄 Dispenser reset! **{count}** account(s) have been returned to available stock.", delete_after=8)


@bot.hybrid_command(
    name="setup-rules",
    aliases=["rules", "postrules", "setuprules"],
    description="Staff command: Post or refresh the official rules embed in #📜-rules"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to post rules in (defaults to #📜-rules or current channel)")
async def setup_rules_cmd(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: Staff permissions required to configure rules.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    target_ch = channel
    if not target_ch:
        target_ch = discord.utils.get(guild.text_channels, name="📜-rules") or discord.utils.get(guild.text_channels, name="rules")
    if not target_ch:
        target_ch = ctx.channel

    try:
        await target_ch.purge(limit=10)
    except Exception:
        pass

    try:
        await apply_read_only_overwrites(target_ch)
    except Exception:
        pass

    embed = build_rules_embed(guild)
    await target_ch.send(embed=embed)
    if target_ch.id != ctx.channel.id:
        await ctx.send(f"✅ Rules successfully posted in {target_ch.mention}!", delete_after=5)


@bot.hybrid_command(
    name="setup-announcements",
    aliases=["setupannouncements", "announcementschannel", "lockannouncements"],
    description="Staff command: Set up and lock down #📢-announcements so general members cannot type"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to configure as announcements (defaults to #📢-announcements)")
async def setup_announcements_cmd(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: Staff permissions required to configure announcements channel.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    target_ch = channel
    if not target_ch:
        target_ch = discord.utils.get(guild.text_channels, name="📢-announcements") or discord.utils.get(guild.text_channels, name="announcements")
    if not target_ch:
        cat = discord.utils.get(guild.categories, name="📌 INFORMATION") or discord.utils.get(guild.categories, name="📢 INFORMATION")
        try:
            target_ch = await guild.create_text_channel(
                "📢-announcements",
                category=cat,
                topic="Official server announcements and updates."
            )
        except Exception as e:
            await ctx.send(f"❌ Error creating announcements channel: {e}", delete_after=8)
            return

    ok = await apply_read_only_overwrites(target_ch)
    if ok:
        await ctx.send(f"✅ Successfully configured {target_ch.mention} as a read-only announcements channel (members cannot type).", delete_after=8)
    else:
        await ctx.send(f"⚠️ Channel found at {target_ch.mention}, but encountered an issue applying permissions.", delete_after=8)


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

    if target_ch:
        try:
            await apply_read_only_overwrites(target_ch)
        except Exception:
            pass

    panel_embed = build_ticket_panel_embed()
    await target_ch.send(embed=panel_embed, view=TicketLaunchView())
    await ctx.send(f"✅ Ticket launch panel ready at {target_ch.mention}!", delete_after=8)

@bot.hybrid_command(
    name="shop",
    aliases=["shopstatus", "shop-status", "storestatus", "store-status", "status"],
    description="Update shop status (OPEN/CLOSED), rename status channel with 🟢/🔴, and send notification embed"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(
    status="Choose OPEN (🟢) or CLOSED (🔴)",
    message="Optional staff announcement, hours, or notice",
    ping_everyone="Mention @everyone with the notification (default: False)"
)
@app_commands.choices(
    status=[
        app_commands.Choice(name="🟢 OPEN", value="open"),
        app_commands.Choice(name="🔴 CLOSED", value="closed"),
    ]
)
async def shop_cmd(
    ctx: commands.Context,
    status: str,
    message: Optional[str] = None,
    ping_everyone: Optional[bool] = False
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You must be a staff member or administrator to update shop status.", delete_after=6)
        return

    raw_status = status.strip().lower()
    if raw_status in ("open", "opened", "on", "yes", "true", "start"):
        is_open = True
    elif raw_status in ("closed", "close", "off", "no", "false", "stop"):
        is_open = False
    else:
        await ctx.send("❌ Invalid status. Please specify `open` or `closed`.\n*Example:* `/shop status:open message:Taking orders now!`", delete_after=8)
        return

    success, summary, target_ch, _ = await update_shop_status(
        guild=ctx.guild,
        is_open=is_open,
        author=ctx.author,
        message=message,
        ping_everyone=bool(ping_everyone)
    )

    await ctx.send(summary, delete_after=8)

@bot.hybrid_command(
    name="fix-channels",
    aliases=["fixchannels", "fix-mod-logs", "fixmodlogs", "restore-mod-logs", "fixmodlog"],
    description="Staff command: Automatically repair and restore channel names (e.g. #📜-mod-logs)"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def fix_channels_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: Staff permissions required to repair channels.", delete_after=6)
        return

    repaired = []
    guild = ctx.guild
    if guild:
        for ch in guild.text_channels:
            cname = ch.name.lower()
            if ("mod" in cname and "log" in cname) and (any(k in cname for k in ("open", "closed", "🟢", "🔴")) or ch.name != "📜-mod-logs"):
                old = ch.name
                try:
                    await ch.edit(name="📜-mod-logs", reason=f"Channel restored by {ctx.author}")
                    repaired.append(f"#{old} -> #📜-mod-logs")
                except Exception as e:
                    repaired.append(f"#{old} (Error: {e})")
            elif ("ticket" in cname and "log" in cname) and (any(k in cname for k in ("open", "closed", "🟢", "🔴")) or ch.name != "📁-ticket-logs"):
                old = ch.name
                try:
                    await ch.edit(name="📁-ticket-logs", reason=f"Channel restored by {ctx.author}")
                    repaired.append(f"#{old} -> #📁-ticket-logs")
                except Exception as e:
                    repaired.append(f"#{old} (Error: {e})")
            elif ("staff" in cname and "chat" in cname) and (any(k in cname for k in ("open", "closed", "🟢", "🔴")) or ch.name != "🛡️-staff-chat"):
                old = ch.name
                try:
                    await ch.edit(name="🛡️-staff-chat", reason=f"Channel restored by {ctx.author}")
                    repaired.append(f"#{old} -> #🛡️-staff-chat")
                except Exception as e:
                    repaired.append(f"#{old} (Error: {e})")
            elif ("receipt" in cname and "brag" in cname) or cname in ("receipt-brags", "🧾-receipt-brags"):
                old = ch.name
                try:
                    vouches_ch = discord.utils.get(guild.text_channels, name="⭐-vouches")
                    if not vouches_ch:
                        await ch.edit(name="⭐-vouches", topic="Customer vouches, reviews, feedback, and 5-star ratings.", reason=f"Renamed #{old} to #⭐-vouches by {ctx.author}")
                        repaired.append(f"#{old} -> #⭐-vouches")
                    else:
                        repaired.append(f"#{old} (Note: #⭐-vouches already exists)")
                except Exception as e:
                    repaired.append(f"#{old} (Error: {e})")
            elif ("food" in cname or "reward" in cname) and ("pizza" in cname or "🌮🍕" in ch.name):
                old = ch.name
                try:
                    await ch.edit(name="🌮-food-rewards", topic="Official rewards store — new methods coming soon!", reason=f"Updated store channel name by {ctx.author}")
                    repaired.append(f"#{old} -> #🌮-food-rewards")
                except Exception as e:
                    repaired.append(f"#{old} (Error: {e})")

        # Also ensure store panel is refreshed with Coming Soon embed
        food_ch = find_food_rewards_channel(guild)
        if food_ch:
            try:
                await refresh_channel_content(food_ch, ctx.author.id, clear_history=True)
                repaired.append(f"Refreshed store panel in {food_ch.mention} (Coming Soon)")
            except Exception:
                pass

        # Lock down announcement and board channels so general members cannot type
        try:
            locked = await audit_and_enforce_read_only_channels(guild)
            for lch in locked:
                repaired.append(f"Locked down #{lch} (read-only for members)")
        except Exception:
            pass

    if repaired:
        await ctx.send(f"✅ Successfully restored channels:\n" + "\n".join(f"• {r}" for r in repaired), delete_after=10)
    else:
        await ctx.send("✅ All server channels and panels are already properly configured.", delete_after=8)

@bot.hybrid_command(
    name="setup-status-channel",
    aliases=["setupstatus", "setupshopstatus", "setup-status"],
    description="Staff command: Set up or initialize the #🟢-shop-open status channel"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_status_channel_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to run setup.", delete_after=6)
        return

    guild = ctx.guild
    if not guild:
        return

    existing = find_shop_status_channel(guild)
    if existing:
        await apply_read_only_overwrites(existing)
        await update_shop_status(
            guild=guild,
            is_open=True,
            author=ctx.author,
            message="Welcome to the rewards store! Live status announcements will be posted here."
        )
        await ctx.send(f"✅ Existing status channel found at {existing.mention} and refreshed!", delete_after=8)
        return

    cat = None
    for c in guild.categories:
        if "savings" in c.name.lower() or "rewards" in c.name.lower():
            cat = c
            break
    if not cat:
        for c in guild.categories:
            if "info" in c.name.lower():
                cat = c
                break

    channel_name = "🟢-shop-open"
    try:
        new_ch = await guild.create_text_channel(
            channel_name,
            category=cat,
            topic="Live shop opening status and operational hours. Check here to see if orders are being accepted!"
        )
        await apply_read_only_overwrites(new_ch)
        await update_shop_status(
            guild=guild,
            is_open=True,
            author=ctx.author,
            message="Welcome to the rewards store! Live status announcements will be posted here."
        )
        await ctx.send(f"✅ Shop status channel created at {new_ch.mention}!", delete_after=8)
    except Exception as e:
        await ctx.send(f"❌ Error creating channel #{channel_name}: {e}", delete_after=8)

@bot.hybrid_command(
    name="sync-commands",
    aliases=["sync", "forcesync", "cleardupes", "cleardups"],
    description="Founder command: Force sync global slash commands and purge duplicate guild commands"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def sync_commands_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Administrator permission required to sync commands.", delete_after=6)
        return

    msg = await ctx.send("🔄 Purging duplicate guild commands & syncing global slash commands with Discord...")
    try:
        if ctx.guild:
            try:
                if is_cvs_guild(ctx.guild):
                    synced_g = await bot.tree.sync(guild=ctx.guild)
                else:
                    bot.tree.clear_commands(guild=ctx.guild)
                    await bot.tree.sync(guild=ctx.guild)
            except Exception as ge:
                print(f"Notice syncing guild commands: {ge}", file=sys.stderr)

        synced_global = await bot.tree.sync()
        status_text = (
            f"✅ Cleanly synced **{len(synced_global)}** global slash commands and configured server command trees!\n"
            f"*(Tip: If your Discord client still displays cached duplicates in the menu, press `Ctrl+R` or restart Discord to refresh.)*"
        )
        await msg.edit(content=status_text)
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
    embed.set_footer(text=f"AIO Bot • Authorized by {ctx.author.display_name}")
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
    welcome_embed.set_footer(text="AIO Bot • Optimizer Access")
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
        description=f"Revoked access for {member.mention} from {target_channel.mention}.",
        color=COLOR_WARN
    )
    embed.set_footer(text=f"AIO Bot • Action by {ctx.author.display_name}")
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
        f"• 🛍️ **SAVINGS & REWARDS**: {_m('🌮-food-rewards')}, {_m('🏷️-deals-and-savings')}, {_m('⭐-vouches')}\n"
        f"• 🎫 **SUPPORT**: {_m('📩-open-a-ticket')} *(with Ticket Panel!)*\n"
        "• 🔊 **VOICE CHANNELS**: `🔊 General Voice`, `🔊 Lounge 1`\n"
        f"• 🛡️ **STAFF ZONE**: {_m('🛡️-staff-chat')}, {_m('📜-mod-logs')}, {_m('🎛️-mod-panel')} *(staff-only control panel)*\n\n"
        "**Options Below:**\n"
        "• **Format & Clean Old Channels**: Sets up the blueprint AND wipes leftover/unformatted channels\n"
        "• **Format (Keep Old)**: Sets up the blueprint alongside existing channels"
    )

    embed = discord.Embed(
        title="🏗️ Server Layout Architect",
        description=desc,
        color=COLOR_PRIMARY
    )
    embed.set_footer(text="AIO Bot • Server Architect")
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
    embed.set_footer(text="AIO Bot • Channel Cleanup")
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
@app_commands.describe(channel="Channel to deploy the ticket panel in (defaults to current channel)")
async def post_ticket_panel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target_channel = channel or ctx.channel
    if is_protected_channel(target_channel):
        await ctx.send("🛡️ **Protected Channel:** `#form-automation` cannot be modified.", delete_after=6)
        return
    cname = target_channel.name.lower()
    if any(k in cname for k in ("shop-open", "shop-closed", "shop-status", "store-status")) or (("🟢" in cname or "🔴" in cname) and any(k in cname for k in ("shop", "status", "store"))):
        await ctx.send("⚠️ The support ticket panel cannot be deployed in the shop status channel. Please choose `#📩-open-a-ticket` or a support channel.", delete_after=8)
        return
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
    embed.set_footer(text="AIO Bot • Support Center")
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
        embed.set_footer(text="AIO Bot • Channel Manager")
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

@bot.hybrid_command(
    name="say",
    aliases=["echo", "repeat", "repost", "botmsg"],
    description="Reposts your message and any attached photos through the bot"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    message="Text to repost through the bot",
    photo="Optional photo/image to repost",
    photo2="Second optional photo/image"
)
async def say_cmd(
    ctx: commands.Context,
    message: Optional[str] = None,
    photo: Optional[discord.Attachment] = None,
    photo2: Optional[discord.Attachment] = None
):
    await safely_delete_message(ctx)
    attachments = []
    if ctx.message and ctx.message.attachments:
        attachments.extend(ctx.message.attachments)
    for p in (photo, photo2):
        if p and p not in attachments:
            attachments.append(p)

    if not message and not attachments:
        await ctx.send("❌ Please provide text or an attached photo to repost.", delete_after=6)
        return

    if ctx.interaction:
        await ctx.interaction.response.send_message("✅ Message successfully reposted!", ephemeral=True)

    await _do_say(ctx.channel, message, attachments)

@bot.hybrid_command(
    name="foodpanel",
    aliases=["rewardsstore", "fastfood", "foodaccounts"],
    description="Deploy the rewards store coming soon panel"
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="Channel to deploy the food rewards purchase panel in (defaults to current channel)")
async def post_food_panel(ctx, channel: Optional[discord.TextChannel] = None):
    await safely_delete_message(ctx)
    target = channel or ctx.channel
    embed = build_food_accounts_embed()
    view = FoodAccountPurchaseView()
    await target.send(embed=embed, view=view)
    if target.id != ctx.channel.id:
        await ctx.send(f"✅ Rewards store coming soon panel deployed to {target.mention}!", delete_after=5)

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
    embed.set_footer(text=f"AIO Bot • Confirmed by {ctx.author.display_name}")
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
    brand = "Taco Bell"
    amt = t_info.get("invoice_amount")
    if amt is None:
        amt = 10.0 if "taco" in brand.lower() else 0.0

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

    vouch_mention = get_channel_mention(ctx.guild, "⭐-vouches", get_channel_mention(ctx.guild, "vouches", "#⭐-vouches"))
    embed = discord.Embed(
        title="🎉 Order Fulfilled & Completed!",
        description=(
            f"Your order has been marked as completed by {ctx.author.mention}!\n\n"
            f"Thank you for shopping with us! If you loved the service, drop a vouch in {vouch_mention} ⭐\n\n"
            "You may click **Close Ticket** below when finished."
        ),
        color=0x9b59b6
    )
    if notes:
        embed.add_field(name="📝 Notes", value=notes.strip(), inline=False)
    embed.add_field(name="⏰ Completed At", value=f"<t:{int(time.time())}:R>", inline=True)
    embed.set_footer(text=f"AIO Bot • Fulfilled by {ctx.author.display_name}")
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
    embed.set_footer(text=f"AIO Bot • Sent by {ctx.author.display_name}")
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
        title="📦 Taco Bell Account Delivered",
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
    embed.set_footer(text=f"AIO Bot • Delivered by {ctx.author.display_name}")
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
    embed.set_footer(text="AIO Bot • Ticket Support")
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
        embed.set_footer(text="AIO Bot • Sales Tracker")
        await ctx.send(embed=embed)
        return

    if act in ("add", "new", "record", "create"):
        embed = discord.Embed(
            title="➕ Recording Orders",
            description=(
                "**To manually record an order into the sales tracker:**\n\n"
                "• **Via Slash Command:**\n"
                "  `/addorder customer:@user price:15.00 item:Taco Bell notes:Paid`\n\n"
                "• **Via Staff Control Center:**\n"
                "  Click the **➕ Add Order** button in `🎛️-mod-panel` to open the modal!"
            ),
            color=COLOR_PRIMARY
        )
        embed.set_footer(text="AIO Bot • Sales Tracker")
        await ctx.send(embed=embed)
        return

    embed = build_order_stats_embed(ctx.guild)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="addorder",
    aliases=["orderadd", "recordorder", "logorder", "neworder"],
    description="Staff command: Manually record an order in the completed sales tracker"
)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    customer="The customer (@mention, username, or user ID)",
    price="Order price/amount (e.g. 10.00, $15, 25.50)",
    item="Item or brand (e.g. Taco Bell, 2x Accounts)",
    notes="Optional order notes or payment reference (e.g. Paid via CashApp)"
)
async def addorder_cmd(
    ctx: commands.Context,
    customer: str,
    price: str,
    item: str,
    *,
    notes: Optional[str] = None
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or founders can record orders.", delete_after=6)
        return

    clean_price = price.replace("$", "").replace(",", "").strip()
    try:
        val = float(clean_price)
        if val < 0:
            raise ValueError()
    except ValueError:
        await ctx.send("❌ Invalid price. Please enter a valid dollar amount (e.g. `10.00` or `$15`).", delete_after=6)
        return

    rec = record_manual_order(
        guild_id=ctx.guild.id if ctx.guild else 0,
        customer_input=customer,
        price=val,
        item=item,
        completed_by_id=ctx.author.id,
        completed_by_name=str(ctx.author),
        notes=notes,
        guild=ctx.guild
    )

    cust_display = f"<@{rec['customer_id']}>" if rec.get('customer_id') else rec.get('customer_name', 'Customer')
    embed = discord.Embed(
        title="✅ Order Recorded",
        description=f"Order #{rec['order_id']:02d} added to sales tracker.",
        color=COLOR_SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Order ID", value=f"`#{rec['order_id']:02d}`", inline=True)
    embed.add_field(name="Customer", value=cust_display, inline=True)
    embed.add_field(name="Item / Brand", value=f"**{rec['brand']}**", inline=True)
    embed.add_field(name="Amount", value=f"**${rec['amount']:.2f}**", inline=True)
    embed.add_field(name="Logged By", value=ctx.author.mention, inline=True)
    if notes:
        embed.add_field(name="Notes", value=notes.strip(), inline=False)

    embed.set_footer(text="AIO Bot • Sales Tracker")
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
            title="🗑️ Order Removed",
            description=(
                f"Successfully removed Order **`#{removed.get('order_id', target_num):02d}`** ({removed.get('brand', 'Item')}) from stats.\n\n"
                f"• **Amount Reverted:** ${removed.get('amount', 0.0):.2f}\n"
                f"• **Remaining Completed Orders:** {remaining}\n"
                "• *This will no longer count towards completed order statistics.*"
            ),
            color=COLOR_WARN
        )
        embed.set_footer(text="AIO Bot • Sales Tracker")
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
            f"\n👑 **Role Hierarchy Tip:**\n"
            f"Under Discord rules, bots cannot move their own role via API.\n"
            f"To place yourself at the top: **Server Settings ➔ Roles ➔ Drag @Founder ABOVE @{bot_name}**."
        )

    embed = discord.Embed(
        title="🛡️ Server Roles Repaired",
        description=desc,
        color=COLOR_SUCCESS
    )
    embed.set_footer(text="AIO Bot • Role Management")
    await status_msg.edit(content=None, embed=embed)


# --- ROLE ASSIGNMENT & REMOVAL COMMANDS ---

@bot.hybrid_command(
    name="giverole",
    aliases=["addrole", "give-role", "roleadd", "assignrole"],
    description="Assign a server role to a member"
)
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(
    member="The member to receive the role",
    role="The role to assign",
    reason="Reason for granting the role (optional)"
)
async def giverole_cmd(
    ctx: commands.Context,
    member: discord.Member,
    role: discord.Role,
    *,
    reason: Optional[str] = "No reason provided"
):
    await safely_delete_message(ctx)
    if not (is_staff_or_admin(ctx.author) or getattr(ctx.author.guild_permissions, "manage_roles", False) or await bot.is_owner(ctx.author)):
        await ctx.send("⛔ Permission Denied: You need 'Manage Roles' permission or staff access to assign roles.", delete_after=6)
        return

    if not ctx.guild.me.guild_permissions.manage_roles:
        await ctx.send("❌ Bot Missing Permissions: The bot does not have 'Manage Roles' permission in this server.", delete_after=6)
        return

    if role.is_default() or role.is_integration() or role.is_bot_managed():
        await ctx.send(f"❌ Cannot assign {role.name}: Managed or default roles cannot be manually assigned.", delete_after=8)
        return

    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ Hierarchy Error: {role.mention} is higher than or equal to the bot's highest role. Move the bot's role higher in Server Settings.", delete_after=8)
        return

    if role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send(f"⛔ Hierarchy Error: You cannot assign a role ({role.mention}) equal to or higher than your own highest role.", delete_after=8)
        return

    if role in member.roles:
        await ctx.send(f"ℹ️ {member.mention} already possesses the {role.mention} role.", delete_after=6)
        return

    try:
        await member.add_roles(role, reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Role Granted", str(member), str(ctx.author), reason or "No reason provided", f"Role: {role.name} ({role.id})")
        embed = discord.Embed(
            title="🏷️ Role Granted",
            description=f"Assigned {role.mention} to {member.mention}.",
            color=role.color if role.color.value != 0 else COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 Target Member", value=f"{member.mention} • `ID: {member.id}`", inline=True)
        embed.add_field(name="🏷️ Role Assigned", value=f"{role.mention} • `{role.name}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="📋 Case Reference", value=f"`#CASE-{case_id:04d}`", inline=True)
        if reason and reason != "No reason provided":
            embed.add_field(name="📄 Reason", value=reason, inline=False)
        embed.set_footer(text="AIO Bot • Moderation", icon_url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Discord Forbidden: Bot lacks permission to assign this role.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error assigning role: {e}", delete_after=8)


@bot.hybrid_command(
    name="removerole",
    aliases=["takerole", "remove-role", "delrole", "roledel"],
    description="Remove a server role from a member"
)
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(
    member="The member to remove the role from",
    role="The role to remove",
    reason="Reason for removing the role (optional)"
)
async def removerole_cmd(
    ctx: commands.Context,
    member: discord.Member,
    role: discord.Role,
    *,
    reason: Optional[str] = "No reason provided"
):
    await safely_delete_message(ctx)
    if not (is_staff_or_admin(ctx.author) or getattr(ctx.author.guild_permissions, "manage_roles", False) or await bot.is_owner(ctx.author)):
        await ctx.send("⛔ Permission Denied: You need 'Manage Roles' permission or staff access to remove roles.", delete_after=6)
        return

    if not ctx.guild.me.guild_permissions.manage_roles:
        await ctx.send("❌ Bot Missing Permissions: The bot does not have 'Manage Roles' permission in this server.", delete_after=6)
        return

    if role.is_default() or role.is_integration() or role.is_bot_managed():
        await ctx.send(f"❌ Cannot remove {role.name}: Managed or default roles cannot be manually altered.", delete_after=8)
        return

    if role >= ctx.guild.me.top_role:
        await ctx.send(f"❌ Hierarchy Error: {role.mention} is higher than or equal to the bot's highest role. Move the bot's role higher in Server Settings.", delete_after=8)
        return

    if role >= ctx.author.top_role and ctx.author != ctx.guild.owner and not is_admin_member(ctx.author):
        await ctx.send(f"⛔ Hierarchy Error: You cannot remove a role ({role.mention}) equal to or higher than your own highest role.", delete_after=8)
        return

    if role not in member.roles:
        await ctx.send(f"ℹ️ {member.mention} does not have the {role.mention} role.", delete_after=6)
        return

    try:
        await member.remove_roles(role, reason=f"{reason} (by {ctx.author})")
        case_id = log_mod_case(ctx.guild.id, "Role Removed", str(member), str(ctx.author), reason or "No reason provided", f"Role: {role.name} ({role.id})")
        embed = discord.Embed(
            title="🏷️ Role Removed",
            description=f"Removed {role.mention} from {member.mention}.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 Target Member", value=f"{member.mention} • `ID: {member.id}`", inline=True)
        embed.add_field(name="🏷️ Role Removed", value=f"{role.mention} • `{role.name}`", inline=True)
        embed.add_field(name="🛡️ Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="📋 Case Reference", value=f"`#CASE-{case_id:04d}`", inline=True)
        if reason and reason != "No reason provided":
            embed.add_field(name="📄 Reason", value=reason, inline=False)
        embed.set_footer(text="AIO Bot • Moderation", icon_url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Discord Forbidden: Bot lacks permission to remove this role.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Error removing role: {e}", delete_after=8)


@bot.hybrid_group(
    name="role",
    description="Role management commands (give or remove roles)"
)
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
async def role_group(ctx: commands.Context):
    if ctx.invoked_subcommand is None:
        await ctx.send_help(ctx.command)

@role_group.command(name="give", aliases=["add"], description="Assign a role to a member")
@app_commands.describe(member="The member to receive the role", role="The role to assign", reason="Reason for granting the role (optional)")
async def role_give_subcmd(ctx: commands.Context, member: discord.Member, role: discord.Role, *, reason: Optional[str] = "No reason provided"):
    await giverole_cmd(ctx, member, role, reason=reason)

@role_group.command(name="remove", aliases=["take"], description="Remove a role from a member")
@app_commands.describe(member="The member to remove the role from", role="The role to remove", reason="Reason for removing the role (optional)")
async def role_remove_subcmd(ctx: commands.Context, member: discord.Member, role: discord.Role, *, reason: Optional[str] = "No reason provided"):
    await removerole_cmd(ctx, member, role, reason=reason)


# --- WELCOME SYSTEM COMMANDS ---

@bot.hybrid_command(
    name="testwelcome",
    aliases=["welcometest"],
    description="Preview or test the welcome message in the welcome channel or active channel"
)
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    member="Member to preview the welcome card for (defaults to yourself)"
)
async def testwelcome_cmd(ctx: commands.Context, member: Optional[discord.Member] = None):
    await safely_delete_message(ctx)
    target = member or ctx.author
    embed = build_welcome_embed(target)
    welcome_ch = get_welcome_channel(ctx.guild)
    if welcome_ch and welcome_ch.id != ctx.channel.id:
        try:
            await welcome_ch.send(content=f"*(Welcome Test Preview)* Welcome {target.mention}!", embed=embed)
            await ctx.send(f"✅ Welcome message test sent to {welcome_ch.mention}!", delete_after=6)
            return
        except Exception as e:
            await ctx.send(f"⚠️ Could not send to {welcome_ch.mention} ({e}). Displaying here:", delete_after=4)

    await ctx.send(content=f"*(Welcome Test Preview)* Welcome {target.mention}!", embed=embed)


@bot.hybrid_command(
    name="setup-welcome",
    aliases=["setupwelcome", "createwelcomechannel"],
    description="Staff command: Set up or configure the #👋-welcome channel"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_welcome_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only server staff or administrators can set up channels.", delete_after=6)
        return

    ch, created = await setup_welcome_channel(ctx.guild)
    if not ch:
        await ctx.send("❌ Failed to create or find welcome channel. Check bot permissions.", delete_after=6)
        return

    action = "Created new" if created else "Found existing"
    await ctx.send(f"✅ {action} welcome channel: {ch.mention}. New members will automatically receive a welcome card here!", delete_after=8)


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
        if getattr(ctx.command, "name", "") in CVS_COMMAND_NAMES:
            return
        await ctx.send(f"⛔ Permission Error: You do not meet the permission requirements for this command.", delete_after=8)
    else:
        print(f"❌ Command Error in '{ctx.command}': {type(error).__name__} | Details: {error}", file=sys.stderr)
        try:
            await ctx.send(f"❌ Error: `{type(error).__name__}: {error}`", delete_after=15)
        except Exception:
            pass

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(p.replace('_', ' ').title() for p in error.missing_permissions)
        msg = f"⛔ You need the **{missing}** permission to run that."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(p.replace('_', ' ').title() for p in error.missing_permissions)
        msg = f"❌ The bot needs the **{missing}** permission to execute this."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "⛔ This command can only be used inside a server channel."
    elif isinstance(error, app_commands.CheckFailure):
        if interaction.command and getattr(interaction.command, "name", "") in CVS_COMMAND_NAMES:
            return
        msg = "⛔ Permission Error: You do not meet the permission requirements for this command."
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


# --- VOUCHES & REVIEWS COMMANDS ---
@bot.hybrid_command(
    name="vouch",
    aliases=["review", "addvouch"],
    description="Leave an official customer review / vouch with star rating"
)
@commands.guild_only()
@app_commands.describe(
    rating="Star rating from 1 to 5",
    comment="Your review or feedback message",
    staff="Staff member who assisted you (optional)",
    proof="Screenshot proof or receipt attachment (optional)"
)
async def vouch_cmd(
    ctx: commands.Context,
    rating: int,
    comment: str,
    staff: Optional[discord.Member] = None,
    proof: Optional[discord.Attachment] = None
):
    await safely_delete_message(ctx)
    if rating < 1 or rating > 5:
        await ctx.send("❌ Rating must be an integer between 1 and 5 stars.", delete_after=6)
        return

    proof_url = proof.url if proof else None
    vouch = add_vouch(
        guild_id=ctx.guild.id,
        user_id=ctx.author.id,
        user_name=str(ctx.author),
        rating=rating,
        comment=comment,
        staff_id=staff.id if staff else None,
        proof_url=proof_url
    )

    ch = get_vouches_channel(ctx.guild)
    if not ch:
        cat = discord.utils.get(ctx.guild.categories, name="🛍️ SAVINGS & REWARDS") or discord.utils.get(ctx.guild.categories, name="💬 COMMUNITY")
        try:
            ch = await ctx.guild.create_text_channel("⭐-vouches", category=cat, topic="Customer reviews, feedback, and 5-star ratings.")
        except Exception:
            ch = None

    embed = build_vouch_embed(vouch, ctx.author)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception as e:
            print(f"⚠️ Error posting to vouches channel: {e}", file=sys.stderr)

    stars = "⭐" * rating
    await ctx.send(
        f"✅ **Thank you, {ctx.author.mention}!** Your {stars} ({rating}/5) review has been recorded." + (f" View it in {ch.mention}!" if ch else ""),
        delete_after=8
    )


# --- GIVEAWAY COMMANDS ---
@bot.hybrid_group(
    name="giveaway",
    aliases=["gw"],
    description="Host and manage server giveaways with customer & role requirements"
)
@commands.guild_only()
async def giveaway_group(ctx: commands.Context):
    if ctx.invoked_subcommand is None:
        await ctx.send_help(ctx.command)


@giveaway_group.command(
    name="start",
    description="Launch a new interactive giveaway with optional customer or role requirements"
)
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    prize="Prize to give away (e.g. $25 Domino's Account, Free Food Drop)",
    duration="Duration before drawing winners (e.g. 15m, 1h, 24h, 3d)",
    winners="Number of winners to draw (default 1)",
    customer_only="Whether only members with completed purchases / customer role can enter (default False)",
    required_role="Specific role required to enter (optional)",
    channel="Channel to host the giveaway in (defaults to current channel)"
)
async def giveaway_start_cmd(
    ctx: commands.Context,
    prize: str,
    duration: str,
    winners: int = 1,
    customer_only: bool = False,
    required_role: Optional[discord.Role] = None,
    channel: Optional[discord.TextChannel] = None
):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only staff or admins can create giveaways.", delete_after=6)
        return

    sec = parse_giveaway_duration(duration)
    if not sec or sec < 10 or sec > 86400 * 30:
        await ctx.send("❌ Invalid duration. Please provide a duration between 10s and 30d (e.g. `15m`, `2h`, `1d`).", delete_after=8)
        return

    target_ch = channel or (get_giveaways_channel(ctx.guild) if ctx.guild else None) or ctx.channel
    if not isinstance(target_ch, discord.TextChannel):
        await ctx.send("❌ Giveaways can only be hosted in text channels.", delete_after=6)
        return

    if is_protected_channel(target_ch):
        await ctx.send("🛡️ **Protected Channel:** Giveaways cannot be hosted in `#form-automation`.", delete_after=6)
        return

    winners_count = max(1, min(20, winners))
    end_unix = int(datetime.now(timezone.utc).timestamp()) + sec

    embed = discord.Embed(
        title=f"🎉 Giveaway • {prize}",
        description=(
            "🎁 Click the button below to participate!\n\n"
            f"• 🏆 **Prize:** `{prize}`\n"
            f"• 👥 **Winners:** `{winners_count}`\n"
            f"• ⏳ **Ends:** <t:{end_unix}:R> (<t:{end_unix}:f>)\n"
            f"• 👤 **Host:** {ctx.author.mention}"
        ),
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc)
    )

    reqs = []
    if customer_only:
        reqs.append("• ⭐ **Verified Customers Only** *(Completed order or Customer role)*")
    if required_role:
        reqs.append(f"• 🏷️ **Required Role:** {required_role.mention}")
    if reqs:
        embed.add_field(name="🔒 Entry Requirements", value="\n".join(reqs), inline=False)
    else:
        embed.add_field(name="👥 Entry Requirements", value="• 🌐 **Open to Everyone!**", inline=False)

    embed.set_footer(text="AIO Bot • Giveaway System", icon_url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None)

    view = GiveawayEntryView(count=0)
    msg = await target_ch.send(embed=embed, view=view)

    giveaways_db[str(msg.id)] = {
        "channel_id": target_ch.id,
        "guild_id": ctx.guild.id,
        "prize": prize,
        "winners_count": winners_count,
        "end_time": end_unix,
        "host_id": ctx.author.id,
        "customer_only": customer_only,
        "required_role_id": required_role.id if required_role else None,
        "participants": [],
        "ended": False
    }
    save_giveaways()

    if target_ch.id != ctx.channel.id:
        await ctx.send(f"✅ Giveaway created in {target_ch.mention}!", delete_after=6)


@giveaway_group.command(
    name="end",
    description="Instantly end a giveaway and draw winners"
)
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(message_id="Message ID of the giveaway to end (optional, defaults to latest in channel)")
async def giveaway_end_cmd(ctx: commands.Context, message_id: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only staff can manage giveaways.", delete_after=6)
        return

    target_id = message_id
    if not target_id:
        for mid, g in reversed(list(giveaways_db.items())):
            if g.get("channel_id") == ctx.channel.id and not g.get("ended"):
                target_id = mid
                break

    if not target_id or target_id not in giveaways_db:
        await ctx.send("❌ No active giveaway found matching that message ID.", delete_after=6)
        return

    await finish_giveaway(target_id)
    await ctx.send("✅ Giveaway ended and winners drawn!", delete_after=6)


@giveaway_group.command(
    name="reroll",
    description="Reroll a new winner for an ended giveaway"
)
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(message_id="Message ID of the giveaway to reroll (optional, defaults to latest in channel)")
async def giveaway_reroll_cmd(ctx: commands.Context, message_id: Optional[str] = None):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only staff can manage giveaways.", delete_after=6)
        return

    target_id = message_id
    if not target_id:
        for mid, g in reversed(list(giveaways_db.items())):
            if g.get("channel_id") == ctx.channel.id:
                target_id = mid
                break

    if not target_id or target_id not in giveaways_db:
        await ctx.send("❌ No giveaway found for that ID.", delete_after=6)
        return

    g = giveaways_db[target_id]
    participants = list(set(g.get("participants", [])))
    if not participants:
        await ctx.send("❌ No participants entered this giveaway to reroll from.", delete_after=6)
        return

    new_winner_id = random.choice(participants)
    prize = g.get("prize", "Prize")
    await ctx.send(
        f"🎲 **Reroll Winner:** Congratulations <@{new_winner_id}>! You are the new winner of **{prize}**! 🎁"
    )


@giveaway_group.command(
    name="channel",
    aliases=["createchannel", "setupchannel", "makechannel"],
    description="Staff command: Automatically create and configure the dedicated #🎉-giveaways channel"
)
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def giveaway_channel_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to create channels.", delete_after=6)
        return

    if not ctx.guild:
        return

    ch, created = await setup_giveaways_channel(ctx.guild)
    if not ch:
        await ctx.send("❌ Error creating giveaways channel. Please verify the bot has Manage Channels permission.", delete_after=8)
        return

    if created:
        await ctx.send(f"🎉 **Dedicated Giveaways Channel Ready:** {ch.mention} has been created with read-only permissions for members!", delete_after=10)
    else:
        await ctx.send(f"ℹ️ Giveaways channel already exists at {ch.mention}.", delete_after=8)


@bot.hybrid_command(
    name="setup-giveaways",
    aliases=["setupgiveaways", "creategiveawaychannel", "giveawaychannel", "makegiveawaychannel"],
    description="Staff command: Automatically create and configure the dedicated #🎉-giveaways channel"
)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def setup_giveaways_standalone_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Permission Denied: You need Staff or Manage Channels permissions to create channels.", delete_after=6)
        return

    if not ctx.guild:
        return

    ch, created = await setup_giveaways_channel(ctx.guild)
    if not ch:
        await ctx.send("❌ Error creating giveaways channel. Please verify the bot has Manage Channels permission.", delete_after=8)
        return

    if created:
        await ctx.send(f"🎉 **Dedicated Giveaways Channel Ready:** {ch.mention} has been created with read-only permissions for members!", delete_after=10)
    else:
        await ctx.send(f"ℹ️ Giveaways channel already exists at {ch.mention}.", delete_after=8)


# --- AUTOMOD SHIELD COMMANDS ---
@bot.hybrid_group(
    name="automod",
    aliases=["shield"],
    description="Staff command: Manage auto-moderation filters and shields"
)
@commands.guild_only()
async def automod_group(ctx: commands.Context):
    if ctx.invoked_subcommand is None:
        await ctx.send_help(ctx.command)


@automod_group.command(
    name="status",
    description="View active auto-moderation protection status"
)
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
async def automod_status_cmd(ctx: commands.Context):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only staff can view automod configuration.", delete_after=6)
        return

    invites = automod_config_db.get("invites_blocked", True)
    scams = automod_config_db.get("scams_blocked", True)
    words = get_filter_words(ctx.guild.id)

    embed = discord.Embed(
        title="🛡️ Auto-Mod Protection Shield",
        description="Real-time status of automated security and anti-raid filters:",
        color=COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name="🔗 Discord Invite Blocker",
        value="🟢 **Enabled** (Unauthorized invites auto-deleted)" if invites else "🔴 **Disabled**",
        inline=True
    )
    embed.add_field(
        name="🚨 Phishing & Scam Link Shield",
        value="🟢 **Enabled** (Suspicious links auto-deleted)" if scams else "🔴 **Disabled**",
        inline=True
    )
    embed.add_field(
        name="🚫 Blacklisted Word Filter",
        value=f"🟢 **Active** ({len(words)} blacklisted terms)" if words else "⚪ **No custom words set**",
        inline=True
    )
    embed.add_field(
        name="🛡️ Protected Channels",
        value="`#form-automation` and staff channels are strictly exempted from all auto-mod deletions.",
        inline=False
    )
    embed.set_footer(text="AIO Bot • Auto-Mod Shield")
    await ctx.send(embed=embed)


@automod_group.command(
    name="toggle",
    description="Enable or disable a specific auto-mod shield filter"
)
@commands.has_permissions(manage_guild=True)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(setting="Filter to toggle: 'invites' or 'scams'")
async def automod_toggle_cmd(ctx: commands.Context, setting: Literal["invites", "scams"]):
    await safely_delete_message(ctx)
    if not is_staff_or_admin(ctx.author) and not await bot.is_owner(ctx.author):
        await ctx.send("⛔ Only staff can configure automod settings.", delete_after=6)
        return

    s = setting.lower().strip()
    if s == "invites":
        new_val = not automod_config_db.get("invites_blocked", True)
        automod_config_db["invites_blocked"] = new_val
        save_automod_config()
        state_str = "🟢 **Enabled**" if new_val else "🔴 **Disabled**"
        await ctx.send(f"🛡️ Discord invite link blocker is now {state_str}.", delete_after=8)
    elif s == "scams":
        new_val = not automod_config_db.get("scams_blocked", True)
        automod_config_db["scams_blocked"] = new_val
        save_automod_config()
        state_str = "🟢 **Enabled**" if new_val else "🔴 **Disabled**"
def setup_command_scoping():
    """
    Safeguard ensuring private CVS tools, accounts, and server formats
    are strictly scoped to CVS_ALLOWED_GUILD_IDS, leaving only Moderation and
    the Ticket System accessible globally in guest/friend servers.
    """
    for cmd_name in CVS_COMMAND_NAMES:
        cmd = bot.get_command(cmd_name)
        if not cmd:
            continue
        if cmd.app_command:
            bot.tree.remove_command(cmd_name)
            for gid in CVS_ALLOWED_GUILD_IDS:
                try:
                    _orig_tree_add_command(cmd.app_command, guild=discord.Object(id=gid))
                except Exception:
                    pass

setup_command_scoping()


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
