import os
import pandas as pd
import requests
import logging
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants from .env
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Initialize logging
logging.basicConfig(level=logging.INFO)

# Initialize settings with default values
settings = {
    'auto_notify': 'N',
    'interval': 1,  # Default to 1 hour if not set
    'threshold': 300  # Default to $300 if not set
}

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
def process_data(mev_df, validator_df):
    mev_df['value'] = mev_df['value'].astype(float)
    mev_df['height'] = mev_df['height'].astype(int)
    mev_df['MEV value ($)'] = mev_df['value'] / 10**6
    merged_df = pd.merge(mev_df, validator_df, left_on='proposer', right_on='pubkey', how='left')
    return merged_df[merged_df['MEV value ($)'] > settings['threshold']]

# Function to send Telegram message
async def send_telegram_message(bot, message):
    await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.HTML)

# Function to handle the /start command
async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! I am your MEV bot. I will notify you about MEV values based on your settings."
    )

# Function to check MEV values
async def check_mev_values():
    logging.info("Starting MEV value check")
    try:
        block_range_response = requests.get("https://dydx.observatory.zone/api/v1/block_range")
        block_range_response.raise_for_status()
        block_range = block_range_response.json()
        final_block_height = int(block_range['lastHeight'])
        logging.info(f"Fetched block range: {block_range}")
    except (requests.RequestException, ValueError) as e:
        logging.error(f"Error fetching the block range: {e}")
        return

    initial_block_height = final_block_height - 50000
    mev_df = get_mev_data(initial_block_height, final_block_height)
    validator_df = get_validator_data()
    
    if mev_df.empty:
        logging.info("No MEV data found")
        return

    filtered_df = process_data(mev_df, validator_df)
    
    if filtered_df.empty:
        logging.info(f"No blocks with MEV value higher than ${settings['threshold']}")
        return

    message = f"Blocks with MEV value higher than ${settings['threshold']}:\n"
    for _, row in filtered_df.iterrows():
        message += f"Block Height: {row['height']}, MEV Value: ${row['MEV value ($)']:.2f}, Proposer: {row['moniker']}\n"

    bot = Bot(token=TELEGRAM_TOKEN)
    await send_telegram_message(bot, message)
    logging.info("Telegram message sent")

async def main():
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler('start', start))

    # Start the bot
    logging.info('Starting bot...')
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
