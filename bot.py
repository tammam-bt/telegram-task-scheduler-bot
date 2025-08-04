from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import logging
from DB import save_user_message, get_user_history, get_all_user_history, clear_user_history
from Handlers.commands_handlers import start_command, help_command, error_handler, clear_command
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global OpenAI client
client = None


def chat_with_gpt(prompt, user_id):
    """Function to interact with OpenAI API"""
    try:
        user_history = get_user_history(user_id)
        messages = user_history + [{"role": "user", "content": prompt}]
    except Exception as e:
        logger.error(f"Error retrieving user history: {e}")
        messages = [{"role": "user", "content": prompt}]  
    try:
        response = client.chat.completions.create(
            model="meta-llama/llama-3-8b-instruct",
            messages=messages,
        )
        # Save the user message to the database
        save_user_message(user_id, "user", prompt)
        print(f"User {user_id} message saved: {prompt}")
        # Save the AI response to the database
        save_user_message(user_id, "assistant", response.choices[0].message.content)
        print(f"User {user_id} response saved: {response.choices[0].message.content}")
        # Return the AI response
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return "Sorry, I'm having trouble processing your request right now."


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular text messages"""
    user_message = update.message.text
    user_name = update.effective_user.first_name
    user_id = update.effective_user.id
    
    # Log the incoming message
    logger.info(f"Message from {user_name}: {user_message}")
    
    # Send typing action to show bot is processing
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        # Get response from GPT
        response = chat_with_gpt(user_message, user_id)
        
        # Send the response
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text(
            "Sorry, I encountered an error while processing your message. Please try again."
        )


def main():
    """Main function to run the bot"""
    # Load environment variables
    load_dotenv()
    
    # Get API keys from environment variables
    openrouter_api_key = os.getenv("OpenRouter_API_KEY")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not openrouter_api_key:
        logger.error("OpenRouter_API_KEY not found in environment variables")
        return
    
    if not telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    # Initialize OpenAI client
    global client
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=openrouter_api_key
    )
    
    # Create the Application
    application = Application.builder().token(telegram_bot_token).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    
    # Add message handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Initialize the bot
    application.bot.initialize()

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
