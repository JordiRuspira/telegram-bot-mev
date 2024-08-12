import requests
import pandas as pd
import logging
import asyncio
import json
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Retrieve the values from environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Dictionary to store user settings
user_settings = {}

# Function to load settings from a file (to persist data across sessions)
def load_settings():
    try:
        with open('user_settings.json', 'r') as file:
            global user_settings
            user_settings = json.load(file)
    except FileNotFoundError:
        user_settings = {}

# Function to save settings to a file
def save_settings():
    with open('user_settings.json', 'w') as file:
        json.dump(user_settings, file)

# Function to start the bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    context.bot.send_message(chat_id=user_id, text="Welcome to the MEV notification bot! Do you want automatic notifications? Type /configure to start.")

# Function to handle configuration
async def configure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if user_id not in user_settings:
        user_settings[user_id] = {'notifications': False, 'interval': 1, 'threshold': 300}
    context.bot.send_message(chat_id=user_id, text="Do you want automatic notifications? (Y/N)")

# Function to handle text messages
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    message = update.message.text.strip().lower()

    if user_id not in user_settings:
        context.bot.send_message(chat_id=user_id, text="Please type /configure to start configuration.")
        return

    if message in ['y', 'yes']:
        user_settings[user_id]['notifications'] = True
        context.bot.send_message(chat_id=user_id, text="Great! How often do you want notifications? (in hours)")
    elif message in ['n', 'no']:
        user_settings[user_id]['notifications'] = False
        context.bot.send_message(chat_id=user_id, text="Notifications disabled. Type /configure to start again.")
    elif user_settings[user_id]['notifications'] and user_settings[user_id]['interval'] == 1:
        try:
            interval = int(message)
            user_settings[user_id]['interval'] = interval
            context.bot.send_message(chat_id=user_id, text="What threshold of MEV capture (in $) should trigger a notification?")
        except ValueError:
            context.bot.send_message(chat_id=user_id, text="Please enter a valid number for hours.")
    elif user_settings[user_id]['notifications'] and user_settings[user_id]['threshold'] == 300:
        try:
            threshold = float(message)
            user_settings[user_id]['threshold'] = threshold
            context.bot.send_message(chat_id=user_id, text=f"Notifications set! You'll receive alerts every {user_settings[user_id]['interval']} hours for MEV values above ${user_settings[user_id]['threshold']}. Type /stop to stop notifications.")
            save_settings()
        except ValueError:
            context.bot.send_message(chat_id=user_id, text="Please enter a valid number for threshold.")

# Function to stop notifications
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if user_id in user_settings:
        user_settings[user_id]['notifications'] = False
        save_settings()
        context.bot.send_message(chat_id=user_id, text="Notifications stopped.")

# Function to get MEV data
def get_mev_data(initial_block_height, final_block_height):
    mev_api_url = f"https://dydx.observatory.zone/api/v1/raw_mev?limit=500000&from_height={initial_block_height}&to_height={final_block_height}&with_block_info=True"
    mev_response = requests.get(mev_api_url)
    mev_data = mev_response.json()
    mev_datapoints = mev_data.get('datapoints', [])
    return pd.DataFrame(mev_datapoints)

# Function to get validator data
def get_validator_data():
    validator_api_url = "https://dydx.observatory.zone/api/v1/validator"
    validator_response = requests.get(validator_api_url)
    validator_data = validator_response.json()
    return pd.DataFrame(validator_data.get('validators', []))

# Function to process and filter MEV data
def process_data(mev_df, validator_df, threshold):
    mev_df['value'] = mev_df['value'].astype(float)
    mev_df['height'] = mev_df['height'].astype(int)
    mev_df['MEV value ($)'] = mev_df['value'] / 10**6
    merged_df = pd.merge(mev_df, validator_df, left_on='proposer', right_on='pubkey', how='left')
    return merged_df[merged_df['MEV value ($)'] > threshold]

# Function to send Telegram message
async def send_telegram_message(bot, user_id, message):
    await bot.send_message(chat_id=user_id, text=message, parse_mode='HTML')

# Function to check MEV values and send notifications
async def check_mev_values():
    while True:
        for user_id, settings in user_settings.items():
            if settings['notifications']:
                # Fetch the latest block range
                try:
                    block_range_response = requests.get("https://dydx.observatory.zone/api/v1/block_range")
                    block_range_response.raise_for_status()
                    block_range = block_range_response.json()
                    final_block_height = int(block_range['lastHeight'])
                except (requests.RequestException, ValueError) as e:
                    continue

                initial_block_height = final_block_height - 50000
                mev_df = get_mev_data(initial_block_height, final_block_height)
                validator_df = get_validator_data()

                if mev_df.empty:
                    continue

                filtered_df = process_data(mev_df, validator_df, settings['threshold'])

                if not filtered_df.empty:
                    message = f"Blocks with MEV value higher than ${settings['threshold']}:\n"
                    for _, row in filtered_df.iterrows():
                        message += f"Block Height: {row['height']}, MEV Value: ${row['MEV value ($)']:.2f}, Proposer: {row['moniker']}\n"
                    bot = Bot(token=TELEGRAM_TOKEN)
                    await send_telegram_message(bot, user_id, message)
        
        await asyncio.sleep(3600 * settings['interval'])  # Sleep based on the user-defined interval

# Main function to run the bot
async def main():
    load_settings()
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    start_handler = CommandHandler('start', start)
    configure_handler = CommandHandler('configure', configure)
    stop_handler = CommandHandler('stop', stop)
    text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)

    application.add_handler(start_handler)
    application.add_handler(configure_handler)
    application.add_handler(stop_handler)
    application.add_handler(text_handler)

    # Start the MEV checking loop
    asyncio.create_task(check_mev_values())

    await application.start()
    await application.idle()

if __name__ == "__main__":
    asyncio.run(main())
