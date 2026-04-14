import os
import json
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv

load_dotenv()

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_API_ENABLED = True
except ImportError:
    GOOGLE_API_ENABLED = False
    print("Google API modules missing. Calendar integration is disabled. Run: pip install -r requirements.txt")

app = Flask(__name__)
app.secret_key = 'space-rep-secret'

DATA_FILE = 'data.json'
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/tasks'
]

DEFAULT_SUBJECTS_BY_TRACK = {
    "School": [
        "Eng R", "Eng G", "Hindi R", "Hindi G", "Physics",
        "Biology", "Chemistry", "SSt", "AI", "Maths"
    ],
    "Allen": ["Physics", "Chemistry", "Maths"]
}

# Google Calendar color IDs mapped from user-requested subject colors.
SUBJECT_COLOR_IDS = {
    "eng r": "6",       # Tangerine-ish (brown approximation)
    "eng g": "6",
    "hindi r": "11",    # Tomato-ish (maroon approximation)
    "hindi g": "11",
    "physics": "10",    # Basil (dark green)
    "biology": "5",     # Banana-ish (skin approximation)
    "chemistry": "5",   # Yellow
    "sst": "9",         # Blueberry
    "ai": "8",          # Graphite (silver approximation)
    "maths": "2",       # Sage (light green)
    "mathematics": "2"
}

def load_google_client_config():
    """
    Load OAuth client config with dotenv-first priority.
    Supported .env keys:
      - GOOGLE_CLIENT_ID
      - GOOGLE_CLIENT_SECRET
      - GOOGLE_PROJECT_ID (optional)
      - GOOGLE_REDIRECT_URI (optional, defaults to http://localhost)
      - GOOGLE_CREDENTIALS_FILE (optional, defaults to credentials.json)
    """
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    project_id = os.getenv('GOOGLE_PROJECT_ID', 'space-rep')
    redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost')

    # Preferred path: all secrets from dotenv/environment.
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "project_id": project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": [redirect_uri]
            }
        }

    # Backward-compatible fallback: read local credentials file if env is missing.
    credentials_file = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
    if os.path.exists(credentials_file):
        try:
            with open(credentials_file, 'r', encoding='utf-8') as f:
                raw_config = json.load(f)
            if 'installed' in raw_config:
                return raw_config
            if 'web' in raw_config:
                # Some Google projects download "web" instead of "installed".
                web = raw_config['web']
                return {
                    "installed": {
                        "client_id": web.get("client_id"),
                        "project_id": web.get("project_id", project_id),
                        "auth_uri": web.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                        "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
                        "auth_provider_x509_cert_url": web.get("auth_provider_x509_cert_url", "https://www.googleapis.com/oauth2/v1/certs"),
                        "client_secret": web.get("client_secret"),
                        "redirect_uris": web.get("redirect_uris", [redirect_uri])
                    }
                }
        except Exception as e:
            print(f"Failed to parse {credentials_file}: {e}")

    return None

def get_google_credentials():
    if not GOOGLE_API_ENABLED:
        return None
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # Force re-auth if token exists but lacks newly-required scopes.
    if creds and not creds.has_scopes(SCOPES):
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                pass
        
        if not creds or not creds.valid:
            client_config = load_google_client_config()
            if not client_config:
                print("No API credentials found. Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in .env or provide credentials.json.")
                return None
            try:
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                print(f"OAuth flow failed: {e}")
                return None
    return creds

def get_calendar_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error connecting to Google Calendar: {e}")
        return None

def get_tasks_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build('tasks', 'v1', credentials=creds)
    except Exception as e:
        print(f"Error connecting to Google Tasks: {e}")
        return None

def get_google_keep_client():
    try:
        import gkeepapi
    except ImportError:
        return None

    keep_email = os.getenv("GOOGLE_KEEP_EMAIL")
    keep_password = os.getenv("GOOGLE_KEEP_PASSWORD")
    if not keep_email or not keep_password:
        return None

    try:
        keep = gkeepapi.Keep()
        if not keep.login(keep_email, keep_password):
            return None
        return keep
    except Exception as e:
        print(f"Google Keep login failed: {e}")
        return None

def get_subject_color_id(subject):
    return SUBJECT_COLOR_IDS.get((subject or "").strip().lower(), "7")

def add_event_to_calendar(task, topic):
    service = get_calendar_service()
    if not service:
        return
    
    event = {
        'summary': f"Review: {topic['subject']} - {topic['topic_name']}",
        'description': f"Spaced Repetition Review for {topic['subject']}. Task: {task['title']}\nDifficulty: {topic['difficulty']}",
        'start': {
            'date': task['scheduled_date'],
            'timeZone': 'UTC',
        },
        'end': {
            'date': task['scheduled_date'],
            'timeZone': 'UTC',
        },
    }
    
    event['colorId'] = get_subject_color_id(topic.get('subject'))
        
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        task['calendar_event_id'] = created_event.get('id')
    except Exception as e:
        print(f"Failed to create Google Calendar event: {e}")

def add_google_task(task, topic):
    service = get_tasks_service()
    if not service:
        return

    body = {
        "title": f"Review: {topic['subject']} - {topic['topic_name']} ({task['title']})",
        "notes": f"Difficulty: {topic['difficulty']}",
        "due": f"{task['scheduled_date']}T00:00:00.000Z"
    }
    try:
        created = service.tasks().insert(tasklist='@default', body=body).execute()
        task['google_task_id'] = created.get('id')
    except Exception as e:
        print(f"Failed to create Google Task: {e}")

def update_google_task_date(task):
    service = get_tasks_service()
    if not service or 'google_task_id' not in task:
        return
    try:
        body = service.tasks().get(tasklist='@default', task=task['google_task_id']).execute()
        body['due'] = f"{task['scheduled_date']}T00:00:00.000Z"
        service.tasks().update(tasklist='@default', task=task['google_task_id'], body=body).execute()
    except Exception as e:
        print(f"Failed to update Google Task date: {e}")

def complete_google_task(task):
    service = get_tasks_service()
    if not service or 'google_task_id' not in task:
        return
    try:
        body = service.tasks().get(tasklist='@default', task=task['google_task_id']).execute()
        body['status'] = 'completed'
        body['completed'] = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        service.tasks().update(tasklist='@default', task=task['google_task_id'], body=body).execute()
    except Exception as e:
        print(f"Failed to complete Google Task: {e}")

def delete_google_task(task):
    service = get_tasks_service()
    if not service or 'google_task_id' not in task:
        return
    try:
        service.tasks().delete(tasklist='@default', task=task['google_task_id']).execute()
    except Exception as e:
        print(f"Failed to delete Google Task: {e}")

def add_task_to_google_keep(topic, task):
    keep = get_google_keep_client()
    if not keep:
        return

    track = topic.get("track", "School")
    target_note = None
    for note in keep.all():
        if note.title and note.title.strip().lower() == track.lower():
            target_note = note
            break

    if not target_note:
        target_note = keep.createNote(track, "")

    keep_text = (
        f"{task['scheduled_date']}: {topic['subject']} - "
        f"{topic['topic_name']} ({task['title']})"
    )
    target_note.text = (target_note.text + "\n" + keep_text).strip()
    try:
        keep.sync()
    except Exception as e:
        print(f"Failed to sync Google Keep note: {e}")

def update_calendar_event_date(task):
    service = get_calendar_service()
    if not service or 'calendar_event_id' not in task:
        return
    
    try:
        event = service.events().get(calendarId='primary', eventId=task['calendar_event_id']).execute()
        event['start']['date'] = task['scheduled_date']
        event['end']['date'] = task['scheduled_date']
        service.events().update(calendarId='primary', eventId=task['calendar_event_id'], body=event).execute()
    except Exception as e:
        print(f"Failed to update Google Calendar event: {e}")


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"topics": [], "tasks": []}
    with open(DATA_FILE, 'r') as f:
        try:
            return json.load(f)
        except:
            return {"topics": [], "tasks": []}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def check_overdue_tasks():
    data = load_data()
    today_str = datetime.date.today().isoformat()
    updated = False
    
    for task in data['tasks']:
        if task['status'] == 'pending':
            if task['scheduled_date'] < today_str:
                task['scheduled_date'] = today_str
                task['missed_count'] = task.get('missed_count', 0) + 1
                updated = True
                
    if updated:
        save_data(data)

def generate_tasks(topic, existing_tasks=None):
    intervals = {
        'Easy': [2, 5, 10, 20, 30],
        'Medium': [1, 3, 7, 14, 30],
        'Hard': [1, 2, 4, 7, 14]
    }
    
    diff = topic['difficulty']
    seq = intervals.get(diff, intervals['Medium'])
    
    tasks = []
    base_date = datetime.date.fromisoformat(topic['created_date'])
    
    existing_keys = set()
    if existing_tasks:
        for existing in existing_tasks:
            existing_keys.add((
                existing.get("topic_id"),
                existing.get("interval_stage"),
                existing.get("scheduled_date")
            ))

    for i, days in enumerate(seq):
        review_date = base_date + datetime.timedelta(days=days)
        task_key = (topic['id'], i + 1, review_date.isoformat())
        if task_key in existing_keys:
            continue

        task = {
            "id": str(uuid.uuid4()),
            "topic_id": topic['id'],
            "title": f"Review {i+1}",
            "scheduled_date": review_date.isoformat(),
            "status": "pending",
            "interval_stage": i + 1,
            "missed_count": 0
        }
        tasks.append(task)
        add_event_to_calendar(task, topic)
        add_google_task(task, topic)
        add_task_to_google_keep(topic, task)
        
    return tasks


@app.route('/')
def dashboard():
    check_overdue_tasks()
    data = load_data()
    today_str = datetime.date.today().isoformat()
    tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    
    missed_tasks = []
    today_tasks = []
    upcoming_tasks = []
    
    topic_map = {t['id']: t for t in data['topics']}
    
    for task in data['tasks']:
        if task['status'] == 'completed':
            continue
        
        task_view = dict(task)
        topic = topic_map.get(task['topic_id'], {})
        task_view['subject'] = topic.get('subject', 'Unknown')
        task_view['topic_name'] = topic.get('topic_name', 'Unknown')
        task_view['difficulty'] = topic.get('difficulty', '')
        
        if task['scheduled_date'] < today_str:
            missed_tasks.append(task_view)
        elif task['scheduled_date'] == today_str:
            today_tasks.append(task_view)
        elif task['scheduled_date'] == tomorrow_str:
            upcoming_tasks.append(task_view)
            
    upcoming_tasks.sort(key=lambda x: x['scheduled_date'])
            
    return render_template('index.html', missed=missed_tasks, today=today_tasks, upcoming=upcoming_tasks)

@app.route('/add', methods=['GET', 'POST'])
def add_topic():
    data = load_data()
    
    if request.method == 'POST':
        track = request.form.get('track')
        subject = request.form.get('subject')
            
        topic_name = request.form.get('topic_name')
        difficulty = request.form.get('difficulty')
        
        if not track or track not in DEFAULT_SUBJECTS_BY_TRACK:
            flash("Please select either School or Allen.")
            return redirect(url_for('add_topic'))

        if subject not in DEFAULT_SUBJECTS_BY_TRACK[track]:
            flash("Please select a valid subject for the selected track.")
            return redirect(url_for('add_topic'))

        if not subject or not topic_name or not difficulty:
            flash("All fields are required.")
            return redirect(url_for('add_topic'))

        today = datetime.date.today().isoformat()
        for existing_topic in data['topics']:
            if (
                existing_topic.get("track") == track and
                existing_topic.get("subject") == subject and
                existing_topic.get("topic_name", "").strip().lower() == topic_name.strip().lower() and
                existing_topic.get("created_date") == today
            ):
                flash("This topic was already added today. Skipping duplicate entry.")
                return redirect(url_for('dashboard'))
            
        topic = {
            "id": str(uuid.uuid4()),
            "track": track,
            "subject": subject,
            "topic_name": topic_name,
            "difficulty": difficulty,
            "color_id": get_subject_color_id(subject),
            "created_date": today
        }
        
        data['topics'].append(topic)
        
        new_tasks = generate_tasks(topic, data['tasks'])
        data['tasks'].extend(new_tasks)
        
        save_data(data)
        session['last_added_topic_id'] = topic['id']
        flash("Topic added and review tasks scheduled.")
        return redirect(url_for('dashboard'))
        
    return render_template('add.html', subject_sets=DEFAULT_SUBJECTS_BY_TRACK)

def delete_calendar_event(task):
    service = get_calendar_service()
    if not service or 'calendar_event_id' not in task:
        return
    try:
        service.events().delete(calendarId='primary', eventId=task['calendar_event_id']).execute()
    except Exception as e:
        print(f"Failed to delete Google Calendar event: {e}")

@app.route('/undo_last_topic', methods=['POST'])
def undo_last_topic():
    topic_id = session.get('last_added_topic_id')
    if not topic_id:
        return redirect(url_for('dashboard'))
        
    data = load_data()
    
    # Remove tasks and their calendar events
    tasks_to_keep = []
    for task in data['tasks']:
        if task['topic_id'] == topic_id:
            delete_calendar_event(task)
            delete_google_task(task)
        else:
            tasks_to_keep.append(task)
            
    data['tasks'] = tasks_to_keep
    
    # Remove topic
    data['topics'] = [t for t in data['topics'] if t['id'] != topic_id]
    
    save_data(data)
    session.pop('last_added_topic_id', None)
    flash("Successfully undid the last added topic.")
    
    return redirect(url_for('dashboard'))

@app.route('/complete_task/<task_id>', methods=['POST'])
def complete_task(task_id):
    data = load_data()
    for task in data['tasks']:
        if task['id'] == task_id:
            task['status'] = 'completed'
            complete_google_task(task)
            break
            
    save_data(data)
    return redirect(url_for('dashboard'))

@app.route('/delay_task/<task_id>', methods=['POST'])
def delay_task(task_id):
    data = load_data()
    target_task = None
    
    for task in data['tasks']:
        if task['id'] == task_id:
            target_task = task
            break
            
    if target_task:
        topic_id = target_task['topic_id']
        stage = target_task.get('interval_stage', 1)
        
        for task in data['tasks']:
            # Delay this task and all future tasks for this topic by 1 day
            if task['topic_id'] == topic_id and task.get('interval_stage', 1) >= stage and task['status'] != 'completed':
                cur_date = datetime.date.fromisoformat(task['scheduled_date'])
                new_date = cur_date + datetime.timedelta(days=1)
                task['scheduled_date'] = new_date.isoformat()
                task['missed_count'] = max(0, task.get('missed_count', 0) - 1) # Reduce missed count since we're explicitly delaying
                update_calendar_event_date(task)
                update_google_task_date(task)
                
        save_data(data)
        
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    # Using host 0.0.0.0 so that PWA could be tested on local network securely with ngrok possibly later
    app.run(host='0.0.0.0', port=5000, debug=True)
