from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import logging
from DB import save_user_message, get_user_history, get_all_user_history, clear_user_history
from Handlers.Calendar_API import authenticate_user, create_event_dict, create_event
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from functools import partial
import re
import googleapiclient.discovery_cache
from datetime import datetime, timedelta
googleapiclient.discovery_cache.DISABLE_FILE_CACHE = True

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global OpenAI client
client = None

# Global google calendar service
service = None

async def chat_with_gpt(prompt, user_id, client, user_history=None, system_message=None):
    """Function to interact with OpenAI API"""
    if system_message:
        messages = [{"role": "system", "content": system_message}]  
    else:
        messages = []    
    if user_history:
        messages += user_history
    else:
        logger.info(f"No user history provided for user {user_id}. Using empty history.")
    # Add the user prompt to the messages    
    messages.append({"role": "user", "content": prompt})
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

async def handle_AI(update: Update, context: ContextTypes.DEFAULT_TYPE, client=None):
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
        response = await chat_with_gpt(user_message, user_id, client=client, user_history=get_user_history(user_id))
        
        # Send the response
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text(
            "Sorry, I encountered an error while processing your message. Please try again."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, client=None, service=None):
    """Handle incoming messages"""
    if client is None:
        logger.error("OpenAI client is not initialized.")
        await update.message.reply_text("Error: OpenAI client is not available.")
        return
    # Extract information from the user message
    user_id = update.effective_user.id
    user_message = update.message.text
    print(datetime.now().isoformat())
    now = datetime.now().isoformat()
    tomorrow_2pm = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=0).isoformat()
    tomorrow_3pm = (datetime.now() + timedelta(days=1)).replace(hour=15, minute=0).isoformat()
    this_week_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    this_week_end = (datetime.now() + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat()
    today_3pm = datetime.now().replace(hour=15, minute=0).isoformat()
    today_4pm = datetime.now().replace(hour=16, minute=0).isoformat()
    system_message = f"""You are a specialized AI agent that converts unstructured user input into structured output for Google Calendar API operations. Your primary function is to parse user requests and extract calendar event information in a specific format.

##Today's date is the following: {now}

## Output Format

You must ALWAYS respond with the following exact structure:

```
Action: create/list/update
Summary: [event title/summary]
Location: [event location or "N/A" if not specified]
Description: [event description or "N/A" if not specified]
Start Time: [ISO 8601 format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS-HH:MM with timezone]
End Time: [ISO 8601 format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS-HH:MM with timezone]
Reminders: [minutes before event, e.g., "15" for 15 minutes, or "N/A" if not specified]
```

## Critical Rules

1. **Error Handling**: If you cannot determine the Action or Summary from the user input, output exactly: `Error`

2. **Summary Handling**:
    - Always extract a clear event title or summary from the user input.
    - Try to reduce the summary to the least number of words while retaining the essence of the event and try to reformulate all the rest of the user input into a description.

3. **Action Types**: Only use these three actions:
   - `create` - for creating new events
   - `list` - for listing/viewing existing events
   - `update` - for modifying existing events

4. **Time Format**: Always use ISO 8601 format for dates and times:
   - With timezone: `{now}`
   - UTC format: `{now}Z`
   - If no time specified, assume reasonable defaults ({now})

5. **Default Values**: 
   - If location not specified: use "N/A"
   - If description not specified: use "N/A"
   - If reminders not specified: use "N/A"
   - If end time not specified: assume 1 hour duration from start time

6. **Date/Time Intelligence**:
   - The current date and time is {now}Z
   - Parse natural language dates (e.g., "tomorrow", "next Friday", "in 2 hours")
   - Assume current year if not specified
   - Use reasonable time defaults for business hours if not specified
   
7. **Description Handling**:
   - If the user does not specify a description, use "N/A"
   - Parse the user input for any description-related keywords or phrases. (e.g., "so that ...", "to do something", etc.)
8. **Location Handling**:
   - If the user does not specify a location, use "N/A"
   - Parse the user input for any location-related keywords or phrases.

## Examples

**Input**: "Schedule a meeting with John tomorrow at 2 PM for 1 hour at the office to discuss project updates"
**Output**:
```
Action: create
Summary: Meeting with John
Location: The office
Description: Discuss project updates
Start Time: {tomorrow_2pm}Z
End Time: {tomorrow_3pm}Z
Reminders: N/A
```

**Input**: "Show me my events for next week"
**Output**:
```
Action: list
Summary: Events for next week
Location: N/A
Description: N/A
Start Time: {this_week_start}Z
End Time: {this_week_end}Z
Reminders: N/A
```

**Input**: "Update my dentist appointment to 3 PM and add reminder 30 minutes before"
**Output**:
```
Action: update
Summary: Dentist appointment
Location: N/A
Description: N/A
Start Time: {today_3pm}Z
End Time: {today_4pm}Z
Reminders: 30
```

**Input**: "I want to do something"
**Output**:
```
Error
```

## Important Notes

- Always maintain the exact format structure
- Do not add explanations or additional text outside the structure
- Be conservative with assumptions - use "N/A" when information is unclear
- For list actions, use date ranges that make sense for the request
- Ensure all times are in valid ISO 8601 format that Google Calendar API accepts"""
    response = await chat_with_gpt(user_message, update.effective_user.id, client=client, user_history= get_user_history(user_id), system_message=system_message)
    response = response.strip().split("```")[1] if "```" in response else response.strip()
    await update.message.reply_text(response)
    print(re.search(r'Action: create', response))
    service = authenticate_user(user_id)
    # Check if the response indicates a create action
    if re.search(r'Action: create', response) is not None and service:
        print("----"*50)
        print("Creating event...")
        print("----"*50)
        # Convert the event string to a dictionary
        event_dict = {}
        for line in response.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                event_dict[key.strip()] = value.strip().strip('"')
        # Here you would typically call a function to create the event in the calendar
        event_dict = {
            'summary': event_dict.get('Summary', ''),
            'location': event_dict.get('Location', ''),
            'description': event_dict.get('Description', ''),
            'start': {'dateTime': event_dict.get('Start Time', '')},
            'end': {'dateTime': event_dict.get('End Time', '')},
        }
        print(event_dict)
        # Call the function to create the event in Google Calendar
        create_event(service=service, event=event_dict, user_id=user_id)
        await update.message.reply_text(f"Event created with details: {event_dict}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    welcome_text = (
        "🤖 Hello! I'm your AI assistant bot.\n\n"
        "I can help you with various questions and tasks. "
        "Just send me a message and I'll respond using AI!\n\n"
        "Use /help to see available commands."
    )
    await update.message.reply_text(welcome_text)

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /connect command to authenticate user with Google Calendar"""
    user_id = update.effective_user.id
    try:
        service = authenticate_user(user_id)
        if service:
            await update.message.reply_text("✅ You have been successfully authenticated with Google Calendar! You can now create, update, and delete events.")
        else:
            await update.message.reply_text("❌ Authentication failed. Please try again.")
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        await update.message.reply_text("❌ An error occurred during authentication. Please try again later.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    help_text = (
        "📋 Available Commands:\n\n"
        "/start - Start the bot and see welcome message\n"
        "/help - Show this help message\n\n"
        "/clear - Clear your message history\n\n"
        "/connect - Connect your Google Calendar account\n\n"
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
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="An error occurred while processing your request. Please try again later."
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

    # Pass the client to the message handler using a lambda or partial
    message_handler_with_client_and_service = partial(handle_message, client=client, service=service)
    ai_handler_with_client = partial(handle_AI, client=client)
    
    # Create the Application
    application = Application.builder().token(telegram_bot_token).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("connect", connect_command))
    

    # Add message handler for text messages
    application.add_handler(MessageHandler(filters.Regex(r'^/AI\s+.*'), ai_handler_with_client))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler_with_client_and_service))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Initialize the bot
    application.bot.initialize()

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
