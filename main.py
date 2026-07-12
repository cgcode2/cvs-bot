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
from datetime import datetime

# 3. DISCORD BOT ENGINE SETUP
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for whocansee member audits and private gateway routing

class CouponBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        
    async def setup_hook(self):
        pass  # Server tree syncing handled within on_ready

bot = CouponBot()

# CRITICAL SECURITY FIX: Enforce persistent directory paths to mount to Railway volumes
DATA_DIR = "/app/data" if os.path.exists("/app/data") else "."
SESSION_CHANNELS_FILE = os.path.join(DATA_DIR, "session_channels.json")
SAVINGS_FILE = os.path.join(DATA_DIR, "savings_data.json")
CARTS_FILE = os.path.join(DATA_DIR, "active_carts.json")

STAFF_ROLE_NAME = "Staff"

# CRITICAL SYNC LOCK: Protects all file I/O from race conditions
file_operation_lock = asyncio.Lock()

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

# Async versions for command handlers (thread-safe with lock)
async def load_json_file(filepath, default_value):
    """Async load with lock protection and safe error handling."""
    async with file_operation_lock:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError, FileNotFoundError) as e:
                print(f"⚠️ Failed to load {filepath}: {e}. Using default.", file=sys.stderr)
                return default_value
        return default_value

async def save_json_file(filepath, data):
    """Async save with lock protection."""
    async with file_operation_lock:
        try:
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ Storage Write Failure on {filepath}: {e}", file=sys.stderr)

session_channels = load_json_file_sync(SESSION_CHANNELS_FILE, {})
savings_tracker = load_json_file_sync(SAVINGS_FILE, {
    "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
    "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": []
})

async def get_channel_session(channel_id, is_test=False):
    """Thread-safe retrieval of cart session from disk with auto-initialization."""
    carts_db = await load_json_file(CARTS_FILE, {})
    ch_key = str(channel_id)
    if ch_key not in carts_db:
        carts_db[ch_key] = {
            "items": [], 
            "coupons": [], 
            "cart_message": None, 
            "is_test": is_test,
            "last_optimization": None  # BUG FIX #2: Store optimization snapshot
        }
        await await save_json_file, carts_db)
    return carts_db[ch_key]

async def save_channel_session(channel_id, session_data):
    """Thread-safe save of cart session to disk."""
    carts_db = await load_json_file(CARTS_FILE, {})
    carts_db[str(channel_id)] = session_data
    await await save_json_file, carts_db)

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
    await await save_json_file, session_channels)

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

async def send_cart_embed(interaction: discord.Interaction, embed, session, channel_id):
    old_msg_id = session.get("cart_message")
    if old_msg_id is not None:
        try:
            old_msg = await interaction.channel.fetch_message(old_msg_id)
            await old_msg.delete()
        except Exception:
            pass  # Safely skip if the message was manually purged or doesn't exist
    
    msg = await interaction.followup.send(embed=embed)
    session["cart_message"] = msg.id
    await save_channel_session(channel_id, session)

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
        print(f'⚡ Direct server command injection successful!')
    except Exception as e:
        print(f'⚠️ Direct sync failed: {e}', file=sys.stderr)

# --- OWNER ONLY SLASH COMMANDS ---

@bot.tree.command(name="import-history", description="Emergency Recovery Tool: Force paste a text block backup directly back into memory data files")
@app_commands.describe(backup_payload="Paste your exported text string profile here")
async def import_history(interaction: discord.Interaction, backup_payload: str):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        parsed_data = json.loads(backup_payload)
        global savings_tracker
        savings_tracker.update(parsed_data)
        await await save_json_file, savings_tracker)
        await interaction.followup.send("✅ History safely injected back into persistent storage drive!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed parsing payload structure: `{e}`", ephemeral=True)

@bot.tree.command(name="export-history", description="Wipe-proofing backup tool: Compresses and exports database ledger arrays directly to DMs")
async def export_history(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("⛔ Security Error.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
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

# --- CORE USER SLASH COMMANDS Engine ---

@bot.tree.command(name="begin", description="Open your private text channel for coupon optimizing calculations")
async def begin_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used inside a server text channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    target_channel, created = await get_or_create_user_coupon_channel(interaction.guild, interaction.user)
    
    if created:
        await interaction.followup.send(f"✅ Your private space has been initialized! Head over to {target_channel.mention} to start shopping.", ephemeral=True)
    else:
        await interaction.followup.send(f"👋 You already have an active session! Jump back into {target_channel.mention} to finish up.", ephemeral=True)

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
        embed.add_field(name="🛡️ Administrative & Owner Commands", value="\n".join(mod_lines), inline=False)

    embed.set_footer(text="Tip: Keep item names to a single word. This menu is completely tailored to your permissions.")
    return embed

@app_commands.default_permissions(send_messages=True)
@bot.tree.command(name="help", description="Show the CVS Coupon Calculator help menu (only visible to you)")
async def slash_help(interaction: discord.Interaction):
    author_perms = interaction.channel.permissions_for(interaction.user) if interaction.guild else discord.Permissions.none()
    is_owner = await bot.is_owner(interaction.user)
    embed = build_help_embed(author_perms, is_owner)
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _add_item_logic(interaction: discord.Interaction, args, test=False):
    prefix = "🧪 [TEST] " if test else ""
    if len(args) == 0 or len(args) % 2 != 0:
        await interaction.response.send_message("❌ Format error. Provide item/price pairs.\n*Example:* `shampoo 6.59 soap 2.99`", ephemeral=True)
        return

    await interaction.response.defer()
    session = await get_channel_session(interaction.channel.id, is_test=test)
    added = []
    try:
        for i in range(0, len(args), 2):
            item_name = args[i]
            price = float(args[i + 1])
            session["items"].append({"name": item_name, "price": price})
            added.append(item_name)
    except ValueError:
        await interaction.followup.send("❌ Format error. Each item must be followed by a numeric price.")
        return

    embed = discord.Embed(title=f"{prefix}🛒 CVS Shopping Cart", color=0x9b59b6 if test else 0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    embed.add_field(name=f"Added: {', '.join(added)}", value="\u200b", inline=False)
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(interaction, embed, session, interaction.channel.id)

@bot.tree.command(name="add", description="Add grocery or care items and prices to your active cart session")
@app_commands.describe(items="Item name followed by price pairs separated by spaces (e.g. shampoo 6.59 soap 2.99)")
async def add_item(interaction: discord.Interaction, items: str):
    await _add_item_logic(interaction, items.split(), test=False)

@bot.tree.command(name="testadd", description="[TEST] Add items and prices to your test cart layout")
@app_commands.describe(items="Item name followed by price pairs separated by spaces")
async def test_add_item(interaction: discord.Interaction, items: str):
    await _add_item_logic(interaction, items.split(), test=True)

async def _undo_item_logic(interaction: discord.Interaction, test=False):
    prefix = "🧪 [TEST] " if test else ""
    session = await get_channel_session(interaction.channel.id, is_test=test)
    if not session["items"]:
        await interaction.response.send_message("❌ Nothing to undo — your cart is empty!", ephemeral=True)
        return

    await interaction.response.defer()
    removed_item = session["items"].pop()
    embed = discord.Embed(title=f"{prefix}↩️ Last Item Undone", color=0xe67e22)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    embed.add_field(name=f"Removed: {removed_item['name']} (${removed_item['price']:.2f})", value="\u200b", inline=False)
    embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
    embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(interaction, embed, session, interaction.channel.id)

@bot.tree.command(name="undo", description="Undo the last item you added to your tracking session")
async def undo_item(interaction: discord.Interaction):
    await _undo_item_logic(interaction, test=False)

@bot.tree.command(name="testundo", description="[TEST] Undo the last item added to your test cart layout")
async def test_undo_item(interaction: discord.Interaction):
    await _undo_item_logic(interaction, test=True)

async def _view_cart_logic(interaction: discord.Interaction, test=False):
    prefix = "🧪 [TEST] " if test else ""
    await interaction.response.defer()
    session = await get_channel_session(interaction.channel.id, is_test=test)
    embed = discord.Embed(title=f"{prefix}🛒 CVS Shopping Cart", color=0x9b59b6 if test else 0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    coupon_str = ", ".join([coupon_label(c) for c in session["coupons"]]) or "None loaded yet."
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=False)
    embed.add_field(name="🎟️ Loaded Coupons", value=coupon_str, inline=False)
    await send_cart_embed(interaction, embed, session, interaction.channel.id)

@bot.tree.command(name="cart", description="View all currently scanned items and loaded coupons")
async def view_cart(interaction: discord.Interaction):
    await _view_cart_logic(interaction, test=False)

@bot.tree.command(name="testcart", description="[TEST] View your active test layout cart details")
async def test_view_cart(interaction: discord.Interaction):
    await _view_cart_logic(interaction, test=True)

async def _remove_item_logic(interaction: discord.Interaction, item_name, test=False):
    prefix = "🧪 [TEST] " if test else ""
    session = await get_channel_session(interaction.channel.id, is_test=test)
    found = False
    for item in reversed(session["items"]):
        if item["name"].lower() == item_name.lower():
            session["items"].remove(item)
            found = True
            break
    if found:
        await interaction.response.defer()
        embed = discord.Embed(title=f"{prefix}❌ Item Removed from Cart", color=0xe67e22)
        item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
        subtotal = sum(item['price'] for item in session["items"])
        embed.add_field(name=f"Removed item: {item_name}", value=f"Here is your updated cart list:", inline=False)
        embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
        embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
        await send_cart_embed(interaction, embed, session, interaction.channel.id)
    else:
        await interaction.response.send_message(f"⚠️ Could not find an item named '**{item_name}**' inside your current cart.", ephemeral=True)

@bot.tree.command(name="remove", description="Drop a specific item out of your active cart tracking list")
@app_commands.describe(item_name="Name of the item you wish to delete")
async def remove_item(interaction: discord.Interaction, item_name: str):
    await _remove_item_logic(interaction, item_name, test=False)

@bot.tree.command(name="testremove", description="[TEST] Drop an item out of your active test layout by name")
@app_commands.describe(item_name="Name of the item you wish to delete")
async def test_remove_item(interaction: discord.Interaction, item_name: str):
    await _remove_item_logic(interaction, item_name, test=True)

HALF_OFF_ALIASES = {"half", "50%", "50%off", "0.5x"}

async def _set_coupons_logic(interaction: discord.Interaction, args, test=False):
    clear_cmd = "/testclear" if test else "/clear"
    session = await get_channel_session(interaction.channel.id, is_test=test)
    try:
        new_coupons = []
        for x in args:
            if x.strip().lower() in HALF_OFF_ALIASES:
                new_coupons.append("half")
            else:
                new_coupons.append(float(x))
        session["coupons"].extend(new_coupons)
        session["coupons"].sort(key=lambda c: -1 if c == "half" else c, reverse=True)
        prefix = "🧪 [TEST] " if test else ""
        added_str = ", ".join([coupon_label(c) for c in new_coupons])
        all_str = ", ".join([coupon_label(c) for c in session["coupons"]])
        await save_channel_session(interaction.channel.id, session)
        await interaction.response.send_message(
            f"{prefix}✅ Added: {added_str}\n🎟️ All Loaded Coupons: {all_str}\n"
            f"*(Run `{clear_cmd}` to wipe coupons/cart and start fresh.)*"
        )
    except ValueError:
        await interaction.response.send_message(f"❌ Format error. Example format: `8 8 5 half`", ephemeral=True)

@bot.tree.command(name="coupons", description="Input all available dollar-off transaction stackers")
@app_commands.describe(values="List of numbers separated by spaces (e.g. 8 8 5 half)")
async def set_coupons(interaction: discord.Interaction, values: str):
    await _set_coupons_logic(interaction, values.split(), test=False)

@bot.tree.command(name="testcoupons", description="[TEST] Add coupon stack values into your test session environment")
@app_commands.describe(values="List of numbers separated by spaces")
async def test_set_coupons(interaction: discord.Interaction, values: str):
    await _set_coupons_logic(interaction, values.split(), test=True)

async def _optimize_logic(interaction: discord.Interaction, test=False):
    prefix = "🧪 [TEST] " if test else ""
    session = get_channel_session(interaction.channel.id, is_test=test)
    items = session["items"]
    coupons = session["coupons"]
    if not items:
        await interaction.response.send_message("❌ Your cart is empty!", ephemeral=True)
        return

    await interaction.response.defer()
    total_due, bundling = await asyncio.to_thread(calculate_best_bundles, items, coupons)
    # BUG FIX #2: Cache optimization snapshot to avoid checkout desync
    session = await get_channel_session(interaction.channel.id, is_test=test)
    session["last_optimization"] = {
        "items": items,
        "coupons": coupons,
        "total_due": total_due,
        "bundling": bundling
    }
    await save_channel_session(interaction.channel.id, session)
    
    embed = discord.Embed(title=f"{prefix}🧾 Optimized CVS Checkout Strategy", color=0x9b59b6 if test else 0x00ff00)

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

    if total_due > 0.0:
        candidate_values = sorted(COUPON_COSTS.keys()) + ["half"]
        working_coupons = list(coupons)
        working_due = total_due
        suggestions = []

        for _ in range(3):
            best_candidate = None
            best_net_benefit = 1e-9
            best_trial = None
            for cp in candidate_values:
                trial_coupons = working_coupons + [cp]
                trial_due, trial_bundling = await asyncio.to_thread(calculate_best_bundles, items, trial_coupons)
                register_savings = working_due - trial_due
                net_benefit = register_savings - coupon_cost(cp)
                if net_benefit > best_net_benefit:
                    best_net_benefit = net_benefit
                    best_candidate = cp
                    best_trial = (trial_due, trial_bundling, register_savings)

                if best_candidate is None:
                    break

            if best_candidate is None:
                break

            trial_due, trial_bundling, register_savings = best_trial
            new_group_idx = len(working_coupons)
            covered_items = trial_bundling.get(new_group_idx, [])
            covered_str = ", ".join(f"**{i['name']}** (${i['price']:.2f})" for i in covered_items) or "a rebalanced set of items"
            suggestions.append(
                f"➔ Buy a **{coupon_label(best_candidate)}** coupon (cost: ${coupon_cost(best_candidate):.2f}).\n"
                f"• Use it on: {covered_str}\n"
                f"• Register total drops to **${trial_due:.2f}** (saves ${register_savings:.2f}), "
                f"net gain after coupon cost: **${best_net_benefit:.2f}**."
            )
            working_coupons.append(best_candidate)
            working_due = trial_due

        if suggestions:
            upgrade_text = f"💡 *You have a remaining balance of **${total_due:.2f}**.*\n\n" + "\n\n".join(
                f"**Step {i+1}:**\n{s}" for i, s in enumerate(suggestions)
            )
            embed.add_field(name="✨ Smart Coupon Upgrade Advice", value=upgrade_text, inline=False)
        else:
            embed.add_field(name="✨ Smart Coupon Upgrade Advice", value=f"💡 No coupon purchase would pay for itself right now.", inline=False)

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="optimize", description="Run the allocation algorithm to bundle your purchases into optimized register steps")
async def optimize_cart(interaction: discord.Interaction):
    await _optimize_logic(interaction, test=False)

@bot.tree.command(name="testoptimize", description="[TEST] Calculate allocation logic distributions over test array bundles")
async def test_optimize_cart(interaction: discord.Interaction):
    await _optimize_logic(interaction, test=True)

async def _clear_logic(interaction: discord.Interaction, test=False):
    # BUG FIX: Pull through the initialization safeguard wrapper to prevent unallocated file registry KeyErrors
    session = await get_channel_session(interaction.channel.id, is_test=test)
    session["items"] = []
    session["coupons"] = []
    session["cart_message"] = None
    await save_channel_session(interaction.channel.id, session)
    msg = "🧪 Test cart and coupons cleared!" if test else "🧹 Cart and coupons cleared!"
    await interaction.response.send_message(msg)

@bot.tree.command(name="clear", description="Completely wipe the current session items and coupons to start fresh")
async def clear_cart(interaction: discord.Interaction):
    await _clear_logic(interaction, test=False)

@bot.tree.command(name="testclear", description="[TEST] Wipe active memory data arrays out of the test environment track")
async def test_clear_cart(interaction: discord.Interaction):
    await _clear_logic(interaction, test=True)

async def _checkout_logic(interaction: discord.Interaction, test=False):
    session = await get_channel_session(interaction.channel.id, is_test=test)
    items = session["items"]
    coupons = session["coupons"]
    if not items:
        await interaction.response.send_message("❌ Your cart is empty — nothing to check out!", ephemeral=True)
        return

    await interaction.response.defer()
    
    # BUG FIX #2: Use cached optimization instead of recalculating
    last_opt = session.get("last_optimization")
    if last_opt is None:
        # Fallback if no optimization was run (should not happen in normal flow)
        subtotal = sum(item['price'] for item in items)
        total_due, _ = await asyncio.to_thread(calculate_best_bundles, items, coupons)
    else:
        # Use exact values from cached optimization (eliminates float precision desync)
        subtotal = sum(item['price'] for item in last_opt.get("items", items))
        total_due = last_opt["total_due"]
        bundling = last_opt["bundling"]
    
    coupon_spend = sum(coupon_cost(c) for c in coupons)
    gross_saved = subtotal - total_due
    net_saved = gross_saved - coupon_spend

    if test:
        embed = discord.Embed(title="🧪 [TEST] Checkout Preview", color=0x9b59b6)
        embed.add_field(name="Full Price (No Coupons)", value=f"${subtotal:.2f}", inline=True)
        embed.add_field(name="Register Total Paid", value=f"${total_due:.2f}", inline=True)
        embed.add_field(name="Spent on Coupons", value=f"${coupon_spend:.2f}", inline=True)
        embed.add_field(name="💰 Net Money Saved (Simulated)", value=f"## **${net_saved:.2f}**", inline=False)
        await interaction.followup.send(embed=embed)
        session["items"] = []
        session["coupons"] = []
        session["cart_message"] = None
        session["last_optimization"] = None
        await save_channel_session(interaction.channel.id, session)
        return

    now = datetime.now()
    trip_record = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "items": [{"name": i["name"], "price": i["price"]} for i in items],
        "coupons": coupons,
        "subtotal": subtotal,
        "total_due": total_due,
        "coupon_spend": coupon_spend,
        "net_saved": net_saved,
    }

    savings_tracker["trip_count"] += 1
    savings_tracker["total_full_price"] += subtotal
    savings_tracker["total_paid"] += total_due
    savings_tracker["total_coupon_cost"] += coupon_spend
    savings_tracker["total_net_saved"] += net_saved
    savings_tracker.setdefault("trips", []).append(trip_record)
    await await save_json_file, savings_tracker)

    embed = discord.Embed(title="✅ Trip Checked Out!", color=0x2ecc71)
    embed.add_field(name="🗓️ Date Logged", value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
    embed.add_field(name="Full Price (No Coupons)", value=f"${subtotal:.2f}", inline=True)
    embed.add_field(name="Register Total Paid", value=f"${total_due:.2f}", inline=True)
    embed.add_field(name="Spent on Coupons", value=f"${coupon_spend:.2f}", inline=True)
    embed.add_field(name="💰 Net Money Saved This Trip", value=f"## **${net_saved:.2f}**", inline=False)
    embed.add_field(name="📈 Lifetime Total Saved", value=f"**${savings_tracker['total_net_saved']:.2f}** across {savings_tracker['trip_count']} trip(s)", inline=False)
    
    is_ticket = interaction.guild is not None and session_channels.get(str(interaction.user.id)) == interaction.channel.id
    footer_note = "Run /savings for lifetime stats. Receipt copied to your DMs!"
    if is_ticket:
        footer_note += " This channel will auto-close in 10 seconds."
    embed.set_footer(text=footer_note)
    await interaction.followup.send(embed=embed)

    session["items"] = []
    session["coupons"] = []
    session["cart_message"] = None
    session["last_optimization"] = None
    await save_channel_session(interaction.channel.id, session)

    try:
        item_str = "\n".join([f"• **{i['name']}**: ${i['price']:.2f}" for i in items]) or "No items."
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None"
        dm_embed = discord.Embed(title="chat log snapshot", color=0x2ecc71)
        dm_embed.add_field(name="🗓️ Date", value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
        dm_embed.add_field(name="🛒 Items Purchased", value=item_str, inline=False)
        dm_embed.add_field(name="🎟️ Coupons Used", value=coupon_str, inline=False)
        dm_embed.add_field(name="Full Price", value=f"${subtotal:.2f}", inline=True)
        dm_embed.add_field(name="Paid at Register", value=f"${total_due:.2f}", inline=True)
        dm_embed.add_field(name="Coupon Cost", value=f"${coupon_spend:.2f}", inline=True)
        dm_embed.add_field(name="💰 Net Money Saved", value=f"## **${net_saved:.2f}**", inline=False)
        await interaction.user.send(embed=dm_embed)
    except Exception as e:
        print(f"⚠️ DM send failed: {e}", file=sys.stderr)


@bot.tree.command(name="checkout", description="Finalize your trip balance splits, lock in metrics, and clear tracking arrays")
async def checkout(interaction: discord.Interaction):
    await _checkout_logic(interaction, test=False)

@bot.tree.command(name="testcheckout", description="[TEST] Simulate database commits to preview receipts without mutating history records")
async def test_checkout(interaction: discord.Interaction):
    await _checkout_logic(interaction, test=True)

@bot.tree.command(name="savings", description="View accumulated lifetime ledger optimizations and performance totals")
async def view_savings(interaction: discord.Interaction):
    s = savings_tracker
    embed = discord.Embed(title="💰 Lifetime Savings Tracker", color=0x2ecc71)
    if s["trip_count"] == 0:
        embed.description = "No trips checked out yet. Run `/checkout` to start tracking!"
    else:
        avg_saved = s["total_net_saved"] / s["trip_count"]
        embed.add_field(name="🧾 Trips Checked Out", value=str(s["trip_count"]), inline=True)
        embed.add_field(name="🏷️ Total Full Price", value=f"${s['total_full_price']:.2f}", inline=True)
        embed.add_field(name="💵 Total Actually Paid", value=f"${s['total_paid']:.2f}", inline=True)
        embed.add_field(name="🎟️ Total Spent on Coupons", value=f"${s['total_coupon_cost']:.2f}", inline=True)
        embed.add_field(name="📊 Avg Net Saved / Trip", value=f"${avg_saved:.2f}", inline=True)
        embed.add_field(name="💰 Lifetime Net Money Saved", value=f"## **${s['total_net_saved']:.2f}**", inline=False)
    await interaction.response.send_message(embed=embed)

def _parse_history_date(raw):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try: return datetime.strptime(raw, fmt).date()
        except ValueError: continue
    return None

@bot.tree.command(name="history", description="Query past receipts and localized financial arrays out of persistent storage tracking sheets")
@app_commands.describe(start="Filter starting bounds date (YYYY-MM-DD)", end="Optional range limit date bounds (YYYY-MM-DD)")
async def view_history(interaction: discord.Interaction, start: str = None, end: str = None):
    trips = savings_tracker.get("trips", [])
    if not trips:
        await interaction.response.send_message("📭 No checked-out trips logged yet.", ephemeral=True)
        return

    start_date, end_date = None, None
    if start:
        start_date = _parse_history_date(start)
        if not start_date:
            await interaction.response.send_message("❌ Error tracking date formatting sequence. Please format strings via `YYYY-MM-DD`.", ephemeral=True)
            return
        end_date = _parse_history_date(end) if end else start_date
        if end and not end_date:
            await interaction.response.send_message("❌ End boundary query contains syntax structural runtime schema variations.", ephemeral=True)
            return

    matches = [t for t in trips if start_date <= datetime.strptime(t["date"], "%Y-%m-%d").date() <= end_date] if start_date else trips[-10:]
    if not matches:
        await interaction.response.send_message("📭 No matching records found.", ephemeral=True)
        return

    title = f"📜 Trip History" if start_date else "📜 Trip History — Last 10 Trips"
    embed = discord.Embed(title=title, color=0x3498db)
    for trip in matches[-15:]:
        item_names = ", ".join(i["name"] for i in trip["items"])
        embed.add_field(
            name=f"🗓️ {trip['date']} @ {trip.get('time', '—')}",
            value=f"Items: {item_names}\nFull: ${trip['subtotal']:.2f} ➔ Paid: ${trip['total_due']:.2f}\n💰 Net Saved: **${trip['net_saved']:.2f}**",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

# --- ADMINISTRATIVE MODERATOR COMMANDS ENGINE ---

@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="nuke", description="Bulk drop message indexes backward to clear active operating rooms logs")
@app_commands.describe(amount="Quantity of records to scrub out (or text string match keyword 'all')")
async def nuke(interaction: discord.Interaction, amount: str):
    await interaction.response.defer(ephemeral=True)
    if amount.lower() == "all":
        deleted = await interaction.channel.purge(limit=None)
    else:
        try: count = int(amount)
        except ValueError: 
            await interaction.followup.send("❌ Error parsing line integer arrays.")
            return
        deleted = await interaction.channel.purge(limit=count)
    await interaction.followup.send(f"🧨 Nuked **{len(deleted)}** message entries!")

@bot.tree.command(name="ticket-close", description="Close this coupon optimizer ticket channel environment directly")
async def ticket_close(interaction: discord.Interaction):
    if not (interaction.user.guild_permissions.manage_channels or discord.utils.get(interaction.user.roles, name=STAFF_ROLE_NAME)):
        await interaction.response.send_message("⛔ Account access authorization evaluation missing.", ephemeral=True)
        return

    owner_id = next((uid for uid, cid in session_channels.items() if cid == interaction.channel.id), None)
    if owner_id:
        session_channels.pop(owner_id, None)
        await save_json_file, session_channels)
    await interaction.response.send_message(f"🔒 Ticket closed. Deleting channel in 5 seconds...")
    await asyncio.sleep(5)
    try: await interaction.channel.delete()
    except discord.HTTPException: pass

@app_commands.default_permissions(manage_channels=True)
@bot.tree.command(name="whocansee", description="List members who can view a channel")
async def who_can_see(interaction: discord.Interaction):
    await interaction.response.defer()
    members_with_access = [m for m in interaction.guild.members if interaction.channel.permissions_for(m).view_channel]
    members_with_access.sort(key=lambda m: m.display_name.lower())
    embed = discord.Embed(title=f"👀 Access Audit for #{interaction.channel.name}", description="\n".join([f"• {m.mention}" for m in members_with_access]) or "None", color=0x3498db)
    await interaction.followup.send(embed=embed)

@app_commands.default_permissions(manage_roles=True)
@bot.tree.command(name="createrole", description="Create a new server role")
@app_commands.describe(role_name="Name of the role", color_name="Color matching keyword (e.g. red, blue, green)")
async def create_role(interaction: discord.Interaction, role_name: str, color_name: str = None):
    if discord.utils.get(interaction.guild.roles, name=role_name):
        await interaction.response.send_message("⚠️ A role with that name already exists.", ephemeral=True)
        return
    await interaction.response.defer()
    color = resolve_color(color_name) or discord.Color.default()
    new_role = await interaction.guild.create_role(name=role_name, color=color) # BUG FIX: Corrected legacy ctx variable crash map to handle interaction natively
    await interaction.followup.send(f"✅ Created role {new_role.mention}!")

@app_commands.default_permissions(manage_roles=True)
@bot.tree.command(name="deleterole", description="Delete an existing server role")
@app_commands.describe(role_name="Exact name of the role to remove")
async def delete_role(interaction: discord.Interaction, role_name: str):
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    if role:
        await role.delete()
        await interaction.response.send_message(f"🗑️ Deleted role `{role_name}`.")
    else:
        await interaction.response.send_message("❌ Role not found.", ephemeral=True)

@app_commands.default_permissions(manage_roles=True)
@bot.tree.command(name="roleadd", description="Assign a role to a server member")
@app_commands.describe(member="The member to receive the role", role_name="The name of the role")
async def role_add(interaction: discord.Interaction, member: discord.Member, role_name: str):
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    if role:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ Gave {member.mention} the `{role_name}` role.")
    else:
        await interaction.response.send_message("❌ Role not found.", ephemeral=True)

@app_commands.default_permissions(manage_roles=True)
@bot.tree.command(name="roleremove", description="Strip a role from a server member")
@app_commands.describe(member="The target member", role_name="The name of the role")
async def role_remove(interaction: discord.Interaction, member: discord.Member, role_name: str):
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    if role and role in member.roles:
        await member.remove_roles(role)
        await interaction.response.send_message(f"✅ Removed the `{role_name}` role from {member.mention}.")
    else:
        await interaction.response.send_message("❌ Target lacks role or index validation failed.", ephemeral=True)

@app_commands.default_permissions(manage_channels=True)
@bot.tree.command(name="createchannel", description="Create a new text channel setup")
@app_commands.describe(name="Name of the channel", visibility="Set public or private visibility configuration templates")
async def create_channel(interaction: discord.Interaction, name: str, visibility: Literal["public", "private"] = "public"):
    overwrites = {}
    if visibility.lower() == "private":
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    channel = await interaction.guild.create_text_channel(name=name, category=interaction.channel.category, overwrites=overwrites)
    await interaction.response.send_message(f"✅ Created channel {channel.mention}")

@app_commands.default_permissions(manage_channels=True)
@bot.tree.command(name="blockrole", description="Hide this channel view permission map allocations entirely from a role")
@app_commands.describe(role="The role to block")
async def block_role(interaction: discord.Interaction, role: discord.Role):
    await interaction.channel.set_permissions(role, view_channel=False)
    await interaction.response.send_message(f"🚫 `{role.name}` role blocked from viewing this channel.")

@app_commands.default_permissions(manage_channels=True)
@bot.tree.command(name="unblockrole", description="Restore channel view permission maps to default inheritance profiles")
@app_commands.describe(role="The role to unblock")
async def unblock_role(interaction: discord.Interaction, role: discord.Role):
    await interaction.channel.set_permissions(role, overwrite=None)
    await interaction.response.send_message(f"✅ Reset `{role.name}` access permissions back to server default settings.")

# --- DIAGNOSTIC TIMINGS & GENERAL UTILITIES ENGINE ---

@bot.tree.command(name="ping", description="Verify backend round-trip latency timings")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latency: **{round(bot.latency * 1000)}ms**")

@bot.tree.command(name="about", description="About the CVS Coupon Calculator bot overview specifications")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ℹ ... About the CVS Coupon Calculator", 
        description="A combinatorics-powered assistant that splits your cart across coupons to minimize what you pay at register.", 
        color=0xcc0000
    )
    embed.add_field(name="Commands", value="Run `/help` for the full walkthrough.", inline=False)
    embed.add_field(name="Hosting", value="Running 24/7 on Railway.", inline=False)
    await interaction.response.send_message(embed=embed)

token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
bot.run(token)
