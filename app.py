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
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    if not GOOGLE_API_ENABLED:
        return None
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                pass
        
        if not creds or not creds.valid:
            client_id = os.getenv('GOOGLE_CLIENT_ID')
            client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
            if client_id and client_secret:
                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "project_id": os.getenv('GOOGLE_PROJECT_ID', 'space-rep'),
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_secret": client_secret,
                        "redirect_uris": ["http://localhost"]
                    }
                }
                try:
                    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                    creds = flow.run_local_server(port=0)
                    with open('token.json', 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    print(f"OAuth flow failed: {e}")
                    return None
            elif os.path.exists('credentials.json'):
                try:
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
                    with open('token.json', 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    print(f"OAuth flow failed: {e}")
                    return None
            else:
                print("No API credentials provided. Calendar integration skipping.")
                return None
    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error connecting to Google Calendar: {e}")
        return None

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
    
    if 'color_id' in topic and topic['color_id']:
        event['colorId'] = topic['color_id']
        
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        task['calendar_event_id'] = created_event.get('id')
    except Exception as e:
        print(f"Failed to create Google Calendar event: {e}")

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

def generate_tasks(topic):
    intervals = {
        'Easy': [2, 5, 10, 20, 30],
        'Medium': [1, 3, 7, 14, 30],
        'Hard': [1, 2, 4, 7, 14]
    }
    
    diff = topic['difficulty']
    seq = intervals.get(diff, intervals['Medium'])
    
    tasks = []
    base_date = datetime.date.fromisoformat(topic['created_date'])
    
    for i, days in enumerate(seq):
        review_date = base_date + datetime.timedelta(days=days)
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
        subject = request.form.get('subject')
        if subject == 'NEW_SUBJECT':
            subject = request.form.get('new_subject_name')
            
        topic_name = request.form.get('topic_name')
        difficulty = request.form.get('difficulty')
        color_id = request.form.get('color_id', '1')
        
        if not subject or not topic_name or not difficulty:
            flash("All fields are required.")
            return redirect(url_for('add_topic'))
            
        topic = {
            "id": str(uuid.uuid4()),
            "subject": subject,
            "topic_name": topic_name,
            "difficulty": difficulty,
            "color_id": color_id,
            "created_date": datetime.date.today().isoformat()
        }
        
        data['topics'].append(topic)
        
        new_tasks = generate_tasks(topic)
        data['tasks'].extend(new_tasks)
        
        save_data(data)
        session['last_added_topic_id'] = topic['id']
        flash("Topic added and review tasks scheduled.")
        return redirect(url_for('dashboard'))
        
    subjects = list(set(t.get('subject', '') for t in data['topics'] if t.get('subject')))
    default_subjects = ["Mathematics", "Physics", "Chemistry", "Biology", "History", "Programming", "Literature"]
    for ds in default_subjects:
        if ds not in subjects:
            subjects.append(ds)
    subjects.sort()
        
    return render_template('add.html', subjects=subjects)

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
                
        save_data(data)
        
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    # Using host 0.0.0.0 so that PWA could be tested on local network securely with ngrok possibly later
    app.run(host='0.0.0.0', port=5000, debug=True)
