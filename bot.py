from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import logging
from DB import save_user_message, get_user_history, get_all_user_history, clear_user_history
from Handlers.Calendar_API import (authenticate_user, create_event_dict, create_event, list_events, delete_event,
                                   update_event, get_calendar_timezone, to_event_time, to_query_bound, find_event,
                                   AmbiguousEvent)
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from functools import partial
import re
import googleapiclient.discovery_cache
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
googleapiclient.discovery_cache.DISABLE_FILE_CACHE = True

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# httpx logs every request URL at INFO, and Telegram API URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Used when OPENROUTER_MODEL is not set.
DEFAULT_MODEL = "openai/gpt-4.1-mini"

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
            model=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
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

def ambiguity_message(verb, summary, candidates, tz):
    """Ask the user to narrow down which event they mean; nothing has been changed."""
    lines = []
    for event in candidates[:10]:
        when = event.get('start', {})
        raw = when.get('dateTime') or when.get('date') or ''
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo(tz))
            raw = parsed.strftime('%a %d %b, %H:%M') if 'dateTime' in when else parsed.strftime('%a %d %b')
        except ValueError:
            pass
        lines.append(f"- {event.get('summary', 'No Title')} ({raw})")
    return (f"I found {len(candidates)} events that could be \"{summary}\", so I did not {verb} anything:\n"
            + "\n".join(lines) + "\nTell me which one, for example by its full title or its date.")


def parse_fields(response):
    """Turn the model's 'Key: value' block into a dict."""
    fields = {}
    for line in response.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields

def is_given(value):
    return value not in (None, "", "N/A")

def describe(summary, start, end, location, tz):
    """One readable line for an event, in the user's timezone. start/end are ISO date or dateTime strings."""
    if 'T' in start:
        zone = ZoneInfo(tz)
        begin = datetime.fromisoformat(start.replace('Z', '+00:00'))
        finish = datetime.fromisoformat(end.replace('Z', '+00:00'))
        begin = begin.astimezone(zone) if begin.tzinfo else begin
        finish = finish.astimezone(zone) if finish.tzinfo else finish
        when = f"{begin:%a %d %b}, {begin:%H:%M}–{finish:%H:%M}"
    else:
        when = f"{datetime.fromisoformat(start):%a %d %b}, all day"
    line = f"{summary or 'No title'} — {when}"
    if location:
        line += f" @ {location}"
    return line

def describe_event(event, tz):
    """describe() for an event as returned by the Calendar API."""
    start = event['start'].get('dateTime', event['start'].get('date'))
    end = event['end'].get('dateTime', event['end'].get('date'))
    return describe(event.get('summary'), start, end, event.get('location'), tz)

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
    service = authenticate_user(user_id)
    # Times are interpreted in the user's own calendar timezone, not the server's
    tz = get_calendar_timezone(service) if service else "UTC"
    now = datetime.now(ZoneInfo(tz))
    today = now.date()
    tomorrow = today + timedelta(days=1)
    next_monday = today + timedelta(days=7 - today.weekday())
    system_message = f"""# Google Calendar API Agent System Prompt

You are a specialized AI agent that converts unstructured user input into structured output for Google Calendar API operations. Your primary function is to parse user requests and extract calendar event information in specific formats based on the action type.

## Current Date and Time

It is {now:%A, %d %B %Y, %H:%M} in the user's timezone ({tz}). Today is {now:%A}. Work out dates such as "tomorrow" or "on Friday" from this.

## Output Formats by Action Type

### For CREATE and UPDATE Actions

```
Action: create/update
Summary: [event title/summary]
Location: [event location or "N/A" if not specified]
Description: [event description or "N/A" if not specified]
Start Time: [local date and time: YYYY-MM-DDTHH:MM:SS]
End Time: [local date and time: YYYY-MM-DDTHH:MM:SS]
Reminders: [minutes before event, e.g., "15" for 15 minutes, or "N/A" if not specified]
```

### For LIST Actions

```
Action: list
Location: [location filter or "N/A" if not specified]
Start Time: [local date and time or "N/A" if not specified]
End Time: [local date and time or "N/A" if not specified]
```

**Note**: For list actions, if you cannot find Location, Start Time, or End Time, simply output: `Action: list`

### For DELETE Actions

```
Action: delete
Summary: [event title/summary to delete]
Start Time: [local date and time or "N/A" if not specified]
End Time: [local date and time or "N/A" if not specified]
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

3. **Time Format**: Give every time as the user's local wall-clock time in the form YYYY-MM-DDTHH:MM:SS.
   - Never add a UTC offset or a "Z", and never convert between timezones: "3 PM" is always T15:00:00
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
   - Summary is the name of the existing event to change
   - Start Time and End Time are the event's NEW times
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
Start Time: {tomorrow}T14:00:00
End Time: {tomorrow}T15:00:00
Reminders: N/A
```

**LIST Examples**:
Input: "Show me my events for next week"
Output:
```
Action: list
Location: N/A
Start Time: {next_monday}T00:00:00
End Time: {next_monday + timedelta(days=6)}T23:59:59
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
Start Time: {today}T15:00:00
End Time: {today}T16:00:00
Reminders: 30
```

**DELETE Examples**:
Input: "Delete my meeting with Sarah tomorrow"
Output:
```
Action: delete
Summary: Meeting with Sarah
Start Time: {tomorrow}T00:00:00
End Time: {tomorrow}T23:59:59
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

- Always keep in mind that it is currently {now:%A, %d %B %Y, %H:%M} for the user when parsing dates
- Always maintain the exact format structure for each action type
- Do not add explanations or additional text outside the structure
- Be conservative with assumptions - use "N/A" when information is unclear
- For list actions, only include fields that can be determined from the input
- Ensure all times are local wall-clock times in the YYYY-MM-DDTHH:MM:SS format"""
    response = await chat_with_gpt(user_message, update.effective_user.id, client=client, user_history= get_user_history(user_id), system_message=system_message)
    response = response.strip().split("```")[1] if "```" in response else response.strip()
    fields = parse_fields(response)
    action = fields.get('Action', '').strip().lower()
    if action not in ('create', 'list', 'update', 'delete'):
        # Not a calendar action: show the model's reply as it is
        await update.message.reply_text(response)
    if not service:
        await update.message.reply_text("I couldn't reach your Google Calendar. Use /connect to link it first.")
        return
    if action == 'create':
        logger.info("Creating event...")
        start = to_event_time(fields.get('Start Time'), tz)
        end = to_event_time(fields.get('End Time'), tz)
        if not is_given(fields.get('Summary')) or start is None:
            await update.message.reply_text("I couldn't tell what to create or when. Try: \"Lunch with Sarah on Friday at noon\".")
            return
        if end is None:
            end = {'dateTime': (datetime.fromisoformat(start['dateTime']) + timedelta(hours=1)).isoformat(), 'timeZone': tz}
        event_dict = {'summary': fields['Summary'], 'start': start, 'end': end}
        for key in ('Location', 'Description'):
            if is_given(fields.get(key)):
                event_dict[key.lower()] = fields[key]
        # Call the function to create the event in Google Calendar
        created = create_event(service=service, event=event_dict, user_id=user_id)
        if created is None:
            await update.message.reply_text(f"❌ I couldn't create \"{event_dict['summary']}\" in your calendar. Please try again.")
            return
        await update.message.reply_text(f"✅ Event created:\n{describe_event(created, tz)}")
    elif action == 'list':
        logger.info("Listing events...")
        time_min = to_query_bound(fields.get('Start Time'), tz)
        time_max = to_query_bound(fields.get('End Time'), tz)
        if time_min is None and time_max is None:
            time_min = now.isoformat()  # nothing specified: show what's coming up
        events = list_events(service, max_results=50, time_min=time_min, time_max=time_max)
        location = fields.get('Location')
        if is_given(location):
            events = [event for event in events if location.lower() in (event.get('location') or '').lower()]
        if not events:
            await update.message.reply_text("📭 No events found for that period.")
            return
        lines = [f"{i}. {describe(e['summary'], e['start'], e['end'], e['location'], tz)}" for i, e in enumerate(events, 1)]
        await update.message.reply_text("📅 Your events:\n" + "\n".join(lines))
    elif action == 'update':
        logger.info("Updating event...")
        summary = fields.get('Summary')
        # Find the event by its name, not by its new time: the event is still at its old time
        try:
            existing = find_event(service, summary, tz)
        except AmbiguousEvent as ambiguous:
            await update.message.reply_text(ambiguity_message("update", summary, ambiguous.candidates, tz))
            return
        if existing is None:
            await update.message.reply_text(f"No event found to update named \"{summary}\".")
            return
        changes = {}
        start = to_event_time(fields.get('Start Time'), tz)
        end = to_event_time(fields.get('End Time'), tz)
        if start and end is None and 'dateTime' in existing['start']:
            # Only a new start was given: keep the event's original length
            old_start = datetime.fromisoformat(existing['start']['dateTime'].replace('Z', '+00:00'))
            old_end = datetime.fromisoformat(existing['end']['dateTime'].replace('Z', '+00:00'))
            end = {'dateTime': (datetime.fromisoformat(start['dateTime']) + (old_end - old_start)).isoformat(), 'timeZone': tz}
        if start:
            changes['start'] = start
        if end:
            changes['end'] = end
        for key in ('Location', 'Description'):
            if is_given(fields.get(key)):
                changes[key.lower()] = fields[key]
        if not changes:
            await update.message.reply_text(f"I found \"{existing.get('summary')}\" but couldn't tell what to change.")
            return
        # Call the function to update the event in Google Calendar
        updated = update_event(service=service, event_id=existing['id'], updated_event=changes)
        if updated is None:
            await update.message.reply_text(f"❌ I couldn't update \"{existing.get('summary')}\". Your calendar is unchanged.")
            return
        await update.message.reply_text(f"✅ Event updated:\n{describe_event(updated, tz)}")
    elif action == 'delete':
        logger.info("Deleting event...")
        summary = fields.get('Summary')
        try:
            existing = find_event(service, summary, tz,
                                  time_min=to_query_bound(fields.get('Start Time'), tz),
                                  time_max=to_query_bound(fields.get('End Time'), tz))
        except AmbiguousEvent as ambiguous:
            await update.message.reply_text(ambiguity_message("delete", summary, ambiguous.candidates, tz))
            return
        if existing is None:
            await update.message.reply_text(f"No event found to delete named \"{summary}\".")
            return
        if not delete_event(service=service, event_id=existing['id']):
            await update.message.reply_text(f"❌ I couldn't delete \"{existing.get('summary')}\". It is still in your calendar.")
            return
        await update.message.reply_text(f"🗑️ Event deleted:\n{describe_event(existing, tz)}")
    else:
        await update.message.reply_text("I couldn't work out a calendar action from that. Try something like \"Move my dentist appointment to Friday at 3\".")

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
