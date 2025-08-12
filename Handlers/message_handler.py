from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from DB import save_user_message, get_user_history, get_all_user_history, clear_user_history, save_created_event, delete_event, update_event
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from Handlers.Calendar_API import authenticate_user, create_event_dict, create_event
import logging
import re

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def chat_with_gpt(prompt, user_id, client, user_history=None, system_message=None):
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
        response = chat_with_gpt(user_message, user_id, client=client, user_history=get_user_history(user_id))
        
        # Send the response
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text(
            "Sorry, I encountered an error while processing your message. Please try again."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, client=None):
    """Handle incoming messages"""
    if client is None:
        logger.error("OpenAI client is not initialized.")
        await update.message.reply_text("Error: OpenAI client is not available.")
        return
    # Extract information from the user message
    user_id = update.effective_user.id
    user_message = update.message.text
    system_message = """You are a specialized AI agent that converts unstructured user input into structured output for Google Calendar API operations. Your primary function is to parse user requests and extract calendar event information in a specific format.

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

2. **Action Types**: Only use these three actions:
   - `create` - for creating new events
   - `list` - for listing/viewing existing events
   - `update` - for modifying existing events

3. **Time Format**: Always use ISO 8601 format for dates and times:
   - With timezone: `2024-03-15T14:30:00-05:00`
   - UTC format: `2024-03-15T14:30:00Z`
   - If no time specified, assume reasonable defaults (e.g., 9 AM for start time)

4. **Default Values**: 
   - If location not specified: use "N/A"
   - If description not specified: use "N/A"
   - If reminders not specified: use "N/A"
   - If end time not specified: assume 1 hour duration from start time

5. **Date/Time Intelligence**:
   - Parse natural language dates (e.g., "tomorrow", "next Friday", "in 2 hours")
   - Assume current year if not specified
   - Use reasonable time defaults for business hours if not specified

## Examples

**Input**: "Schedule a meeting with John tomorrow at 2 PM for 1 hour at the office"
**Output**:
```
Action: create
Summary: Meeting with John
Location: The office
Description: N/A
Start Time: 2024-03-16T14:00:00Z
End Time: 2024-03-16T15:00:00Z
Reminders: N/A
```

**Input**: "Show me my events for next week"
**Output**:
```
Action: list
Summary: Events for next week
Location: N/A
Description: N/A
Start Time: 2024-03-18T00:00:00Z
End Time: 2024-03-24T23:59:59Z
Reminders: N/A
```

**Input**: "Update my dentist appointment to 3 PM and add reminder 30 minutes before"
**Output**:
```
Action: update
Summary: Dentist appointment
Location: N/A
Description: N/A
Start Time: 2024-03-15T15:00:00Z
End Time: 2024-03-15T16:00:00Z
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
    response = chat_with_gpt(user_message, update.effective_user.id, client=client, user_history= get_user_history(user_id), system_message=system_message)
    await update.message.reply_text(response)
    if re.match(r'^action\s*=\s*"create"', response):
        # Extract event details from the response
        event_details = re.search(r'event\s*=\s*{(.*?)}', response, re.DOTALL)
        if event_details:
            event_str = event_details.group(1).strip()
            # Convert the event string to a dictionary
            event_dict = {}
            for line in event_str.splitlines():
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
            create_event(service=authenticate_user(user_id), event=event_dict, user_id=user_id)
            # For example: create_event(event_dict)
            await update.message.reply_text(f"Event created with details: {event_dict}")