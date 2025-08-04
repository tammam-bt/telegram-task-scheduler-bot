import sqlite3
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Get database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")


"""Create a table for user message history if it doesn't exist"""
conn = sqlite3.connect(DATABASE_URL)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_history (
        user_id     TEXT PRIMARY KEY,
        role        TEXT NOT NULL,
        message     TEXT NOT NULL,
        timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()
conn.close()

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

        
        