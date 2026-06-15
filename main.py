from sys import prefix
import asyncio
import discord
from discord.ext import commands, tasks
import os
from datetime import datetime, date, timezone,time
import json
from ollama import AsyncClient, chat
import yfinance as yf
from zoneinfo import ZoneInfo

intents = discord.Intents.default()
intents.message_content = True

#AI
ollama_client = AsyncClient()
chat_history = []

#SCHEDULE
Romanian_TZ=ZoneInfo("Europe/Bucharest")
afternoon_target_time = time(hour=17, minute=30, tzinfo=Romanian_TZ)
reminders_folder_path=r"..."

STATE_FILE = "last_message_id.json"
obsidian_vault_root=r"..."
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ROUTING = {
    "ideas": {
        "folder": "Ideas",
        "title_prefix": "Idea"
    },
    "tasks": {
        "folder": "Tasks",
        "title_prefix": "Task"
    },
    "reminders": {
        "folder": "Reminders",
        "title_prefix": "Reminder"
    }
}

sp = yf.Ticker("^GSPC")
def sp_performance(sp):
    today = sp.history(period="1d", interval="5m")
    daily = sp.history(period="10d")

    if today.empty or daily.empty:
        return None

    current_price = float(today["Close"].iloc[-1])
    open_price = float(today["Open"].iloc[0])

    today_return = (current_price / open_price - 1) * 100

    week_ago_close = float(daily["Close"].iloc[-6])
    latest_close = float(daily["Close"].iloc[-1])

    week_return = (latest_close / week_ago_close - 1) * 100

    return {
        "current_price": round(current_price, 2),
        "today_return_pct": round(today_return, 2),
        "today_high": round(float(today["High"].max()), 2),
        "today_low": round(float(today["Low"].min()), 2),
        "today_range_pct": round(
            (today["High"].max() / today["Low"].min() - 1) * 100,
            2,
        ),
        "week_return_pct": round(week_return, 2),
    }

def filename_strip(content: str, prefix: str) -> str:
    truncated = content[:20].strip()
    clean_title = "".join([carac for carac in truncated if carac.isalnum() or carac == ' ']).strip()

    if not clean_title:
        clean_title = "Untitled"

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return f"{prefix}-{clean_title}-{timestamp}.md"


def generate_md(content: str, title: str) -> str:
    date_str = datetime.now().strftime("%d-%m-%Y")
    return f"""---
created: {date_str}
ai processed: false
---
# {title}

{content}
"""

async def general_list(message):
    user_text = message.content.lower().strip()
    state = check_new_message()

    if "general" in state:
        last_id = state["general"]
        last_message_time = discord.utils.snowflake_time(last_id)

        current_time = datetime.now(timezone.utc)

        if (current_time - last_message_time).days > 0:
            print("🧹 More than 24 hours since last activity. Purging channel...")
            deleted = await message.channel.purge(limit=51)
            await message.channel.send(f"🧹 Swept away {len(deleted) - 1} old messages!", delete_after=3.0)


    state["general"] = message.id
    save_message_id(state)
    if user_text in CHANNEL_ROUTING:
        route = CHANNEL_ROUTING[user_text]
        target_folder = os.path.join(obsidian_vault_root, route["folder"])

        try:

            all_files = os.listdir(target_folder)


            if len(all_files) == 0:
                await message.channel.send(f"📁 Your **{route['folder']}** folder is completely empty!")
                return


            clean_names = []
            for file in all_files:
                clean_names.append(f"• {file.replace('.md', '')}")


            final_text = "\n".join(clean_names)


            await message.channel.send(f"**Here is your list of {route['folder']}:**\n{final_text}")

        except FileNotFoundError:
            await message.channel.send(f"❌ Error: Could not find the folder at `{target_folder}`")

def check_new_message():
    print("\n=== LOADING STATE ===")

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as file:
            state = json.load(file)

        print(f"Loaded state: {state}")
        return state

    print("State file does not exist.")
    return {}


def save_message_id(state: dict):
    print(f"\n=== SAVING STATE ===")
    print(state)

    with open(STATE_FILE, "w") as file:
        json.dump(state, file)

async def clear_messages(message):
    prompt = await message.channel.send("⚠️ **SECURITY VERIFICATION REQUIRED**\n"
        "Are you sure you want to purge this entire channel ledger? Respond with **y** or **n**.")
    def check(m):
        return (m.author == message.author and m.channel == message.channel and m.content.lower() in ["y", "n","yes","no"])
    try:
        response = await bot.wait_for("message", check=check, timeout=15)
        if response.content.lower() in ["y","yes"]:
            await message.channel.send(" *Initiating channel cleaning....")
            deleted = await message.channel.purge(limit=None)
            await message.channel.send(
                f"📟 **SYSTEM PURGE COMPLETE**\n"
                f"Source code cleared. {len(deleted)} messages successfully dropped from the ledger.",
                delete_after=5.0
            )
        else:
            await message.channel.send("❌ *Purge sequence aborted. Data ledger remains intact.*", delete_after=5.0)
            await prompt.delete()
            await response.delete()
            await message.delete()

    except asyncio.TimeoutError:
        await message.channel.send("⏱️ *Verification timeout. Operation canceled automatically.*", delete_after=5.0)
        await prompt.delete()
        await message.delete()

    except Exception as e:
        print(f"❌ Purge failed: {e}")

async def ai_chat(message):
    user_text = message.content
    chat_history.append({'role': 'user', 'content': user_text})
    if len(chat_history) > 10:
        chat_history.pop(0)

    async with message.channel.typing():
        try:
            response = await ollama_client.chat(model='gemma2', messages=chat_history)
            ai_reply = response['message']['content']
            chat_history.append({'role': 'assistant', 'content': ai_reply})

            await message.channel.send(f"👋 Hi, {message.author.mention}!\n\n{ai_reply}")
        except Exception as e:
            print(f"A problem occured: {e}")
            await message.channel.send("❌ There was a problem accessing the assistant.")

def fetch_reminders():
    combined_reminders=[]
    if os.path.exists(reminders_folder_path) and os.path.isdir(reminders_folder_path):
        for filename in os.listdir(reminders_folder_path):
            if filename.endswith(".md"):
                file_path = os.path.join(reminders_folder_path, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        content = file.read().strip()
                        if content:
                            category_name = filename[:-3]
                            combined_reminders.append(f"### Category: {category_name}\n{content}")
                except Exception as e:
                    print(f"Error reading file {filename}: {e}")

    if combined_reminders:
        return "\n\n".join(combined_reminders)

    return "No active reminder files found in the folder right now."

@tasks.loop(time=afternoon_target_time)
async def afternoon_scheduled_message():
    channel=bot.get_channel(...)
    if not channel: return

    print("🌆 Running afternoon system review...")
    all_reminders=fetch_reminders()

    market_data=sp_performance(sp)
    if market_data:
        market_string = (
            f"Current Index Price: {market_data['current_price']}\n"
            f"Today's Return: {market_data['today_return_pct']}%\n"
            f"Intraday High: {market_data['today_high']} | Low: {market_data['today_low']}\n"
            f"Intraday Range/Volatility: {market_data['today_range_pct']}%\n"
            f"5-Day Trend Return: {market_data['week_return_pct']}%"
        )
    else:
        market_string = "Market data unavailable (The US exchanges are currently closed for the weekend or a public holiday)."

    prompt = f"""
        The user is a student focused on high-performance execution. 
        Here is the live data pulled from their system environment:

        [LIVE REMINDERS]:
        {all_reminders}
        [FINANCIAL DATA - S&P 500]:
        {market_string}

        You must construct a daily afternoon briefing following these professional execution rules:
        1. Start the message EXACTLY with the words: "System Update: Afternoon Briefing compiled." 
        2. Analyze the [FINANCIAL DATA] objectively and concisely. State whether the broader market is trending positive or negative today, noting volatility. Keep it analytical, data-driven, and brief.
        3. Review the [LIVE REMINDERS] and propose a structured, strategic plan of action for the evening. Treat academic requirements and internship tasks as critical project milestones. Use clear, proactive language (e.g., "Prioritize," "Execute," "Allocate time for").
        4. The tone must be that of an elite Chief of Staff or a highly advanced AI system (like a professional JARVIS). It should be articulate, highly organized, motivating, and entirely focused on optimizing the user's workflow.
        5. Conclude the briefing with a single, professional closing thought regarding system optimization, continuous improvement, or disciplined execution. 
        """

    try:
     async with channel.typing():
        response = await ollama_client.chat(model='gemma2', messages=[
            {
                'role': 'system',
                'content': 'You are an elite, deeply disciplined personal assistant and Chief of Staff focused on technical and academic strategy.'
            },
            {
                'role': 'user',
                'content': prompt
            }
        ])

        ai_reply = response['message']['content']
        await channel.send(f"🌆 **AFTERNOON SYSTEM BRIEFING** 🌆\n\n{ai_reply}")
    except Exception as e:
        print(f"Afternoon folder briefing failed: {e}")


@bot.event
async def on_ready():
    print(f"Jarvis is online and logged in as {bot.user}")

    state = check_new_message()
    print("\nCHANNEL ROUTING:")
    print(CHANNEL_ROUTING)

    for channel_name, route in CHANNEL_ROUTING.items():
        print(f"\n--- Checking channel: {channel_name} ---")

        channel = discord.utils.get(
            bot.get_all_channels(),
            name=channel_name
        )

        print(f"Channel object: {channel}")

        if not channel:
            print("Channel not found!")
            continue

        if channel_name not in state:
            print(f"No saved state for {channel_name}")
            continue

        last_id = state[channel_name]
        print(f"Last processed ID: {last_id}")

        count = 0
        print(f"Fetching history after {last_id}")

        async for message in channel.history(
                limit=50,
                after=discord.Object(id=last_id)
        ):
            count += 1

            print(
                f"MISSED MESSAGE FOUND | "
                f"ID={message.id} | "
                f"CONTENT='{message.content}'"
            )


            if message.author == bot.user:
                continue

            target_folder = os.path.join(obsidian_vault_root, route["folder"])
            content = message.content
            filename = filename_strip(content, route["title_prefix"])
            filepath = os.path.join(target_folder, filename)

            note_title = filename.replace(".md", "")
            md_content = generate_md(content, note_title)

            try:
                os.makedirs(target_folder, exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as file:
                    file.write(md_content)
                    print(f"Saved file: {filepath}")
                await message.add_reaction("💾")


                state[channel_name] = message.id

            except Exception as e:
                print(f"Error catching up on file: {e}")



        print(f"Found {count} missed messages in {channel_name}")
        save_message_id(state)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.lower() == "clear":
        await clear_messages(message)
        return

    channel_name = message.channel.name.lower()
    if channel_name == "general":
        await general_list(message)
        return

    elif channel_name == "chat-with-jarvis":
        await ai_chat(message)
        return

    elif channel_name == "afternoon-updates" and message.author != bot.user:
        if message.content.lower()=="test":
            await message.channel.send("⏳ *Jarvis is compiling your afternoon review on demand...*")
            try:
                await afternoon_scheduled_message()
            except Exception as test_error:
                print(f"❌ [TEST] Manual execution failed: {test_error}")
                await message.channel.send(f"❌ Failed to run review: {test_error}")

    if channel_name not in CHANNEL_ROUTING:
        return

    route = CHANNEL_ROUTING[channel_name]
    target_folder = os.path.join(obsidian_vault_root, route["folder"])

    content = message.content
    filename = filename_strip(content, route["title_prefix"])
    filepath = os.path.join(target_folder, filename)
    note_title = filename.replace(".md", "")
    md_content = generate_md(content, note_title)

    try:
        os.makedirs(target_folder, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as file:
            file.write(md_content)

        await message.add_reaction("💾")

        state = check_new_message()
        state[channel_name] = message.id
        save_message_id(state)

    except Exception as e:
        print(f"Error: {e}")
        await message.channel.send(f"Error: {e}")

    await bot.process_commands(message)
bot.run("discord bot token")