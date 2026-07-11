import discord
from discord.ext import commands
import itertools
from flask import Flask
from threading import Thread
import os

# 1. BACKGROUND WEB SERVER (Keeps the bot running 24/7)
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
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None) # Disables default help to use our custom one

current_session = {"items": [], "coupons": []}

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

@bot.command(name="help")
async def help_menu(ctx):
    embed = discord.Embed(
        title="📖 CVS Coupon Calculator — Help Menu", 
        description="Follow this quick blueprint to maximize your coupon values and slash your out-of-pocket register total.", 
        color=0xcc0000
    )
    
    embed.add_field(
        name="🎟️ 1. Load Your Coupons", 
        value="Tell the bot what coupons you have available.\n`!coupons [value1] [value2] ...`\n*Example:* `!coupons 8 8 5`", 
        inline=False
    )
    
    embed.add_field(
        name="🛒 2. Add Cart Items", 
        value="Scan items in as you shop using a single-word name and price.\n`!add [item_name] [price]`\n*Example:* `!add shampoo 6.59`", 
        inline=False
    )
    
    embed.add_field(
        name="📊 3. Calculate Strategy", 
        value="Run the engine to sort items into optimized register bundles.\n`!optimize`", 
        inline=False
    )
    
    embed.add_field(
        name="🧹 4. Clear Session", 
        value="Wipe the current cart and coupon stack to start fresh.\n`!clear`", 
        inline=False
    )
    
    embed.set_footer(text="Tip: Keep item names to a single word for best formatting.")
    await ctx.send(embed=embed)

@bot.command(name="add")
async def add_item(ctx, item_name: str, price: float):
    current_session["items"].append({"name": item_name, "price": price})
    
    embed = discord.Embed(title="🛒 CVS Shopping Cart", color=0xcc0000)
    item_str = "\n".join([f"• **{item['name']}**: ${item['price']:.2f}" for item in current_session["items"]])
    subtotal = sum(item['price'] for item in current_session["items"])
    
    embed.add_field(name="Scanned Items", value=item_str or "No items added yet.", inline=False)
    embed.add_field(name="Current Subtotal", value=f"**${subtotal:.2f}**")
    await ctx.send(embed=embed)

@bot.command(name="coupons")
async def set_coupons(ctx, *args):
    try:
        coupons = sorted([float(x) for x in args], reverse=True)
        current_session["coupons"] = coupons
        await ctx.send(f"✅ Loaded Coupons: " + ", ".join([f"${c:.2f}" for c in coupons]))
    except ValueError:
        await ctx.send("❌ Format error. Example: `!coupons 8 8 5`")

@bot.command(name="optimize")
async def optimize_cart(ctx):
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
    embed.add_field(name="📊 Final Register Total Due", value=f"### **${total_due:.2f}**", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="clear")
async def clear_cart(ctx):
    current_session["items"] = []
    current_session["coupons"] = []
    await ctx.send("🧹 Cart and coupons cleared!")

token = os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or os.environ.get('token')
bot.run(token)
