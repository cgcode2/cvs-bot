import discord
from discord.ext import commands
import itertools
from flask import Flask
from threading import Thread
import os
import sys

# 1. BACKGROUND WEB SERVER
app = Flask('')
@app.route('/')
def home(): 
    return "Coupon Calculator is running 24/7!"
def run_server(): 
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
def keep_alive(): 
    Thread(target=run_server).start()

keep_alive()

# 2. DISCORD BOT ENGINE
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

current_session = {"items": [], "coupons": [], "cart_message": None}

async def send_cart_embed(ctx, embed):
    """Sends the cart embed, deleting the previous one so only one is ever visible."""
    old_message = current_session.get("cart_message")
    if old_message is not None:
        try:
            await old_message.delete()
        except Exception as e:
            print(f"❌ Old cart embed deletion failed! Error Type: {type(e).__name__} | Details: {e}", file=sys.stderr)
    current_session["cart_message"] = await ctx.send(embed=embed)

async def safely_delete_message(ctx):
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"❌ Deletion Failed! Error Type: {type(e).__name__} | Details: {e}", file=sys.stderr)

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
            group_sum = sum(item['price'] for item in group_items)
            coupon_val = coupons[group_idx]
            current_total_due += max(0.0, group_sum - coupon_val)

        if current_total_due < best_total_due:
            best_total_due = current_total_due
            best_distribution = groups
    return best_total_due, best_distribution

@bot.event
async def on_ready():
    print(f'🤖 Coupon Calculator is officially online via Replit!')

@bot.command(name="setup")
@commands.is_owner()
async def setup_channel(ctx):
    await safely_delete_message(ctx)
    guild = ctx.guild

    # guild.owner can be None if the member isn't cached (no Members intent) — fetch it explicitly
    owner = guild.owner or await guild.fetch_member(guild.owner_id)

    # Configure permission rules: Block everyone, but allow the server owner and the bot
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True, manage_messages=True)
    }
    
    channel_name = "cvs-coupon-optimizer"
    
    # Check if channel already exists
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
    if existing_channel:
        await ctx.send(f"⚠️ A channel named `#{channel_name}` already exists!", delete_after=5)
        return
        
    new_channel = await guild.create_text_channel(channel_name, overwrites=overwrites)
    
    welcome_embed = discord.Embed(
        title="🎯 CVS Coupon Optimizer Room",
        description="This is your secure, private command base for calculated shopping bundles! Type `!help` to see directions.",
        color=0xcc0000
    )
    await new_channel.send(embed=welcome_embed)
    await ctx.send(f"✅ Secure channel {new_channel.mention} successfully built!", delete_after=5)

@bot.command(name="permit")
@commands.is_owner()
async def permit_user(ctx, member: discord.Member):
    await safely_delete_message(ctx)
    channel_name = "cvs-coupon-optimizer"
    channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)
    
    if not channel:
        await ctx.send("❌ Error: The `#cvs-coupon-optimizer` channel does not exist yet. Run `!setup` first!", delete_after=5)
        return
        
    # Explicitly overlay viewing access for the pinged user
    await channel.set_permissions(member, view_channel=True, send_messages=True, read_messages=True)
    await ctx.send(f"✅ Granted access to {member.mention} to use the optimizer room!", delete_after=5)

@bot.command(name="help")
async def help_menu(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="📖 CVS Coupon Calculator — Help Menu", 
        description="Follow this quick blueprint to maximize your coupon values and slash your out-of-pocket register total.", 
        color=0xcc0000
    )
    embed.add_field(name="🎟️ 1. Load Your Coupons", value="`!coupons [value1] [value2] ...`\n*Example:* `!coupons 8 8 5`", inline=False)
    embed.add_field(name="🛒 2. Add Cart Items", value="`!add [item_name] [price] ...`\n*Example:* `!add Fairlife 4.49 shampoo 6.59`", inline=False)
    embed.add_field(name="↩️ 3. Undo Last Add", value="`!undo`", inline=False)
    embed.add_field(name="❌ 4. Remove Cart Items", value="`!remove [item_name]`\n*Example:* `!remove Fairlife`", inline=False)
    embed.add_field(name="👀 5. View Cart", value="`!cart`", inline=False)
    embed.add_field(name="📊 6. Calculate Strategy", value="`!optimize`", inline=False)
    embed.add_field(name="🧹 7. Clear Session", value="`!clear`", inline=False)
    embed.add_field(name="🧨 8. Nuke Messages", value="`!nuke [amount]` or `!nuke all`\n*Requires Manage Messages permission.*", inline=False)
    embed.add_field(name="🏓 9. Bot Status", value="`!ping`", inline=False)
    embed.add_field(name="ℹ️ 10. About This Bot", value="`!about`", inline=False)
    embed.set_footer(text="Tip: Keep item names to a single word for best formatting.")
    await ctx.send(embed=embed)

@bot.command(name="add")
async def add_item(ctx, *args):
    await safely_delete_message(ctx)

    if len(args) == 0 or len(args) % 2 != 0:
        await ctx.send(
            "❌ Format error. Provide item/price pairs.\n*Example:* `!add shampoo 6.59 soap 2.99 gum 1.29`",
            delete_after=8
        )
        return

    added = []
    try:
        for i in range(0, len(args), 2):
            item_name = args[i]
            price = float(args[i + 1])
            current_session["items"].append({"name": item_name, "price": price})
            added.append(item_name)
    except ValueError:
        await ctx.send(
            "❌ Format error. Each item must be followed by a numeric price.\n*Example:* `!add shampoo 6.59 soap 2.99`",
            delete_after=8
        )
        return

    embed = discord.Embed(title="🛒 CVS Shopping Cart", color=0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
    subtotal = sum(item['price'] for item in current_session["items"])
    embed.add_field(name=f"Added: {', '.join(added)}", value="\u200b", inline=False)
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(ctx, embed)

@bot.command(name="undo")
async def undo_item(ctx):
    await safely_delete_message(ctx)
    if not current_session["items"]:
        await ctx.send("❌ Nothing to undo — your cart is empty!", delete_after=5)
        return

    removed_item = current_session["items"].pop()
    embed = discord.Embed(title="↩️ Last Item Undone", color=0xe67e22)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
    subtotal = sum(item['price'] for item in current_session["items"])
    embed.add_field(name=f"Removed: {removed_item['name']} (${removed_item['price']:.2f})", value="\u200b", inline=False)
    embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
    embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
    await send_cart_embed(ctx, embed)

@bot.command(name="cart")
async def view_cart(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(title="🛒 CVS Shopping Cart", color=0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
    subtotal = sum(item['price'] for item in current_session["items"])
    coupon_str = ", ".join([f"${c:.2f}" for c in current_session["coupons"]]) or "None loaded yet."
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**", inline=False)
    embed.add_field(name="🎟️ Loaded Coupons", value=coupon_str, inline=False)
    await send_cart_embed(ctx, embed)

@bot.command(name="remove")
async def remove_item(ctx, item_name: str):
    await safely_delete_message(ctx)
    found = False
    for item in reversed(current_session["items"]):
        if item["name"].lower() == item_name.lower():
            current_session["items"].remove(item)
            found = True
            break
    if found:
        embed = discord.Embed(title="❌ Item Removed from Cart", color=0xe67e22)
        item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
        subtotal = sum(item['price'] for item in current_session["items"])
        embed.add_field(name=f"Removed item: {item_name}", value=f"Here is your updated cart list:", inline=False)
        embed.add_field(name="Remaining Items", value=item_str or "No items left in cart.", inline=False)
        embed.add_field(name="Updated Subtotal", value=f"**${subtotal:.2f}**")
        await send_cart_embed(ctx, embed)
    else:
        await ctx.send(f"⚠️ Could not find an item named '**{item_name}**' inside your current cart.", delete_after=5)

@bot.command(name="coupons")
async def set_coupons(ctx, *args):
    await safely_delete_message(ctx)
    try:
        coupons = sorted([float(x) for x in args], reverse=True)
        current_session["coupons"] = coupons
        await ctx.send(f"✅ Loaded Coupons: " + ", ".join([f"${c:.2f}" for c in coupons]))
    except ValueError:
        await ctx.send("❌ Format error. Example: `!coupons 8 8 5`")

@bot.command(name="optimize")
async def optimize_cart(ctx):
    await safely_delete_message(ctx)
    items = current_session["items"]
    coupons = current_session["coupons"]
    if not items:
        await ctx.send("❌ Your cart is empty!")
        return
        
    total_due, bundling = calculate_best_bundles(items, coupons)
    embed = discord.Embed(title="🧾 Optimized CVS Checkout Strategy", color=0x00ff00)
    
    for idx, coupon_val in enumerate(coupons):
        group_items = bundling.get(idx, [])
        if group_items:
            item_details = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in group_items])
            subtotal = sum(item['price'] for item in group_items)
            due = max(0.0, subtotal - coupon_val)
            embed.add_field(
                name=f"Transaction {idx+1}: Use ${coupon_val:.2f} Coupon",
                value=f"{item_details}\n*Subtotal: ${subtotal:.2f}* ➔ **Due: ${due:.2f}**",
                inline=False
            )
    
    embed.add_field(name="📊 Final Register Total Due", value=f"## **${total_due:.2f}**", inline=False)

    if total_due > 0.0:
        standard_coupons = [2.00, 3.00, 4.00, 5.00, 6.00, 7.00, 8.00, 10.00]
        recommended_coupon = None
        for cp in standard_coupons:
            if cp >= total_due:
                recommended_coupon = cp
                break
        if not recommended_coupon:
            recommended_coupon = standard_coupons[-1]
            
        savings = min(total_due, recommended_coupon)
        new_total = max(0.0, total_due - recommended_coupon)
        
        upgrade_text = (
            f"💡 *You have a remaining balance of **${total_due:.2f}**.*\n"
            f"➔ **Recommendation:** Pick up or buy an extra **${recommended_coupon:.0f}.00 Off** coupon.\n"
            f"• This saves you an extra **${savings:.2f}** right now.\n"
            f"• Your new register balance drops to **${new_total:.2f}**!"
        )
        embed.add_field(name="✨ Smart Coupon Upgrade Advice", value=upgrade_text, inline=False)

    await ctx.send(embed=embed)

@bot.command(name="clear")
async def clear_cart(ctx):
    await safely_delete_message(ctx)
    current_session["items"] = []
    current_session["coupons"] = []
    current_session["cart_message"] = None
    await ctx.send("🧹 Cart and coupons cleared!")

@bot.command(name="nuke")
@commands.has_permissions(manage_messages=True)
async def nuke(ctx, amount: str):
    await safely_delete_message(ctx)

    if amount.lower() == "all":
        deleted = await ctx.channel.purge(limit=None)
    else:
        try:
            count = int(amount)
        except ValueError:
            await ctx.send("❌ Format error. Use a number or `all`.\n*Example:* `!nuke 5` or `!nuke all`", delete_after=8)
            return
        if count <= 0:
            await ctx.send("❌ Please provide a number greater than 0.", delete_after=8)
            return
        deleted = await ctx.channel.purge(limit=count)

    confirmation = await ctx.send(f"🧨 Nuked **{len(deleted)}** message(s)!")
    await confirmation.delete(delay=5)

@bot.command(name="ping")
async def ping(ctx):
    await safely_delete_message(ctx)
    await ctx.send(f"🏓 Pong! Latency: **{round(bot.latency * 1000)}ms**", delete_after=8)

@bot.command(name="about")
async def about(ctx):
    await safely_delete_message(ctx)
    embed = discord.Embed(
        title="ℹ️ About the CVS Coupon Calculator",
        description="A combinatorics-powered assistant that splits your cart across coupons to minimize what you pay at register.",
        color=0xcc0000
    )
    embed.add_field(name="Commands", value="Run `!help` for the full walkthrough.", inline=False)
    embed.add_field(name="Hosting", value="Running 24/7 on Replit.", inline=False)
    await ctx.send(embed=embed)

# Checks security against the core Discord account token creator
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("⛔ Security Error: Only the bot's application owner can run this command.", delete_after=10)
    elif isinstance(error, discord.Forbidden):
        await ctx.send(f"❌ Permission Error: The bot is missing Discord permissions to do that (needs **Manage Channels**). Details: {error.text}", delete_after=15)
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ Permission Error: You need the **Manage Messages** permission to run that.", delete_after=8)
    else:
        print(f"❌ Command Error in '{ctx.command}': {type(error).__name__} | Details: {error}", file=sys.stderr)
        await ctx.send(f"❌ Unexpected error running `{ctx.command}`: `{type(error).__name__}: {error}`", delete_after=15)

token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
bot.run(token)
