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
        pass  # Regulated server syncing offloaded straight to on_ready

bot = CouponBot()

current_session = {"items": [], "coupons": [], "cart_message": None}
test_session = {"items": [], "coupons": [], "cart_message": None}

# Per-user private coupon optimizer channels configuration
SESSION_CHANNELS_FILE = "session_channels.json"
STAFF_ROLE_NAME = "Staff"

def load_session_channels():
    if os.path.exists(SESSION_CHANNELS_FILE):
        try:
            with open(SESSION_CHANNELS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def save_session_channels(data):
    with open(SESSION_CHANNELS_FILE, "w") as f:
        json.dump(data, f, indent=2)

session_channels = load_session_channels()  # str(user_id) -> channel_id

def is_staff_or_channel_manager():
    async def predicate(ctx):
        if ctx.guild is None:
            return False
        if ctx.author.guild_permissions.manage_channels:
            return True
        return discord.utils.get(ctx.author.roles, name=STAFF_ROLE_NAME) is not None
    return commands.check(predicate)

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
    save_session_channels(session_channels)

    welcome_embed = discord.Embed(
        title="🎯 Your Private Coupon Optimizer Channel",
        description=(
            f"Hey {user.mention}! This is your own space to run the full coupon flow "
            "without cluttering the main chat. Only you"
            + (f" and the `{STAFF_ROLE_NAME}` role" if staff_role else "")
            + " can see this channel.\n\nRun `!help` (or `/help`) here anytime for the full command list."
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

SAVINGS_FILE = "savings_data.json"
DEFAULT_SAVINGS = {
    "trip_count": 0, "total_full_price": 0.0, "total_paid": 0.0,
    "total_coupon_cost": 0.0, "total_net_saved": 0.0, "trips": [],
}

def load_savings():
    if os.path.exists(SAVINGS_FILE):
        try:
            with open(SAVINGS_FILE, "r") as f:
                data = json.load(f)
            merged = DEFAULT_SAVINGS.copy()
            merged.update(data)
            return merged
        except Exception as e:
            print(f"⚠️ Failed to load savings data, starting fresh: {e}", file=sys.stderr)
    return DEFAULT_SAVINGS.copy()

def save_savings(data):
    try:
        with open(SAVINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"❌ Failed to save savings data! Details: {e}", file=sys.stderr)

savings_tracker = load_savings()

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

async def send_cart_embed(ctx, embed, session=None):
    if session is None:
        session = current_session
    old_message = session.get("cart_message")
    if old_message is not None:
        try:
            await old_message.delete()
        except Exception:
            pass
    session["cart_message"] = await ctx.send(embed=embed)

async def safely_delete_message(ctx):
    if ctx.interaction is not None:
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

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
        # Direct server injection to completely bypass the 1-hour global rollout queue
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        print(f'⚡ Direct server command injection successful!')
    except Exception as e:
        print(f'⚠️ Direct sync failed: {e}', file=sys.stderr)

# --- THE PRIVATE GATEWAY COMMAND ---
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

@bot.hybrid_command(name="setup", description="Create the private CVS coupon optimizer channel")
@commands.is_owner()
async def setup_channel(ctx):
    await safely_delete_message(ctx)
    guild = ctx.guild
    owner = guild.owner or await guild.fetch_member(guild.owner_id)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True, manage_messages=True)
    }
    
    channel_name = "cvs-coupon-optimizer"
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
    if existing_channel:
        await ctx.send(f"⚠️ A channel named `#{channel_name}` already exists!", delete_after=5)
        return
        
    new_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
    welcome_embed = discord.Embed(
        title="🎯 CVS Coupon Optimizer Room",
        description="This is your secure, private command base for calculated shopping bundles! Run `/begin` to get your personal workspace.",
        color=0xcc0000
    )
    await new_channel.send(welcome_embed)
    await ctx.send(f"✅ Secure channel {new_channel.mention} successfully built!", delete_after=5)

@bot.hybrid_command(name="permit", description="Grant a member access to the coupon optimizer channel")
@commands.is_owner()
async def permit_user(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    channel_name = "cvs-coupon-optimizer"
    channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)
    
    if not channel:
        await ctx.send("❌ Error: The `#cvs-coupon-optimizer` channel does not exist yet. Run `!setup` first!", delete_after=5)
        return
        
    await channel.set_permissions(member, view_channel=True, send_messages=True, read_messages=True)
    await ctx.send(f"✅ Granted access to {member.mention} to use the optimizer room!", delete_after=5)

def build_help_embed(author_perms: discord.Permissions, is_owner: bool) -> discord.Embed:
    embed = discord.Embed(
        title="📖 CVS Coupon Calculator — Help Menu", 
        description="Follow this quick blueprint to maximize your coupon values and slash your out-of-pocket register total.", 
        color=0xcc0000
    )
    embed.add_field(name="🚀 0. Launch Private Channel", value="`/begin`\n*Creates your personal private operations room right here on the server.*", inline=False)
    embed.add_field(name="🎟️ 1. Load Your Coupons", value="`!coupons [value1] [value2] ...`\n*Example:* `!coupons 8 8 5`", inline=False)
    embed.add_field(name="🛒 2. Add Cart Items", value="`!add [item_name] [price] ...`\n*Example:* `!add Fairlife 4.49 shampoo 6.59`", inline=False)
    embed.add_field(name="↩️ 3. Undo Last Add", value="`!undo`", inline=False)
    embed.add_field(name="❌ 4. Remove Cart Items", value="`!remove [item_name]`\n*Example:* `!remove Fairlife`", inline=False)
    embed.add_field(name="👀 5. View Cart", value="`!cart`", inline=False)
    embed.add_field(name="📊 6. Calculate Strategy", value="`!optimize`", inline=False)
    embed.add_field(name="✅ 7. Check Out & Track Savings", value="`!checkout`\n*Locks in the trip, logs your net savings, and clears the cart.*", inline=False)
    embed.add_field(name="💰 8. View Lifetime Savings", value="`!savings`", inline=False)
    embed.add_field(name="📜 8b. Pull Trip History", value="`!history` (last 10 trips)\n`!history 2026-07-01` (one day)\n`!history 2026-07-01 2026-07-12` (date range)", inline=False)
    embed.add_field(name="🧹 9. Clear Session (no tracking)", value="`!clear`", inline=False)
    embed.add_field(name="🏓 10. Bot Status", value="`!ping`", inline=False)
    embed.add_field(name="ℹ️ 11. About This Bot", value="`!about`", inline=False)
    embed.add_field(name="🧪 12. Test Mode", value="Same flow, prefixed with `test`: `!testcoupons`, `!testadd`, `!testundo`, `!testremove`, `!testcart`, `!testoptimize`, `!testcheckout`, `!testclear`.", inline=False)

    if author_perms.manage_messages or author_perms.manage_roles:
        mod_lines = []
        if author_perms.manage_messages: mod_lines.append("`!nuke [amount]` or `!nuke all` — bulk delete messages")
        if author_perms.manage_roles:
            mod_lines.append("`!createrole [name] [color]` — create role")
            mod_lines.append("`!deleterole [name]` — delete role")
            mod_lines.append("`!roleadd [@member] [name]` — give role")
            mod_lines.append("`!roleremove [@member] [name]` — strip role")
        if author_perms.manage_channels:
            mod_lines.append("`!createchannel [name] [public|private] [@role]` — make channel")
            mod_lines.append("`!blockrole [role]` — hide channel from role")
            mod_lines.append("`!unblockrole [role]` — unhide channel")
            mod_lines.append("`!whocansee` — view channel visibility audits")
        embed.add_field(name="🛡️ Moderator Commands", value="\n".join(mod_lines), inline=False)

    if is_owner:
        embed.add_field(name="👑 Owner Commands", value="`!setup` — initialize private gateway channel\n`!permit [@member]` — whitelist member views", inline=False)

    embed.set_footer(text="Tip: Keep item names to a single word. This menu is completely tailored to your permissions.")
    return embed

@bot.command(name="help")
async def plain_help_redirect(ctx):
    await safely_delete_message(ctx)
    await ctx.send(f"{ctx.author.mention} Use **`/help`** instead — it executes as an ephemeral slash message that doesn't clutter chat!", delete_after=8)

@bot.tree.command(name="help", description="Show the CVS Coupon Calculator help menu (only visible to you)")
async def slash_help(interaction: discord.Interaction):
    author_perms = interaction.channel.permissions_for(interaction.user) if interaction.guild else discord.Permissions.none()
    is_owner = await bot.is_owner(interaction.user)
    embed = build_help_embed(author_perms, is_owner)
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def _add_item_logic(ctx, session, args, test=False):
    await safely_delete_message(ctx)
    prefix = "🧪 [TEST] " if test else ""
    cmd = "!testadd" if test else "!add"

    if len(args) == 0 or len(args) % 2 != 0:
        await ctx.send(f"❌ Format error. Provide item/price pairs.\n*Example:* `{cmd} shampoo 6.59 soap 2.99`", delete_after=8)
        return

    added = []
    try:
        for i in range(0, len(args), 2):
            item_name = args[i]
            price = float(args[i + 1])
            session["items"].append({"name": item_name, "price": price})
            added.append(item_name)
    except ValueError:
        await ctx.send(f"❌ Format error. Each item must be followed by a numeric price.", delete_after=8)
        return

    embed = discord.Embed(title=f"{prefix}🛒 CVS Shopping Cart", color=0x9b59b6 if test else 0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    embed.add_field(name=f"Added: {', '.join(added)}", value="\u200b", inline=False)
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(ctx, embed, session)

@bot.hybrid_command(name="add", description="Add items and prices to your cart (e.g. shampoo 6.59 soap 2.99)")
@app_commands.describe(items="Item name followed by price pairs separated by spaces")
async def add_item(ctx, *, items: str):
    await _add_item_logic(ctx, current_session, items.split(), test=False)

@bot.hybrid_command(name="testadd", description="[TEST] Add items and prices to your test cart")
@app_commands.describe(items="Item name followed by price pairs separated by spaces")
async def test_add_item(ctx, *, items: str):
    await _add_item_logic(ctx, test_session, items.split(), test=True)

async def _undo_item_logic(ctx, session, test=False):
    await safely_delete_message(ctx)
    prefix = "🧪 [TEST] " if test else ""
    if not session["items"]:
        await ctx.send("❌ Nothing to undo — your cart is empty!", delete_after=5)
        return

    removed_item = session["items"].pop()
    embed = discord.Embed(title=f"{prefix}↩️ Last Item Undone", color=0xe67e22)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    embed.add_field(name=f"Removed: {removed_item['name']} (${removed_item['price']:.2f})", value="\u200b", inline=False)
    embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
    embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(ctx, embed, session)

@bot.hybrid_command(name="undo", description="Undo the last item you added to your cart")
async def undo_item(ctx):
    await _undo_item_logic(ctx, current_session, test=False)

@bot.hybrid_command(name="testundo", description="[TEST] Undo the last item added to your test cart")
async def test_undo_item(ctx):
    await _undo_item_logic(ctx, test_session, test=True)

async def _view_cart_logic(ctx, session, test=False):
    await safely_delete_message(ctx)
    prefix = "🧪 [TEST] " if test else ""
    embed = discord.Embed(title=f"{prefix}🛒 CVS Shopping Cart", color=0x9b59b6 if test else 0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
    subtotal = sum(item['price'] for item in session["items"])
    coupon_str = ", ".join([coupon_label(c) for c in session["coupons"]]) or "None loaded yet."
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=False)
    embed.add_field(name="🎟️ Loaded Coupons", value=coupon_str, inline=False)
    await send_cart_embed(ctx, embed, session)

@bot.hybrid_command(name="cart", description="View your current cart")
async def view_cart(ctx):
    await _view_cart_logic(ctx, current_session, test=False)

@bot.hybrid_command(name="testcart", description="[TEST] View your test cart")
async def test_view_cart(ctx):
    await _view_cart_logic(ctx, test_session, test=True)

async def _remove_item_logic(ctx, session, item_name, test=False):
    await safely_delete_message(ctx)
    prefix = "🧪 [TEST] " if test else ""
    found = False
    for item in reversed(session["items"]):
        if item["name"].lower() == item_name.lower():
            session["items"].remove(item)
            found = True
            break
    if found:
        embed = discord.Embed(title=f"{prefix}❌ Item Removed from Cart", color=0xe67e22)
        item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in session["items"]])
        subtotal = sum(item['price'] for item in session["items"])
        embed.add_field(name=f"Removed item: {item_name}", value=f"Here is your updated cart list:", inline=False)
        embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
        embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
        await send_cart_embed(ctx, embed, session)
    else:
        await ctx.send(f"⚠️ Could not find an item named '**{item_name}**' inside your current cart.", delete_after=5)

@bot.hybrid_command(name="remove", description="Remove an item from your cart by name")
async def remove_item(ctx, item_name: str):
    await _remove_item_logic(ctx, current_session, item_name, test=False)

@bot.hybrid_command(name="testremove", description="[TEST] Remove an item from your test cart by name")
async def test_remove_item(ctx, item_name: str):
    await _remove_item_logic(ctx, test_session, item_name, test=True)

HALF_OFF_ALIASES = {"half", "50%", "50%off", "0.5x"}

async def _set_coupons_logic(ctx, session, args, test=False):
    await safely_delete_message(ctx)
    clear_cmd = "!testclear" if test else "!clear"
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
        await ctx.send(
            f"{prefix}✅ Added: {added_str}\n🎟️ All Loaded Coupons: {all_str}\n"
            f"*(Run `{clear_cmd}` to wipe coupons/cart and start fresh.)*"
        )
    except ValueError:
        await ctx.send(f"❌ Format error. Example: `!coupons 8 8 5 half`")

@bot.hybrid_command(name="coupons", description="Add coupon values to your session (e.g. 8 8 5 half)")
@app_commands.describe(values="List of spaces-separated values")
async def set_coupons(ctx, *, values: str):
    await _set_coupons_logic(ctx, current_session, values.split(), test=False)

@bot.hybrid_command(name="testcoupons", description="[TEST] Add coupon values to your test session")
@app_commands.describe(values="List of spaces-separated values")
async def test_set_coupons(ctx, *, values: str):
    await _set_coupons_logic(ctx, test_session, values.split(), test=True)

async def _optimize_logic(ctx, session, test=False):
    await safely_delete_message(ctx)
    prefix = "🧪 [TEST] " if test else ""
    items = session["items"]
    coupons = session["coupons"]
    if not items:
        await ctx.send("❌ Your cart is empty!")
        return

    # Keeps Discord lines open while processing combinatorial logic loops
    async with ctx.typing():
        total_due, bundling = await asyncio.to_thread(calculate_best_bundles, items, coupons)
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

    await ctx.send(embed=embed)

@bot.hybrid_command(name="optimize", description="Calculate the best coupon bundling strategy for your cart")
async def optimize_cart(ctx):
    await _optimize_logic(ctx, current_session, test=False)

@bot.hybrid_command(name="testoptimize", description="[TEST] Calculate the best strategy for your test cart")
async def test_optimize_cart(ctx):
    await _optimize_logic(ctx, test_session, test=True)

@bot.hybrid_command(name="clear", description="Clear your cart and coupons (no savings tracking)")
async def clear_cart(ctx):
    await safely_delete_message(ctx)
    current_session["items"] = []
    current_session["coupons"] = []
    current_session["cart_message"] = None
    await ctx.send("🧹 Cart and coupons cleared!")

@bot.hybrid_command(name="testclear", description="[TEST] Clear your test cart and coupons")
async def test_clear_cart(ctx):
    await safely_delete_message(ctx)
    test_session["items"] = []
    test_session["coupons"] = []
    test_session["cart_message"] = None
    await ctx.send("🧪 Test cart and coupons cleared!")

async def _checkout_logic(ctx, session, test=False):
    await safely_delete_message(ctx)
    items = session["items"]
    coupons = session["coupons"]
    if not items:
        await ctx.send("❌ Your cart is empty — nothing to check out!", delete_after=5)
        return

    subtotal = sum(item['price'] for item in items)
    total_due, _ = await asyncio.to_thread(calculate_best_bundles, items, coupons)
    coupon_spend = sum(coupon_cost(c) for c in coupons)
    gross_saved = subtotal - total_due
    net_saved = gross_saved - coupon_spend

    if test:
        embed = discord.Embed(title="🧪 [TEST] Checkout Preview", color=0x9b59b6)
        embed.add_field(name="Full Price (No Coupons)", value=f"${subtotal:.2f}", inline=True)
        embed.add_field(name="Register Total Paid", value=f"${total_due:.2f}", inline=True)
        embed.add_field(name="Spent on Coupons", value=f"${coupon_spend:.2f}", inline=True)
        embed.add_field(name="💰 Net Money Saved (Simulated)", value=f"## **${net_saved:.2f}**", inline=False)
        await ctx.send(embed=embed)
        session["items"] = []
        session["coupons"] = []
        session["cart_message"] = None
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
    save_savings(savings_tracker)

    embed = discord.Embed(title="✅ Trip Checked Out!", color=0x2ecc71)
    embed.add_field(name="🗓️ Date Logged", value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
    embed.add_field(name="Full Price (No Coupons)", value=f"${subtotal:.2f}", inline=True)
    embed.add_field(name="Register Total Paid", value=f"${total_due:.2f}", inline=True)
    embed.add_field(name="Spent on Coupons", value=f"${coupon_spend:.2f}", inline=True)
    embed.add_field(name="💰 Net Money Saved This Trip", value=f"## **${net_saved:.2f}**", inline=False)
    embed.add_field(name="📈 Lifetime Total Saved", value=f"**${savings_tracker['total_net_saved']:.2f}** across {savings_tracker['trip_count']} trip(s)", inline=False)
    
    is_ticket = ctx.guild is not None and session_channels.get(str(ctx.author.id)) == ctx.channel.id
    footer_note = "Run !savings for lifetime stats. Receipt copied to your DMs!"
    if is_ticket:
        footer_note += " This channel will auto-close in 10 seconds."
    embed.set_footer(text=footer_note)
    await ctx.send(embed=embed)

    session["items"] = []
    session["coupons"] = []
    session["cart_message"] = None

    try:
        item_str = "\n".join([f"• **{i['name']}**: ${i['price']:.2f}" for i in items]) or "No items."
        coupon_str = ", ".join(coupon_label(c) for c in coupons) or "None"
        dm_embed = discord.Embed(title="🧾 Your CVS Trip Receipt", color=0x2ecc71)
        dm_embed.add_field(name="🗓️ Date", value=now.strftime("%A, %B %d, %Y @ %I:%M %p"), inline=False)
        dm_embed.add_field(name="🛒 Items Purchased", value=item_str, inline=False)
        dm_embed.add_field(name="🎟️ Coupons Used", value=coupon_str, inline=False)
        dm_embed.add_field(name="Full Price", value=f"${subtotal:.2f}", inline=True)
        dm_embed.add_field(name="Paid at Register", value=f"${total_due:.2f}", inline=True)
        dm_embed.add_field(name="Coupon Cost", value=f"${coupon_spend:.2f}", inline=True)
        dm_embed.add_field(name="💰 Net Money Saved", value=f"## **${net_saved:.2f}**", inline=False)
        await ctx.author.send(embed=dm_embed)
    except discord.Forbidden:
        await ctx.send(f"⚠️ {ctx.author.mention} I couldn't DM you a receipt — check your privacy configs.", delete_after=8)

    if is_ticket:
        session_channels.pop(str(ctx.author.id), None)
        save_session_channels(session_channels)
        await asyncio.sleep(10)
        try: await ctx.channel.delete()
        except discord.HTTPException: pass

@bot.hybrid_command(name="checkout", description="Finalize your trip, log savings, and clear your cart")
async def checkout(ctx):
    await _checkout_logic(ctx, current_session, test=False)

@bot.hybrid_command(name="testcheckout", description="[TEST] Preview checkout without tracking savings")
async def test_checkout(ctx):
    await _checkout_logic(ctx, test_session, test=True)

@bot.hybrid_command(name="savings", description="View your lifetime savings stats")
async def view_savings(ctx):
    await safely_delete_message(ctx)
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
    await ctx.send(embed=embed)

def _parse_history_date(raw):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try: return datetime.strptime(raw, fmt).date()
        except ValueError: continue
    return None

@bot.hybrid_command(name="history", description="View past trips (all, one date, or a date range)")
async def view_history(ctx, start: str = None, end: str = None):
    await safely_delete_message(ctx)
    trips = savings_tracker.get("trips", [])
    if not trips:
        await ctx.send("📭 No checked-out trips logged yet.", delete_after=8)
        return

    start_date, end_date = None, None
    if start:
        start_date = _parse_history_date(start)
        if not start_date:
            await ctx.send("❌ Couldn't read date format. Use `YYYY-MM-DD`.", delete_after=8)
            return
        end_date = _parse_history_date(end) if end else start_date
        if end and not end_date:
            await ctx.send("❌ Invalid end date.", delete_after=8)
            return

    matches = [t for t in trips if start_date <= datetime.strptime(t["date"], "%Y-%m-%d").date() <= end_date] if start_date else trips[-10:]
    if not matches:
        await ctx.send("📭 No matching records found.", delete_after=8)
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
    await ctx.send(embed=embed)

@bot.hybrid_command(name="nuke", description="Bulk delete messages in this channel")
@commands.has_permissions(manage_messages=True)
async def nuke(ctx, amount: str):
    await safely_delete_message(ctx)
    if amount.lower() == "all":
        deleted = await ctx.channel.purge(limit=None)
    else:
        try: count = int(amount)
        except ValueError: return
        deleted = await ctx.channel.purge(limit=count)
    await ctx.send(f"🧨 Nuked **{len(deleted)}** message(s)!", delete_after=5)

@bot.hybrid_command(name="createrole", description="Create a new server role")
@commands.has_permissions(manage_roles=True)
async def create_role(ctx, role_name: str, *, color_name: str = None):
    await safely_delete_message(ctx)
    if discord.utils.get(ctx.guild.roles, name=role_name): return
    color = resolve_color(color_name) or discord.Color.default()
    new_role = await ctx.guild.create_role(name=role_name, color=color)
    await ctx.send(f"✅ Created role {new_role.mention}!", delete_after=6)

@bot.hybrid_command(name="deleterole", description="Delete a server role")
@commands.has_permissions(manage_roles=True)
async def delete_role(ctx, *, role_name: str):
    await safely_delete_message(ctx)
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role: await role.delete()
    await ctx.send(f"🗑️ Deleted role `{role_name}`.", delete_after=6)

@bot.hybrid_command(name="roleadd", description="Give a member a role")
@commands.has_permissions(manage_roles=True)
async def role_add(ctx, member: discord.Member, *, role_name: str):
    await safely_delete_message(ctx)
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role and role not in member.roles: await member.add_roles(role)
    await ctx.send(f"✅ Gave {member.mention} the `{role_name}` role.", delete_after=6)

@bot.hybrid_command(name="roleremove", description="Take a role away from a member")
@commands.has_permissions(manage_roles=True)
async def role_remove(ctx, member: discord.Member, *, role_name: str):
    await safely_delete_message(ctx)
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role and role in member.roles: await member.remove_roles(role)
    await ctx.send(f"✅ Removed the `{role_name}` role from {member.mention}.", delete_after=6)

@bot.hybrid_command(name="createchannel", description="Create a new text channel")
@commands.has_permissions(manage_channels=True)
async def create_channel(ctx, name: str, visibility: Literal["public", "private"] = "public", role: discord.Role = None):
    await safely_delete_message(ctx)
    overwrites = {}
    if visibility.lower() == "private":
        overwrites[ctx.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        overwrites[ctx.author] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    channel = await ctx.guild.create_text_channel(name=name, category=ctx.channel.category, overwrites=overwrites if overwrites else discord.utils.MISSING)
    await ctx.send(f"✅ Created channel {channel.mention}", delete_after=8)

@bot.hybrid_command(name="blockrole", description="Hide a channel from a role")
@commands.has_permissions(manage_channels=True)
async def block_role(ctx, role: discord.Role, channel: discord.TextChannel = None):
    await safely_delete_message(ctx)
    channel = channel or ctx.channel
    await channel.set_permissions(role, view_channel=False)
    await ctx.send(f"🚫 `{role.name}` role blocked from #{channel.name}.", delete_after=6)

@bot.hybrid_command(name="unblockrole", description="Reset a role's channel access to default")
@commands.has_permissions(manage_channels=True)
async def unblock_role(ctx, role: discord.Role, channel: discord.TextChannel = None):
    await safely_delete_message(ctx)
    channel = channel or ctx.channel
    await channel.set_permissions(role, overwrite=None)
    await ctx.send(f"✅ Reset `{role.name}` access for #{channel.name}.", delete_after=6)

@bot.hybrid_group(name="ticket", description="Manage optimizer ticket channels", invoke_without_command=True)
async def ticket(ctx):
    await ctx.send("Usage: `/ticket close`", delete_after=8)

@ticket.command(name="close", description="Close this coupon optimizer ticket channel")
@is_staff_or_channel_manager()
async def ticket_close(ctx):
    await safely_delete_message(ctx)
    owner_id = next((uid for uid, cid in session_channels.items() if cid == ctx.channel.id), None)
    if owner_id:
        session_channels.pop(owner_id, None)
        save_session_channels(session_channels)
    await ctx.send(f"🔒 Ticket closed. Deleting channel in 5 seconds...")
    await asyncio.sleep(5)
    try: await ctx.channel.delete()
    except discord.HTTPException: pass

@bot.hybrid_command(name="whocansee", description="List members who can view a channel")
@commands.has_permissions(manage_channels=True)
async def who_can_see(ctx, channel: discord.TextChannel = None):
    await safely_delete_message(ctx)
    channel = channel or ctx.channel
    async with ctx.typing():
        members_with_access = [m for m in ctx.guild.members if channel.permissions_for(m).view_channel]
    members_with_access.sort(key=lambda m: m.display_name.lower())
    embed = discord.Embed(title=f"👀 Access Audit for #{channel.name}", description="\n".join([f"• {m.mention}" for m in members_with_access]) or "None", color=0x3498db)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="ping", description="Check the bot's latency")
async def ping(ctx):
    await safely_delete_message(ctx)
    await ctx.send(f"🏓 Pong! Latency: **{round(bot.latency * 1000)}ms**", delete_after=8)

@bot.hybrid_command(name="about", description="About the CVS Coupon Calculator bot")
async def about(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="ℹ ... About the CVS Coupon Calculator", 
        description="A combinatorics-powered assistant that splits your cart across coupons to minimize what you pay at register.", 
        color=0xcc0000
    )
    embed.add_field(name="Commands", value="Run `!help` for the full walkthrough.", inline=False)
    embed.add_field(name="Hosting", value="Running 24/7 on Railway.", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("⛔ Security Error: Owner command access validation failed.", delete_after=10)
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ Permission Error: Account lacks server authority.", delete_after=8)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(f"⛔ Permission Error: Staff check fallback triggered.", delete_after=8)

token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
bot.run(token)
