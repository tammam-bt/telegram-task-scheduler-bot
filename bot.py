from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import logging
from DB import save_user_message, get_user_history, get_all_user_history, clear_user_history
from Handlers.Calendar_API import authenticate_user, create_event_dict, create_event, list_events, delete_event, update_event
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
        await update.message.reply_text(response, parse_mode='Markdown')
        
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
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    print(datetime.now().isoformat())
    now = datetime.now().isoformat()
    system_message = f"""# Google Calendar API Agent System Prompt

You are a specialized AI agent that converts unstructured user input into structured output for Google Calendar API operations. Your primary function is to parse user requests and extract calendar event information in specific formats based on the action type.

## Output Formats by Action Type

### For CREATE and UPDATE Actions

```
Action: create/update
Summary: [event title/summary]
Location: [event location or "N/A" if not specified]
Description: [event description or "N/A" if not specified]
Start Time: [ISO 8601 format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS-HH:MM with timezone]
End Time: [ISO 8601 format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS-HH:MM with timezone]
Reminders: [minutes before event, e.g., "15" for 15 minutes, or "N/A" if not specified]
```

### For LIST Actions

```
Action: list
Location: [location filter or "N/A" if not specified]
Start Time: [ISO 8601 format or "N/A" if not specified]
End Time: [ISO 8601 format or "N/A" if not specified]
```

**Note**: For list actions, if you cannot find Location, Start Time, or End Time, simply output: `Action: list`

### For DELETE Actions

```
Action: delete
Summary: [event title/summary to delete]
Start Time: [ISO 8601 format or "N/A" if not specified]
End Time: [ISO 8601 format or "N/A" if not specified]
```

## Critical Rules

1. **Error Handling**: 
   - For CREATE/UPDATE: If you cannot determine the Action or Summary, output exactly: `Error`
   - For DELETE: If you cannot determine the Summary, output exactly: `Error`, the start time and end time are not required.
   - For LIST: If you cannot find any of the optional fields, just output: `Action: list`

2. **Action Types**: Only use these four actions:
   - `create` - for creating new events
   - `update` - for modifying existing events
   - `list` - for listing/viewing existing events
   - `delete` - for removing events

3. **Time Format**: Always use ISO 8601 format for dates and times:
   - With timezone: `{now}`
   - UTC format: `{now}Z`
   - If no time specified, assume reasonable defaults (e.g., 9 AM for start time)

4. **Default Values for CREATE**: 
   - If location not specified: use "N/A"
   - If description not specified: use "N/A"
   - If reminders not specified: use "N/A"
   - If end time not specified: assume 1 hour duration from start time   

5. **Date/Time Intelligence**:
   - Parse natural language dates (e.g., "tomorrow", "next Friday", "in 2 hours")
   - Assume current year if not specified
   - Use reasonable time defaults for business hours if not specified
6. **No additional text**: Do not add explanations or additional text outside the structured output format. Only return the structured output as specified.
7. **Default Values for DELETE**:
   - If summary not specified: use "N/A"
   - If start time not specified: use "N/A", do not assume a default time
   - If end time not specified: use "N/A", do not assume a default time
8. **Default Values for UPDATE**:
   - If location not specified: use "N/A"
   - If description not specified: use "N/A"
   - If reminders not specified: use "N/A"
   - If start time not specified: use "N/A", do not assume a default time
   - If end time not specified: use "N/A", do not assume a default time

## Examples

**CREATE Example**:
Input: "Schedule a meeting with John tomorrow at 2 PM for 1 hour at the office"
Output:
```
Action: create
Summary: Meeting with John
Location: The office
Description: N/A
Start Time: 2025-08-14T14:00:00Z
End Time: 2025-08-14T15:00:00Z
Reminders: N/A
```

**LIST Examples**:
Input: "Show me my events for next week"
Output:
```
Action: list
Location: N/A
Start Time: 2025-08-18T00:00:00Z
End Time: 2025-08-24T23:59:59Z
```

Input: "List my calendar"
Output:
```
Action: list
```

**UPDATE Example**:
Input: "Update my dentist appointment to 3 PM and add reminder 30 minutes before"
Output:
```
Action: update
Summary: Dentist appointment
Location: N/A
Description: N/A
Start Time: 2025-08-13T15:00:00Z
End Time: 2025-08-13T16:00:00Z
Reminders: 30
```

**DELETE Examples**:
Input: "Delete my meeting with Sarah tomorrow"
Output:
```
Action: delete
Summary: Meeting with Sarah
Start Time: 2025-08-14T00:00:00Z
End Time: 2025-08-14T23:59:59Z
```

Input: "Remove the dentist appointment"
Output:
```
Action: delete
Summary: Dentist appointment
Start Time: N/A
End Time: N/A
```

**ERROR Examples**:
Input: "I want to do something"
Output:
```
Error
```

Input: "Delete something" (no identifiable summary)
Output:
```
Error
```

## Important Notes

- Always keep in mind the user's timezone and current time which is {now} when parsing dates
- Always maintain the exact format structure for each action type
- Do not add explanations or additional text outside the structure
- Be conservative with assumptions - use "N/A" when information is unclear
- For list actions, only include fields that can be determined from the input
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
        reply_text = f"Event created with details:\n"
        for key, value in event_dict.items():
            reply_text += f"{key}: {value}\n"
        await update.message.reply_text(reply_text)
    elif re.search(r'Action: list', response) is not None and service:
        print("----"*50)
        print("Listing events...")
        print("----"*50)
        # Here you would typically call a function to list the events
        for line in response.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                if key.strip() == 'Start Time':
                    time_min = value.strip().strip('"')
                elif key.strip() == 'End Time':
                    time_max = value.strip().strip('"')
                elif key.strip() == 'Location':
                    location = value.strip().strip('"')    
                else:
                    location = None
                    time_min = None
                    time_max = None
        print(f"Listing events with location: {location}, time_min: {time_min}, time_max: {time_max}")   
        events = list_events(service, max_results=9999, time_min=time_min, time_max=time_max)  
        for i,event in enumerate(events):
            print(event)
            print("----"*50)
            await update.message.reply_text(f"""Event {i+1}:
Summary: {event['summary']}{"\nLocation: " + event['location'] if event['location'] else ''}{"\nDescription: " + event['description'] if event['description'] else ''}
Start Time: {event['start']}{"\nEnd Time: " + event['end'] if event['end'] else ''}""")
    elif re.search(r'Action: update', response) is not None and service:
        print("----"*50)
        print("Updating event...")
        print("----"*50)
        # Convert the event string to a dictionary
        event_dict = {}
        for line in response.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                event_dict[key.strip()] = value.strip().strip('"')
        # Here you would typically call a function to update the event in the calendar
        event_dict = {
            'summary': event_dict.get('Summary', ''),
            'location': event_dict.get('Location', ''),
            'description': event_dict.get('Description', ''),
            'start': {'dateTime': event_dict.get('Start Time', '')},
            'end': {'dateTime': event_dict.get('End Time', '')},
        }
        if event_dict.get('start').get('dateTime') in ["N/A",""]:
            event_dict.pop('start')
            print("Start time is N/A, removing from event dictionary.")
        if event_dict.get('end').get('dateTime') in ["N/A",""]:
            event_dict.pop('end')
            print("End time is N/A, removing from event dictionary.")
        if event_dict.get('location') in ["N/A",""]:
            event_dict.pop('location')
            print("Location is N/A, setting to empty string.")  
        if event_dict.get('description') in ["N/A",""]:
            event_dict.pop('description')
            print("Description is N/A, setting to empty string.")            
        print("The event dictionary to update:")
        print(event_dict)
        events = list_events(service, max_results=9999, time_max=event_dict.get('end').get('dateTime') if event_dict.get('end') else None, time_min=event_dict.get('start').get('dateTime') if event_dict.get('start') else None)
        Found_event = False
        for event in events:
            if event['summary'] == event_dict.get('summary', ''):
                print(f"Found event to update: {event}")
                event_dict['id'] = event['id']
                Found_event = True
                break
        if not Found_event:
            await update.message.reply_text("No event found to update with the provided summary.")
            return 
        # Call the function to update the event in Google Calendar
        update_event(service=service, event_id=event_dict.get('id'), updated_event=event_dict)
        reply_text = f"Event updated with details:\n"
        for key, value in event_dict.items():
            reply_text += f"{key}: {value}\n"
        await update.message.reply_text(reply_text)
    elif re.search(r'Action: delete', response) is not None and service:
        print("----"*50)
        print("Deleting event...")
        print("----"*50)
        # Convert the event string to a dictionary
        event_dict = {}
        for line in response.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                event_dict[key.strip()] = value.strip().strip('"')
        # Here you would typically call a function to delete the event in the calendar
        event_dict = {
            'summary': event_dict.get('Summary', ''),
            'start': {'dateTime': event_dict.get('Start Time', '')},
            'end': {'dateTime': event_dict.get('End Time', '')},
        }
        print(event_dict)
        print(event_dict.get('end').get('dateTime') if event_dict.get('end').get('dateTime') not in ["", "N/A"] else None)
        print(event_dict.get('start').get('dateTime') if event_dict.get('start').get('dateTime') not in ["", "N/A"] else None)
        events = list_events(service, max_results=9999, time_max=event_dict.get('end').get('dateTime') if event_dict.get('end').get('dateTime') not in ["", "N/A"] else None, time_min=event_dict.get('start').get('dateTime') if event_dict.get('start').get('dateTime') not in ["", "N/A"] else None)
        print(events)
        Found_event = False
        for event in events:
            if event['summary'] == event_dict.get('summary', ''):
                print(f"Found event to delete: {event}")
                delete_event(service=service, event_id=event['id'])
                Found_event = True
                break
        if not Found_event:
            await update.message.reply_text("No event found to delete with the provided summary.")
            return 
        reply_text = f"Event deleted with details:\n"
        for key, value in event_dict.items():
            reply_text += f"{key}: {value}\n"
        await update.message.reply_text(reply_text)    

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    welcome_text = ("""🎉 *Welcome to Calendar Assistant Bot!*

Hi there! I'm your intelligent calendar assistant, ready to make managing your schedule effortless.

*🚀 Let's get you set up:*

1️⃣ **Connect your Google Calendar** - Use `/connect` to link your Google account

2️⃣ **Start managing your calendar** - Just send me natural messages like:
   • "Schedule a meeting with Alex tomorrow at 2pm"
   • "What's on my calendar for Friday?"
   • "Cancel my dentist appointment"

*✨ What makes me special:*
• No complicated commands to remember
• Just talk to me naturally
• I understand what you want and handle the rest
• Full Google Calendar integration

*Ready to begin?*
Type `/connect` to link your Google account, or `/help` for more details.

Let's make your calendar management smart and simple! 📅"""
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

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
    help_text = (f"""🤖 *Calendar Assistant Bot - Help*

Welcome! I'm your intelligent calendar assistant. Here's what I can do for you:

*📋 Available Commands:*

🚀 `/start` - Initialize the bot and get started

🔗 `/connect` - Connect your Google account to manage your calendar events

🤖 `/AI` - Ask me any questions or get assistance with anything

❓ `/help` - Show this help message

*✨ Smart Calendar Management:*

Simply send me a natural message containing what you want to do with your calendar! I understand these actions:

• **Create** events: "Create a meeting with John tomorrow at 3pm"
• **List** events: "Show me my events for next week"  
• **Update** events: "Move my dentist appointment to Friday"
• **Delete** events: "Cancel the team meeting on Monday"

*💡 How it works:*
1. Connect your Google account using `/connect`
2. Send me any message describing what you want to do
3. I'll automatically interpret your request and manage your calendar
4. No need for special formatting - just write naturally!

*Examples:*
• "Schedule lunch with Sarah at noon on Thursday"
• "What do I have planned for tomorrow?"
• "Reschedule my 2pm meeting to 4pm"
• "Remove the workshop from my calendar"

Need help with anything else? Just use `/AI` followed by your question!

---
*Made with ❤️ to make calendar management effortless*"""
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

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
