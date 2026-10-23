from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os
from datetime import datetime
from zoneinfo import ZoneInfo
# Imported under another name: this module defines its own delete_event(service, event_id),
# which would otherwise shadow the DB function and call itself.
from DB import save_created_event, get_created_events, update_event_in_db, delete_event as delete_event_from_db
import googleapiclient.discovery_cache
googleapiclient.discovery_cache.DISABLE_FILE_CACHE = True


def get_calendar_timezone(service):
    """Return the user's Google Calendar timezone (IANA name), or UTC if it can't be read."""
    try:
        return service.settings().get(setting='timezone').execute()['value']
    except Exception as e:
        print(f"Could not read calendar timezone, using UTC: {e}")
        return 'UTC'


def _wall_clock(value):
    """Parse an ISO 8601 string from the model, keeping only the local date and time.

    The model is asked for the time exactly as the user said it. Any offset or 'Z' it
    adds anyway is dropped, so "3 PM" stays 3 PM in the user's own timezone."""
    if not value or value.strip().upper() in ('N/A', 'NA', 'NONE'):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def to_event_time(value, tz):
    """Model time string -> Calendar API start/end field, or None if absent."""
    local = _wall_clock(value)
    if local is None:
        return None
    return {'dateTime': local.isoformat(timespec='seconds'), 'timeZone': tz}


def to_query_bound(value, tz):
    """Model time string -> RFC 3339 timestamp with offset (required by events.list), or None."""
    local = _wall_clock(value)
    if local is None:
        return None
    return local.replace(tzinfo=ZoneInfo(tz)).isoformat()


def find_event(service, summary, tz, time_min=None, time_max=None):
    """Find the user's event whose title matches `summary`, ignoring case.

    Searches by name rather than by time, so an event can be found when the user only
    says what it is ("move my dentist appointment to Friday"). Without a window it looks
    from the start of today onwards. Exact title matches win over partial ones."""
    if not summary or summary.strip().upper() == 'N/A':
        return None
    wanted = summary.strip().lower()
    if time_min is None:
        today = datetime.now(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_min = today.isoformat()
    try:
        items = service.events().list(calendarId='primary', q=summary.strip(), singleEvents=True,
                                      orderBy='startTime', timeMin=time_min, timeMax=time_max,
                                      maxResults=50).execute().get('items', [])
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    titles = [(event, event.get('summary', '').strip().lower()) for event in items]
    exact = [event for event, title in titles if title == wanted]
    partial = [event for event, title in titles if wanted in title or (title and title in wanted)]
    matches = exact or partial
    return matches[0] if matches else None


# Function to authenticate user with Google Calendar API
# This function will create a token into a JSON file for the user if it doesn't exist
# This function returns the service object to interact with Google Calendar API
def authenticate_user(user_id: int):
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    token_path = f'tokens/{user_id}.json'
    credentials_path = 'credentials.json'
    # Create tokens directory if it doesn't exist
    os.makedirs('tokens', exist_ok=True)
    creds = None
    # Check if the token file exists
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    # If there are no valid credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())   
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES)
            flow.redirect_uri = 'http://localhost:8080/'
            creds = flow.run_local_server(port=8080, access_type='offline', prompt='consent')
            with open(token_path, 'w') as token_file:
                token_file.write(creds.to_json())
    service = build('calendar', 'v3', credentials=creds)
    return service

def create_event_dict(title, start_time, end_time, description=None, location=None):
    """Create a dictionary for a calendar event."""
    event = {
        'summary': title,
        'location': location if location else '',
        'description': description if description else '',
        'start': {
            'dateTime': start_time,
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_time,
            'timeZone': 'UTC',
        },
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                {'method': 'popup', 'minutes': 10},       # 10 minutes before
            ],
        },
    }
    return event

def create_event(service, event, user_id):
    """Create a new event in the user's primary calendar."""
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    # Save the created event to the database; a failure here must not hide that Google has it
    try:
        save_created_event(
            user_id=user_id,
            title=event['summary'],
            start_time=event['start']['dateTime'],
            end_time=event['end']['dateTime'],
            description=event.get('description'),
            location=event.get('location')
        )
    except Exception as e:
        print(f"Event created but not saved locally: {e}")
    print(f"Event created: {created_event.get('htmlLink')}")
    return created_event
    
def list_events(service, max_results=10, time_min=None, time_max=None):
    """List the next n events from the user's primary calendar, in start-time order."""
    try:
        events_result = service.events().list(calendarId='primary', maxResults=max_results, singleEvents=True, orderBy='startTime', timeMin=time_min, timeMax=time_max).execute()
        events = events_result.get('items', [])
        if not events:
            print('No upcoming events found.')
        events = [{'id': event['id'],
                   'summary': event.get('summary', 'No Title'),
                   'description': event.get('description', ''),
                   'location': event.get('location', ''),
                   'start': event['start'].get('dateTime', event['start'].get('date')),
                   'end': event['end'].get('dateTime', event['end'].get('date'))} for event in events]    
        return events
    except Exception as e:
        print(f"An error occurred: {e}")
        return []
    
def delete_event(service, event_id):
    '''Delete an event from the user's primary calendar. Returns True only if Google confirmed it.'''
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    # Also delete the event from the database
    delete_event_from_db(event_id)
    print(f"Event {event_id} deleted.")
    return True
        
def update_event(service, event_id, updated_event):
    '''Update an existing event in the user's primary calendar.'''
    try:
        updated_event = service.events().patch(calendarId='primary', eventId=event_id, body=updated_event).execute()
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    # Also update the event in the database; a failure here must not hide that Google has it
    try:
        update_event_in_db(
            event_id=event_id,
            title=updated_event.get('summary'),
            start_time=updated_event['start'].get('dateTime'),
            end_time=updated_event['end'].get('dateTime'),
            description=updated_event.get('description'),
            location=updated_event.get('location')
        )
    except Exception as e:
        print(f"Event updated but not saved locally: {e}")
    print(f"Event updated: {updated_event.get('htmlLink')}")
    return updated_event