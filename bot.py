import discord
from discord.ext import commands
from discord.ui import View, Button
import datetime
import os
from flask import Flask
from threading import Thread
import json

# -----------------------------
# CONFIG
# -----------------------------
TOKEN = os.getenv("TOKEN")  # set on Render
MAIN_CHANNEL_ID = 1393285181590212788  # permanent "Log Shift" embed
LOG_CHANNEL_ID = 1406476956802875423   # shift logs channel
GUILD_ID = 1389462240968577086         # your server ID
MAIN_EMBED_FILE = "main_embed.json"    # cache message id (best-effort)

# -----------------------------
# SHIFT DATA
# -----------------------------
shift_data = {}          # user_id -> {start_time, worked_time, status, last_action}
active_dm_users = set()  # users with DM menu open

# -----------------------------
# KEEP-ALIVE WEB SERVER (Render requires listening on $PORT)
# -----------------------------
app = Flask(__name__)

@app.get("/")
def home():
    return "Shift Logger bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    Thread(target=run_web, daemon=True).start()

# -----------------------------
# VIEWS
# -----------------------------
class ShiftMenu(View):
    def __init__(self, user: discord.User):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Start Shift", style=discord.ButtonStyle.success, custom_id="start_shift")
    async def start_shift(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn’t your shift menu.", ephemeral=True)

        data = shift_data.get(interaction.user.id, {})
        if data.get("status") == "running":
            return await interaction.response.send_message("Your shift is already running.", ephemeral=True)

        now = datetime.datetime.now()
        if "start_time" not in data or data.get("status") == "ended":
            data["start_time"] = now
            data["worked_time"] = datetime.timedelta(0)

        data["status"] = "running"
        data["last_action"] = now
        shift_data[interaction.user.id] = data

        self.children[0].disabled = True   # Start
        self.children[1].disabled = False  # Pause
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Pause Shift", style=discord.ButtonStyle.secondary, custom_id="pause_shift", disabled=True)
    async def pause_shift(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn’t your shift menu.", ephemeral=True)

        data = shift_data.get(interaction.user.id, {})
        if data.get("status") != "running":
            return await interaction.response.send_message("Start your shift first.", ephemeral=True)

        now = datetime.datetime.now()
        worked = now - data["last_action"]
        data["worked_time"] += worked
        data["status"] = "paused"
        data["last_action"] = now
        shift_data[interaction.user.id] = data

        self.children[0].disabled = False  # Start
        self.children[1].disabled = True   # Pause
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="End Shift", style=discord.ButtonStyle.danger, custom_id="end_shift")
    async def end_shift(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn’t your shift menu.", ephemeral=True)

        confirm_view = ConfirmEndShift(interaction.user)
        embed = discord.Embed(
            title="Confirm End Shift",
            description="Are you sure you want to end your shift?",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)


class ConfirmEndShift(View):
    def __init__(self, user: discord.User):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn’t your confirmation.", ephemeral=True)

        data = shift_data.get(interaction.user.id, {})
        now = datetime.datetime.now()

        if data.get("status") == "running":
            worked = now - data["last_action"]
            data["worked_time"] += worked

        start_time = data.get("start_time")
        end_time = now
        worked_time = data.get("worked_time", datetime.timedelta(0))
        total_time = end_time - start_time
        break_time = total_time - worked_time

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        embed = discord.Embed(title="Shift Log", color=discord.Color.green())
        embed.add_field(name="Name", value=interaction.user.mention, inline=False)
        embed.add_field(name="Date", value=start_time.strftime("%Y-%m-%d"), inline=False)
        embed.add_field(name="Time Started", value=start_time.strftime("%H:%M:%S"), inline=True)
        embed.add_field(name="Time Ended", value=end_time.strftime("%H:%M:%S"), inline=True)
        embed.add_field(name="Break Time", value=str(break_time), inline=False)
        embed.add_field(name="Total Time", value=str(total_time), inline=False)
        embed.add_field(name="Total Time Working", value=str(worked_time), inline=False)
        await log_channel.send(embed=embed)

        data["status"] = "ended"
        shift_data[interaction.user.id] = data
        active_dm_users.discard(interaction.user.id)
        await interaction.response.send_message("✅ Shift ended and logged!", ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn’t your confirmation.", ephemeral=True)
        active_dm_users.discard(interaction.user.id)
        await interaction.response.send_message("❌ Cancelled. Returning to shift menu.", ephemeral=True)


class MainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log Shift", style=discord.ButtonStyle.success, custom_id="log_shift")
    async def log_shift(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in active_dm_users:
            return await interaction.response.send_message(
                "⚠️ You already have a shift menu open or a shift running!",
                ephemeral=True
            )

        active_dm_users.add(interaction.user.id)
        embed = discord.Embed(
            title="Shift Controls",
            description="Use the buttons below to manage your shift.",
            color=discord.Color.blue()
        )
        try:
            await interaction.user.send(embed=embed, view=ShiftMenu(interaction.user))
            await interaction.response.send_message("📩 Check your DMs!", ephemeral=True)
        except discord.Forbidden:
            # User has DMs closed
            active_dm_users.discard(interaction.user.id)
            await interaction.response.send_message("⚠️ I can’t DM you. Please enable DMs from server members.", ephemeral=True)

# -----------------------------
# BOT SETUP
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def load_main_embed_id():
    if os.path.exists(MAIN_EMBED_FILE):
        try:
            with open(MAIN_EMBED_FILE, "r") as f:
                return json.load(f).get("message_id")
        except Exception:
            return None
    return None

def save_main_embed_id(message_id: int):
    try:
        with open(MAIN_EMBED_FILE, "w") as f:
            json.dump({"message_id": message_id}, f)
    except Exception:
        pass

async def find_existing_main_embed(channel: discord.TextChannel):
    """Fallback: look for an existing 'Shift Logger' embed posted by this bot."""
    async for msg in channel.history(limit=50):
        if msg.author.id == bot.user.id and msg.embeds:
            for e in msg.embeds:
                if (e.title or "").strip().lower() == "shift logger":
                    return msg
    return None

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    keep_alive()  # start tiny web server

    channel = bot.get_channel(MAIN_CHANNEL_ID)
    if not channel:
        print("Main channel not found. Check MAIN_CHANNEL_ID.")
        return

    msg_id = load_main_embed_id()
    msg = None

    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
        except Exception:
            msg = None

    if msg is None:
        # try to find any existing main embed to avoid duplicates
        msg = await find_existing_main_embed(channel)

    if msg:
        try:
            await msg.edit(view=MainView())  # reattach live buttons after restart
            print("Reattached MainView to existing main embed.")
            return
        except Exception:
            pass

    # send a fresh main embed
    embed = discord.Embed(
        title="Shift Logger",
        description="Click below to log your shift!",
        color=discord.Color.green()
    )
    msg = await channel.send(embed=embed, view=MainView())
    save_main_embed_id(msg.id)
    print(f"Posted main embed message id: {msg.id}")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("ERROR: Set TOKEN env var.")
    bot.run(TOKEN)