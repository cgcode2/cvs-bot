import discord
from discord.ext import commands
import itertools
import os
import sys

# DISCORD BOT ENGINE ONLY
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

current_session = {"items": [], "coupons": []}

async def safely_delete_message(ctx):
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"❌ Deletion Failed! Details: {e}", file=sys.stderr)

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
    print(f'🤖 Coupon Calculator is officially online via Render Background Worker!')

@bot.command(name="setup")
@commands.is_owner()
async def setup_channel(ctx):
    await safely_delete_message(ctx)
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
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
    embed.add_field(name="🛒 2. Add Cart Items", value="`!add [item_name] [price]`\n*Example:* `!add Fairlife 4.49`", inline=False)
    embed.add_field(name="❌ 3. Remove Cart Items", value="`!remove [item_name]`\n*Example:* `!remove Fairlife`", inline=False)
    embed.add_field(name="📊 4. Calculate Strategy", value="`!optimize`", inline=False)
    embed.add_field(name="🧹 5. Clear Session", value="`!clear`", inline=False)
    embed.set_footer(text="Tip: Keep item names to a single word for best formatting.")
    await ctx.send(embed=embed)

@bot.command(name="add")
async def add_item(ctx, item_name: str, price: float):
    await safely_delete_message(ctx)
    current_session["items"].append({"name": item_name, "price": price})
    embed = discord.Embed(title="🛒 CVS Shopping Cart", color=0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
    subtotal = sum(item['price'] for item in current_session["items"])
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**")
    await ctx.send(embed=embed)

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
        await ctx.send(embed=embed)
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
