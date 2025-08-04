from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import logging
from DB import clear_user_history

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    welcome_text = (
        "🤖 Hello! I'm your AI assistant bot.\n\n"
        "I can help you with various questions and tasks. "
        "Just send me a message and I'll respond using AI!\n\n"
        "Use /help to see available commands."
    )
    await update.message.reply_text(welcome_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    help_text = (
        "📋 Available Commands:\n\n"
        "/start - Start the bot and see welcome message\n"
        "/help - Show this help message\n\n"
        "/clear - Clear your message history\n\n"
        "💬 How to use:\n"
        "Simply send me any message and I'll respond using AI!\n\n"
        "✨ Examples:\n"
        "• Ask me questions\n"
        "• Request explanations\n"
        "• Get creative writing help\n"
        "• And much more!"
    )
    await update.message.reply_text(help_text)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /clear command to clear user history"""
    user_id = update.effective_user.id
    clear_user_history(user_id)
    await update.message.reply_text("🗑️ Your message history has been cleared.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="An error occurred while processing your request. Please try again later."
    )
