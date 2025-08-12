from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os
from DB import save_created_event, get_created_events, delete_event, update_event, delete_event
import googleapiclient.discovery_cache
googleapiclient.discovery_cache.DISABLE_FILE_CACHE = True


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
            creds = flow.run_local_server(port=8080, access_type='offline')
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
        # Save the created event to the database
        save_created_event(
            user_id=user_id,
            title=event['summary'],
            start_time=event['start']['dateTime'],
            end_time=event['end']['dateTime'],
            description=event.get('description'),
            location=event.get('location')
        )
        print(f"Event created: {created_event.get('htmlLink')}")
        return created_event
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    
def list_events(service, max_results=10):
    """List the next n events from the user's primary calendar."""
    try:
        events_result = service.events().list(calendarId='primary', maxResults=max_results, singleEvents=True,
                                             orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events:
            print('No upcoming events found.')
        return events
    except Exception as e:
        print(f"An error occurred: {e}")
        return []
    
def delete_event(service, event_id):
    '''Delete an event from the user's primary calendar.'''
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        # Also delete the event from the database
        delete_event(event_id)
        print(f"Event {event_id} deleted.")
    except Exception as e:
        print(f"An error occurred: {e}")    
        
def update_event(service, event_id, updated_event):
    '''Update an existing event in the user's primary calendar.'''
    try:
        updated_event = service.events().update(calendarId='primary', eventId=event_id, body=updated_event).execute()
        # Also update the event in the database
        update_event(
            event_id=event_id,
            title=updated_event.get('summary'),
            start_time=updated_event['start']['dateTime'],
            end_time=updated_event['end']['dateTime'],
            description=updated_event.get('description'),
            location=updated_event.get('location')
        )
        print(f"Event updated: {updated_event.get('htmlLink')}")
        return updated_event
    except Exception as e:
        print(f"An error occurred: {e}")
        return None        