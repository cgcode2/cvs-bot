# 1. PYTHON 3.13 AUDIOOP CRASH FIX (Must be at the absolute top)
import sys
from types import ModuleType

if 'audioop' not in sys.modules:
    fake_audioop = ModuleType('audioop')
    fake_audioop.error = Exception
    sys.modules['audioop'] = fake_audioop

# 2. CORE ENGINE IMPORTS
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal
import asyncio
import itertools
import os
import json
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

# 3. DISCORD BOT ENGINE SETUP
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for whocansee member audits and private gateway routing

class CouponBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        
    async def setup_hook(self):
        self.tree.on_error = on_app_command_error

bot = CouponBot()

# CRITICAL SECURITY FIX: Enforce persistent directory paths to mount to Railway volumes
DATA_DIR = "/app/data" if os.path.exists("/app/data") else "."
SESSION_CHANNELS_FILE = os.path.join(DATA_DIR, "session_channels.json")
SAVINGS_FILE = os.path.join(DATA_DIR, "savings_data.json")
CARTS_FILE = os.path.join(DATA_DIR, "active_carts.json")
BUG_VAULT_FILE = os.path.join(DATA_DIR, "bug_telemetry_vault.json")

STAFF_ROLE_NAME = "Staff"
NY_TZ = ZoneInfo("America/New_York")  # Explicitly locks bot logs to Eastern Standard Time

# CRITICAL TRANSACTION SYNC LOCKS: Protects data states from overwrite race conditions
db_lock = asyncio.Lock()
vault_lock = asyncio.Lock()

# Synchronous versions for boot-time initialization (before async loop)
def load_json_file_sync(filepath, default_value):
    """Sync load for startup before event loop exists."""
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, FileNotFoundError) as e:
            print(f"⚠️ Failed to load {filepath}: {e}. Using default.", file=sys.stderr)
            return default_value
    return default_value

def save_json_file_sync(filepath, data):
    """Sync save for startup before event loop exists."""
    try:
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"❌ Storage Write Failure on {filepath}: {e}", file=sys.stderr)

# UNIFIED TRANSACTION LAYER
async def update_channel_session(channel_id, update_func, is_test=False):
    """Executes a thread-safe atomic read-modify-write database transaction."""
    async with db_lock:
        if os.path.exists(CARTS_FILE):
            try:
                with open(CARTS_FILE, "r") as f:
                    carts_db = json.load(f)
            except Exception:
                carts_db = {}
        else:
            carts_db = {}

        ch_key = str(channel_id)
        if is_test:
            ch_key = f"{ch_key}_test"

        if ch_key not in carts_db:
            carts_db[ch_key] = {
                "items": [], 
                "coupons": [], 
                "cart_message": None, 
                "is_test": is_test,
                "last_optimization": None,
                "audit_log": []
            }

        session_data = carts_db[ch_key]
        updated_session = await update_func(session_data)
        carts_db[ch_key] = updated_session

        try:
            os.makedirs(os.path.dirname(CARTS_FILE) or ".", exist_ok=True)
            with open(CARTS_FILE, "w") as f:
                json.dump(carts_db, f, indent=2)
        except Exception as e:
            print(f"❌ Transaction Storage Write Failure: {e}", file=sys.stderr)
            
        return updated_session

async def get_channel_session_immutable(channel_id, is_test=False):
    """Safe read-only view of a session block under active lock control maps."""
    async with db_lock:
        if os.path.exists(CARTS_FILE):
            try:
                with open(CARTS_FILE, "r") as f:
                    carts_db = json.load(f)
            except Exception:
                carts_db = {}
        else:
            carts_db = {}

        ch_key = str(channel_id)
        if is_test:
            ch_key = f"{ch_key}_test"

        return carts_db.get(ch_key, {
            "items": [], "coupons": [], "cart_message": None, 
            "is_test": is_test, "last_optimization": None, "audit_log": []
        })

async def log_action(channel_id, command_name, input_details, output_summary, is_test=False):
    """Records chronological metrics footprints safely inside the atomic transaction loop."""
    async def _logger(session):
        timestamp = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {
            "timestamp": timestamp,
            "command": command_name,
            "input": input_details,
            "output": output_summary,
            "current_cart_state": {
                "items": list(session.get("items", [])),
                "coupons": list(session.get("coupons", []))
            }
        }
        if "audit_log" not in session:
            session["audit_log"] = []
        session["audit_log"].append(log_entry)
        return session
    await update_channel_session(channel_id, _logger, is_test=is_test)

# SILENT COMPILATION CRASH VAULT TELEMETRY PIPELINE
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if hasattr(error, "original"):
        error = error.original

    if isinstance(error, discord.errors.NotFound) and "Unknown interaction" in str(error):
        print("⚠️ Suppressed an interaction lifespans race delay.", file=sys.stderr)
        return

    trace_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    print(f"🚨 Bug Suppressed and Cached to Vault:\n{trace_str}", file=sys.stderr)

    error_embed = discord.Embed(
        title="⚠️ System Telemetry Notice",
        description="An unexpected exception occurred while executing this command. The telemetry log has been compiled and cached silently to the database storage vaults.",
        color=0xe74c3c
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
    except Exception:
        pass

    async with vault_lock:
        try:
            if os.path.exists(BUG_VAULT_FILE):
                with open(BUG_VAULT_FILE, "r") as f:
                    vault = json.load(f)
            else:
                vault = []
                
            options = interaction.data.get("options", []) if interaction.data else []
            arg_summary = ", ".join([f"{opt.get('name')}: {opt.get('value')}" for opt in options]) or "None"
            
            bug_entry = {
                "timestamp": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "command": f"/{interaction.command.name if interaction.command else 'Unknown'}",
                "inputs": arg_summary,
                "channel_id": str(interaction.channel_id),
                "traceback": trace_str
            }
            vault.append(bug_entry)
            
            with open(BUG_VAULT_FILE, "w") as f:
                json.dump(vault, f, indent=2)
        except Exception as e:
            print(f"❌ Failed caching exception to storage file: {e}", file=sys.stderr)

session_channels = load_json_file_sync(SESSION_CHANNELS_FILE, {})

async def get_or_create_user_coupon_channel(guild, user):
    existing_id = session_channels.get(str(user.id))
    if existing_id:
        channel = guild.get_channel(existing_id)
        if channel is not None:
            return channel, False

    safe_name = "".join(c for c in user.name.lower() if c.isalnum() or c in ("-", "_")) or str(user.id)
    channel_name = f"coupons-{safe_name}"[:100]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True, manage_messages=True),
    }
    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True)

    reference_channel = discord.utils.get(guild.text_channels, name="cvs-coupon-optimizer")
    parent_category = reference_channel.category if reference_channel else guild.text_channels[0].category

    channel = await guild.create_text_channel(
        name=channel_name,
        category=parent_category,
        overwrites=overwrites,
        reason=f"Private coupon optimizer channel for {user}"
    )

    session_channels[str(user.id)] = channel.id
    try:
        with open(SESSION_CHANNELS_FILE, "w") as f:
            json.dump(session_channels, f, indent=2)
    except Exception:
         pass

    welcome_embed = discord.Embed(
        title="🎯 Your Private Coupon Optimizer Channel",
        description=(
            f"Hey {user.mention}! This is your own space to run the full coupon flow "
            "without cluttering the main chat. Only you"
            + (f" and the `{STAFF_ROLE_NAME}` role" if staff_role else "")
            + " can see this channel.\n\nRun `/help` here anytime for the full command list."
        ),
        color=0xcc0000
    )
    await channel.send(embed=welcome_embed)
    return channel, True

COLOR_NAMES = {
    "red": 0xe74c3c, "dark red": 0x992d22, "orange": 0xe67e22, "yellow": 0xf1c40f,
    "gold": 0xf1c40f, "green": 0x2ecc71, "dark green": 0x1f8b4c, "teal": 0x1abc9c,
    "cyan": 0x00ffff, "blue": 0x3498db, "dark blue": 0x206694, "navy": 0x2c3e50,
    "purple": 0x9b59b6, "dark purple": 0x71368a, "magenta": 0xe91e63, "pink": 0xff69b4,
    "brown": 0x795548, "black": 0x23272a, "white": 0xffffff, "gray": 0x95a5a6,
    "grey": 0x95a5a6, "dark gray": 0x2c2f33, "dark grey": 0x2c2f33, "lime": 0x32cd32,
    "blurple": 0x5865f2, "fuchsia": 0xff00ff, "indigo": 0x4b0082, "maroon": 0x800000,
}

COUPON_COSTS = {
    2.00: 0.10, 3.00: 0.20, 4.00: 0.35, 5.00: 0.50, 6.00: 0.65,
    7.00: 0.80, 8.00: 1.00, 9.00: 1.15, 10.00: 1.30, 11.00: 1.50, 12.00: 1.75,
}
HALF_OFF_COST = 0.01

def coupon_cost(coupon_val):
    if coupon_val == "half":
        return HALF_OFF_COST
    return COUPON_COSTS.get(round(coupon_val, 2), 0.0)

def coupon_label(coupon_val):
    return "50% Off One Item" if coupon_val == "half" else f"${coupon_val:.2f} Off"

def resolve_color(color_input):
    if not color_input:
        return discord.Color.default()
    key = color_input.strip().lower()
    if key in COLOR_NAMES:
        return discord.Color(COLOR_NAMES[key])
    try:
        return discord.Color(int(key.lstrip('#'), 16))
    except ValueError:
        return None

async def send_cart_embed(interaction: discord.Interaction, embed, session, channel_id, is_test=False):
    old_msg_id = session.get("cart_message")
    if old_msg_id is not None:
        try:
            old_msg = await interaction.channel.fetch_message(old_msg_id)
            await old_msg.delete()
        except Exception:
            pass
    
    try:
        if interaction.response.is_done():
            msg = await interaction.followup.send(embed=embed)
        else:
            await interaction.response.defer()
            msg = await interaction.followup.send(embed=embed)
            
        async def _msg_updater(s):
            s["cart_message"] = msg.id
            return s
        await update_channel_session(channel_id, _msg_updater, is_test=is_test)
    except Exception as e:
        print(f"⚠️ Messaging wrapper pipeline bypass: {e}", file=sys.stderr)

def group_due(group_items, coupon_val):
    if not group_items:
        return 0.0
    group_sum = sum(item['price'] for item in group_items)
    if coupon_val == "half":
        return group_sum - 0.5 * max(item['price'] for item in group_items)
    return max(0.0, group_sum - coupon_val)

def calculate_best_bundles(items, coupons):
    num_groups = len(coupons)
    if num_groups == 0: 
        return sum(item['price'] for item in items), {0: items}
    best_total_due = float('inf')
    best_distribution = None

    for distribution in itertools.product(range(num_groups), repeat=len(items)):
        groups = {i: [] for i in range(num_groups)}
        for item_idx, group_idx in enumerate(distribution):
            groups[group_idx].append(items[item_idx])
        
        current_total_due = 0
        for group_idx, group_items in groups.items():
            coupon_val = coupons[group_idx]
            current_total_due += group_due(group_items, coupon_val)

        if current_total_due < best_total_due:
            best_total_due = current_total_due
            best_distribution = groups
    return best_total_due, best_distribution

@bot.event
async def on_ready():
    print(f'🤖 Coupon Calculator is logged into Railway!')
    try:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        print(f'⚡ Direct command tree injection successful!')
    except Exception as e:
        print(f'⚠️ Direct sync failed: {e}', file=sys.stderr)

# --- OWNER ONLY DIAGNOSTIC OPERATIONS COMMANDS ---

@bot.tree.command(name="run-stress-test", description="High-Intensity Suite: Automates a 5-cycle loop firing 35 total structural additions")
async def run_stress_test(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    # FIXED: Defer as ephemeral on line 1, and reply purely via followups to prevent 40060 errors
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("⏳ **Initiating High-Intensity Stress Test Loop...** Sandbox cleared. Firing rapid writes...", ephemeral=True)
    
    async def _test_clear(s):
        s["items"] = []
        s["coupons"] = []
        s["cart_message"] = None
        s["last_optimization"] = None
        return s
    await update_channel_session(interaction.channel.id, _test_clear, is_test=True)

    for cycle in range(1, 6):
        mock_items = [
            (f"ItemA_C{cycle}", 2.50 * cycle),
            (f"ItemB_C{cycle}", 1.15 * cycle),
            (f"ItemC_C{cycle}", 3.99 * cycle),
            (f"ItemD_C{cycle}", 0.75 * cycle)
        ]
        mock_coupons = [float(2 * cycle), float(cycle), "half"]

        async def _loop_add(s, items=mock_items):
            for name, price in items:
                s["items"].append({"name": name, "price": price})
            return s
        await update_channel_session(interaction.channel.id, lambda s: _loop_add(s, mock_items), is_test=True)

        async def _loop_coupons(s, coupons=mock_coupons):
            s["coupons"].extend(coupons)
            s["coupons"].sort(key=lambda c: -1 if c == "half" else c, reverse=True)
            return s
        await update_channel_session(interaction.channel.id, lambda s: _loop_coupons(s, mock_coupons), is_test=True)
        
        await asyncio.sleep(0.1)

    # FIXED: Execute the math evaluation natively inside the stress context to bypass dual defer exceptions
    session = await get_channel_session_immutable(interaction.channel.id, is_test=True)
    items = session["items"]
    coupons = session["coupons"]
    
    total_due, bundling = await asyncio.to_thread(calculate_best_bundles, items, coupons)
    
    async def _optimize_saver(s):
        s["last_optimization"] = {
            "items": items, "coupons": coupons, "total_due": total_due, "bundling": bundling
        }
        return s
    await update_channel_session(interaction.channel.id, _optimize_saver, is_test=True)
    
    embed = discord.Embed(title="🧪 [STRESS TEST] Optimized Checkout Strategy", color=0x9b59b6)
    for idx, coupon_val in enumerate(coupons):
        group_items = bundling.get(idx, [])
        if group_items:
            item_details = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in group_items])
            subtotal = sum(item['price'] for item in group_items)
            due = group_due(group_items, coupon_val)
            embed.add_field(
                name=f"Transaction {idx+1}: Use {coupon_label(coupon_val)} Coupon",
                value=f"{item_details}\n*Subtotal: ${subtotal:.2f}* ➔ **Due: ${due:.2f}**",
                inline=False
            )
    embed.add_field(name="📊 Final Register Total Due", value=f"## **${total_due:.2f}**", inline=False)
    
    await log_action(interaction.channel.id, "optimize", "Stress Loop", f"Calculated total due: ${total_due:.2f}", is_test=True)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="export-bug-logs", description="Admin Tool: Packages all silent exceptions since your last check and flushes the vault disk cache")
async def export_bug_logs(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    async with vault_lock:
        if not os.path.exists(BUG_VAULT_FILE):
            await interaction.followup.send("📭 The vault is empty! No system bugs have been logged since your last flush.", ephemeral=True)
            return
            
        try:
            with open(BUG_VAULT_FILE, "r") as f:
                vault_data = json.load(f)
        except Exception:
            vault_data = []

        if not vault_data:
            await interaction.followup.send("📭 The vault is empty! No system bugs have been logged since your last flush.", ephemeral=True)
            return

        filename = "compiled_bug_telemetry.json"
        with open(filename, "w") as f:
            json.dump(vault_data, f, indent=2)

        try:
            os.remove(BUG_VAULT_FILE)
        except Exception:
            pass

    await interaction.followup.send(
        "📦 **Vault Compilation Successful!**",
        file=discord.File(filename),
        ephemeral=True
    )
    os.remove(filename)

@bot.tree.command(name="delete-last-trip", description="Emergency Undo: Wipes the last checked-out trip and reverses lifetime statistics completely")
async def delete_last_trip(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with db_lock:
        savings_tracker = load_json_file_sync(SAVINGS_FILE, {
            "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
            "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": []
        })
        trips = savings_tracker.get("trips", [])

        if not trips:
            await interaction.followup.send("📭 History ledger contains no valid trips to erase.", ephemeral=True)
            return

        removed_trip = trips.pop()
        savings_tracker["trip_count"] = max(0, savings_tracker["trip_count"] - 1)
        savings_tracker["total_full_price"] = max(0.0, savings_tracker["total_full_price"] - removed_trip["subtotal"])
        savings_tracker["total_paid"] = max(0.0, savings_tracker["total_paid"] - removed_trip["total_due"])
        savings_tracker["total_coupon_cost"] = max(0.0, savings_tracker["total_coupon_cost"] - removed_trip["coupon_spend"])
        savings_tracker["total_net_saved"] = max(0.0, savings_tracker["total_net_saved"] - removed_trip["net_saved"])

        try:
            with open(SAVINGS_FILE, "w") as f:
                json.dump(savings_tracker, f, indent=2)
        except Exception:
            pass

    embed = discord.Embed(title="↩️ Checkout Trip Successfully Erased", color=0xe74c3c)
    embed.add_field(name="Removed Trip Date", value=f"`{removed_trip['date']} @ {removed_trip.get('time', '—')}`", inline=False)
    embed.add_field(name="Reversed Net Savings", value=f"-${removed_trip['net_saved']:.2f}", inline=True)
    embed.add_field(name="Adjusted Lifetime Total", value=f"${savings_tracker['total_net_saved']:.2f}", inline=True)
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="export-session-logs", description="Compiles and spits out every command execution and state footprint from this session")
@app_commands.describe(mode="Choose whether to fetch regular shopping logs or test tracks logs")
async def export_session_logs(interaction: discord.Interaction, mode: Literal["standard", "test"] = "standard"):
    await interaction.response.defer(ephemeral=True)
    is_test = (mode == "test")
    session = await get_channel_session_immutable(interaction.channel.id, is_test=is_test)
    logs = session.get("audit_log", [])
    
    if not logs:
        await interaction.followup.send("📭 No operational events logged inside this channel sector yet.", ephemeral=True)
        return
        
    raw_payload = json.dumps(logs, indent=2)
    filename = f"audit_log_{interaction.channel.id}_{mode}.json"
    with open(filename, "w") as f:
        f.write(raw_payload)
        
    await interaction.followup.send(
        "📦 **Diagnostic Blueprint Compiled!**",
        file=discord.File(filename),
        ephemeral=True
    )
    os.remove(filename)

@bot.tree.command(name="import-history", description="Emergency Recovery Tool: Force paste a text block backup directly back into memory data files")
@app_commands.describe(backup_payload="Paste your exported text string profile here")
async def import_history(interaction: discord.Interaction, backup_payload: str):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    async with db_lock:
        try:
            parsed_data = json.loads(backup_payload)
            savings_tracker = load_json_file_sync(SAVINGS_FILE, {
                "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
                "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": []
            })
            savings_tracker.update(parsed_data)
            with open(SAVINGS_FILE, "w") as f:
                json.dump(savings_tracker, f, indent=2)
            await interaction.followup.send("✅ History safely injected back into persistent storage drive!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed parsing payload structure: `{e}`", ephemeral=True)

@bot.tree.command(name="export-history", description="Wipe-proofing backup tool: Compresses and exports database ledger arrays directly to DMs")
async def export_history(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with db_lock:
        try:
            savings_tracker = load_json_file_sync(SAVINGS_FILE, {
                "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
                "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": []
            })
            raw_json = json.dumps(savings_tracker, indent=2)
            if len(raw_json) < 1900:
                await interaction.user.send(f"📦 **Backup Data Ledger String:**\n```json\n{raw_json}\n```")
            else:
                with open("history_backup.json", "w") as f:
                    f.write(raw_json)
                await interaction.user.send("📦 **Backup Ledger Data payload file:**", file=discord.File("history_backup.json"))
                os.remove("history_backup.json")
            await interaction.followup.send("✅ Safe backup profile exported and channeled straight to your DMs!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Operational backup pipeline break: `{e}`", ephemeral=True)

@bot.tree.command(name="setup", description="Create the private CVS coupon optimizer channel")
async def setup_channel(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer()
    guild = interaction.guild
    owner = guild.owner or await guild.fetch_member(guild.owner_id)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True, manage_messages=True)
    }
    
    channel_name = "cvs-coupon-optimizer"
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
    if existing_channel:
        await interaction.followup.send(f"⚠️ A channel named `#{channel_name}` already exists!")
        return
        
    new_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
    welcome_embed = discord.Embed(
        title="🎯 CVS Coupon Optimizer Room",
        description="This is your secure, private command base for calculated shopping bundles! Run `/begin` to get your personal workspace.",
        color=0xcc0000
    )
    await new_channel.send(embed=welcome_embed)
    await interaction.followup.send(f"✅ Secure channel {new_channel.mention} successfully built!")

@bot.tree.command(name="permit", description="Grant a member access to the main coupon optimizer channel")
@app_commands.describe(member="The user you want to grant access to")
async def permit_user(interaction: discord.Interaction, member: discord.Member):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer()
    channel_name = "cvs-coupon-optimizer"
    channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
    
    if not channel:
        await interaction.followup.send("❌ Error: The `#cvs-coupon-optimizer` channel does not exist yet. Run `/setup` first!")
        return
        
    await channel.set_permissions(member, view_channel=True, send_messages=True, read_messages=True)
    await interaction.followup.send(f"✅ Granted access to {member.mention} to use the optimizer room!")

# --- CORE USER SLASH COMMANDS ENGINE ---

def build_help_embed(author_perms: discord.Permissions, is_owner: bool) -> discord.Embed:
    embed = discord.Embed(
        title="📖 CVS Coupon Calculator — Help Menu", 
        description="Follow this quick blueprint to maximize your coupon values and slash your out-of-pocket register total.", 
        color=0xcc0000
    )
    embed.add_field(name="🚀 0. Launch Workspace", value="`/begin`\n*Creates your personal private operations room right here on the server.*", inline=False)
    embed.add_field(name="🎟️ 1. Load Your Coupons", value="`/coupons [values separated by spaces]`\n*Example:* `/coupons values:8 8 5`", inline=False)
    embed.add_field(name="🛒 2. Add Cart Items", value="`/add [item_name_and_prices]`\n*Example:* `/add items:Fairlife 4.49 shampoo 6.59`", inline=False)
    embed.add_field(name="↩️ 3. Undo Last Add", value="`/undo`", inline=False)
    embed.add_field(name="❌ 4. Remove Cart Items", value="`/remove [item_name]`\n*Example:* `/remove item_name:Fairlife`", inline=False)
    embed.add_field(name="👀 5. View Cart", value="`/cart`", inline=False)
    embed.add_field(name="📊 6. Calculate Strategy", value="`/optimize`", inline=False)
    embed.add_field(name="✅ 7. Check Out & Track Savings", value="`/checkout`\n*Locks in the trip, logs your net savings, and clears the cart.*", inline=False)
    embed.add_field(name="💰 8. View Lifetime Savings", value="`/savings`", inline=False)
    embed.add_field(name="📜 8b. Pull Trip History", value="`/history` (last 10 trips)\n`/history start:2026-07-01` (one day)\n`/history start:2026-07-01 end:2026-07-12` (range)", inline=False)
    embed.add_field(name="🧹 9. Clear Session (no tracking)", value="`/clear`", inline=False)
    embed.add_field(name="🏓 10. Bot Status", value="`/ping`", inline=False)
    embed.add_field(name="ℹ️ 11. About This Bot", value="`/about`", inline=False)
    embed.add_field(name="🧪 12. Test Mode", value="Same flow, prefixed with `test`: `/testcoupons`, `/testadd`, `/testundo`, `/testremove`, `/testcart`, `/testoptimize`, `/testcheckout`, `/testclear`.", inline=False)

    if author_perms.manage_messages or author_perms.manage_roles or is_owner:
        mod_lines = []
        if author_perms.manage_messages or is_owner: mod_lines.append("`/nuke [amount]` — bulk delete messages")
        if author_perms.manage_channels or is_owner: mod_lines.append("`/ticket-close` — close active optimizer ticket channels")
        if author_perms.manage_roles or is_owner: 
            mod_lines.append("`/createrole [name] [color]` — create a new role")
            mod_lines.append("`/deleterole [name]` — remove a role")
            mod_lines.append("`/roleadd [@member] [name]` — give a role")
            mod_lines.append("`/roleremove [@member] [name]` — take a role")
        if author_perms.manage_channels or is_owner:
            mod_lines.append("`/createchannel [name] [visibility]` — spawn new channel")
            mod_lines.append("`/blockrole [role]` — hide a channel from a role")
            mod_lines.append("`/unblockrole [role]` — restore access configuration templates")
            mod_lines.append("`/whocansee` — view channel visibility audits")
        if is_owner:
            mod_lines.append("`/setup` — initialize private gateway core channel")
            mod_lines.append("`/permit [@member]` — whitelist member access paths")
            mod_lines.append("`/export-history` — secure trip tracker manual ledger output")
            mod_lines.append("`/import-history [payload]` — emergency state recovery restoration string engine")
            mod_lines.append("`/export-session-logs [mode]` — dump transaction track audit metrics logs")
            mod_lines.append("`/delete-last-trip` — erase mistake checkouts and rebalance statistics")
            mod_lines.append("`/export-bug-logs` — download and flush silent exception vault cache files")
            mod_lines.append("`/run-stress-test` — trigger high-intensity 5-cycle continuous load loops")
        embed.add_field(name="🛡️ Administrative & Owner Commands", value="\n".join(mod_lines), inline=False)

    embed.set_footer(text="Tip: Keep item names to a single word. This menu is completely tailored to your permissions.")
    return embed

async def _view_cart_logic(interaction: discord.Interaction, test=False):
    prefix = "🧪 [TEST] " if test else ""
    await interaction.response.defer()
    session = await get_channel_session_immutable(interaction.channel.id, is_test=test)
    embed = discord.Embed(title=f"{prefix}🛒 CVS Shopping Cart", color=0x9b59b6 if test else 0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    coupon_str = ", ".join([coupon_label(c) for c in session["coupons"]]) or "None loaded yet."
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=False)
    embed.add_field(name="🎟️ Loaded Coupons", value=coupon_str, inline=False)
    
    await log_action(interaction.channel.id, "cart", "None", f"Viewed subtotal: ${subtotal:.2f}", is_test=test)
    await send_cart_embed(interaction, embed, session, interaction.channel.id, is_test=test)

@bot.tree.command(name="cart", description="View all currently scanned items and loaded coupons")
async def view_cart(interaction: discord.Interaction):
    await _view_cart_logic(interaction, test=False)

@bot.tree.command(name="testcart", description="[TEST] View your active test layout cart details")
async def test_view_cart(interaction: discord.Interaction):
    await _view_cart_logic(interaction, test=True)

# token fetching and main execution
token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
bot.run(token)
