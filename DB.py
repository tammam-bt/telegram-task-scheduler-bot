import sqlite3
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Get database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------------------------------------------------------------------------------------------------------------------------
"""Create a table for user message history, created events history if it doesn't exist"""
conn = sqlite3.connect(DATABASE_URL)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_history (
        msg_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        role        TEXT NOT NULL,
        message     TEXT NOT NULL,
        timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS created_events (
        event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL,
        title      TEXT NOT NULL,
        start_time DATETIME NOT NULL,
        end_time   DATETIME NOT NULL,
        description TEXT,
        location   TEXT,
        timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()
conn.close()

# ---------------------------------------------------------------------------------------------------------------------------------
'''AI CHAT HISTORY TABLE'''
def save_user_message(user_id, role, message, timestamp=None):
    """Save a user message to the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO chat_history (user_id, role, message, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (user_id, role, message, timestamp))
    conn.commit()
    conn.close()

def get_user_history(user_id):
    """Retrieve user message history from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, message FROM chat_history
        WHERE user_id = ?
        ORDER BY timestamp DESC
    ''', (user_id,))
    history = cursor.fetchall()
    history = [{'role': row[0], 'content': row[1]} for row in history]
    conn.close()
    return history

def get_all_user_history():
    """Retrieve all user message history from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, role, message, timestamp FROM chat_history
        ORDER BY timestamp DESC
    ''')
    all_history = cursor.fetchall()
    all_history = [{'user_id': row[0], 'role': row[1], 'message': row[2], 'timestamp': row[3]} for row in all_history]
    conn.close()
    return all_history

def clear_user_history(user_id):
    """Clear user message history from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM chat_history WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()
    
def clear_all_user_history():
    """Clear all user message history from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM chat_history
    ''')
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------------------------------------------------------------
'''CREATED EVENTS TABLE'''
def save_created_event(user_id, title, start_time, end_time, description=None, location=None):
    """Save a created event to the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO created_events (user_id, title, start_time, end_time, description, location)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, title, start_time, end_time, description, location))
    conn.commit()
    conn.close()    
    
def get_created_events(user_id):
    """Retrieve created events for a user from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, start_time, end_time, description, location FROM created_events
        WHERE user_id = ?
        ORDER BY timestamp DESC
    ''', (user_id,))
    events = cursor.fetchall()
    events = [{'title': row[0], 'start_time': row[1], 'end_time': row[2], 'description': row[3], 'location': row[4]} for row in events]
    conn.close()
    return events

def get_event_by_id(event_id):
    """Retrieve a specific event by its ID"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, title, start_time, end_time, description, location FROM created_events
        WHERE event_id = ?
    ''', (event_id,))
    event = cursor.fetchone()
    if event:
        event = {
            'event_id': event_id,
            'user_id': event[0],
            'title': event[1],
            'start_time': event[2],
            'end_time': event[3],
            'description': event[4],
            'location': event[5]
        }
    conn.close()
    return event    

def delete_event(event_id):
    """Delete an event by its ID"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM created_events WHERE event_id = ?
    ''', (event_id,))
    conn.commit()
    conn.close()
    
def update_event(event_id, title=None, start_time=None, end_time=None, description=None, location=None):
    """Update an existing event by its ID"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    updates = []
    params = []
    
    if title:
        updates.append("title = ?")
        params.append(title)
    if start_time:
        updates.append("start_time = ?")
        params.append(start_time)
    if end_time:
        updates.append("end_time = ?")
        params.append(end_time)
    if description:
        updates.append("description = ?")
        params.append(description)
    if location:
        updates.append("location = ?")
        params.append(location)
    
    params.append(event_id)
    
    cursor.execute(f'''
        UPDATE created_events SET {', '.join(updates)} WHERE event_id = ?
    ''', tuple(params))
    
    conn.commit()
    conn.close()    
        
def get_all_created_events():
    """Retrieve all created events from the database"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT event_id, user_id, title, start_time, end_time, description, location FROM created_events
        ORDER BY timestamp DESC
    ''')
    all_events = cursor.fetchall()
    all_events = [{
        'event_id': row[0],
        'user_id': row[1],
        'title': row[2],
        'start_time': row[3],
        'end_time': row[4],
        'description': row[5],
        'location': row[6]
    } for row in all_events]
    conn.close()
    return all_events        