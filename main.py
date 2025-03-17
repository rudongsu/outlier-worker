from dotenv import load_dotenv
import os
import json  # Add this line
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
from time import sleep
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import schedule
import threading
import sys
import signal
from datetime import datetime, time as datetime_time, timedelta  # Add timedelta here too

# Project names to monitor
MONITORED_PROJECTS = [
    "Mint Rating V2",
    "Sunny Mathematics",
]

# Load environment variables from .env file
load_dotenv()

# Define URLs
LOGIN_URL = "https://app.outlier.ai/internal/loginNext/expert?redirect_url=marketplace"
MARKETPLACE_URL = "https://app.outlier.ai/internal/experts/project/marketplace/history"
REMAINING_TASKS_URL = "https://app.outlier.ai/internal/user-projects/bulk-remaining-tasks"

# from environment variables
EMAIL = os.getenv("EMAIL")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
PASSWORD = os.getenv("PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL")
TO_EMAIL = os.getenv("TO_EMAIL")


# Create SendGrid client instance
sg_client = SendGridAPIClient(SENDGRID_API_KEY)

WORK_HOURS_START = datetime_time(9, 0)  # 9:00 AM ACST
WORK_HOURS_END = datetime_time(17, 30)  # 5:30 PM ACST
NIGHT_HOURS_END = datetime_time(23, 59)  # 11:59 PM ACST
EMAIL_COOLDOWN_MINUTES = 60  # Send emails every hour during allowed hours
LAST_EMAIL_SENT = {}  # Track last email time per project

def should_send_email():
    """Check if we should send an email based on time of day."""
    now = datetime.now()
    current_time = now.time()
    
    # Don't send emails on weekends
    if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
        
    # Don't send during work hours
    if WORK_HOURS_START <= current_time <= WORK_HOURS_END:
        return False
        
    # Only send between work hours end and night hours end
    if WORK_HOURS_END <= current_time <= NIGHT_HOURS_END:
        # Check cooldown period
        for project_id, last_sent in LAST_EMAIL_SENT.items():
            if (now - last_sent).total_seconds() < EMAIL_COOLDOWN_MINUTES * 60:
                print(f"Email cooldown active until {last_sent + timedelta(minutes=EMAIL_COOLDOWN_MINUTES)}")
                return False
        return True
        
    return False

def load_project_ids():
    """Load project IDs from projects.json file."""
    try:
        with open('projects.json', 'r') as f:
            projects = json.load(f)
            return list(projects.keys()), projects
    except Exception as e:
        print(f"Error loading projects.json: {e}")
        return [], {}

def check_remaining_tasks(session, headers):
    # Load current projects
    project_ids, project_map = load_project_ids()
    
    tasks_headers = headers.copy()
    tasks_headers.update({
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://app.outlier.ai/internal/experts/project/marketplace",
        "Origin": "https://app.outlier.ai",
        "x-csrf-token": session.cookies.get('_csrf')
    })
    

    payload = {
        "projectIds": project_ids
    }

    try:
        response = session.post(
            REMAINING_TASKS_URL,
            json=payload,
            headers=tasks_headers
        )
        
        print("Request URL:", response.url)
        print("Response status:", response.status_code)
        
        # Handle different content encodings
        content = response.content
        if response.headers.get('Content-Encoding') == 'br':
            import brotli
            content = brotli.decompress(content)
        
        # Try to decode the content
        try:
            text_content = content.decode('utf-8')
            print("Raw response content:", text_content[:200])
            
            if not text_content:
                print("Empty response received")
                return
                
            data = json.loads(text_content)
            print("Successfully parsed JSON response")
            
            # Update web interface with new counts
            try:
                web_app_url = os.getenv('WEB_APP_URL')
                if not web_app_url:
                    print("WEB_APP_URL environment variable not set")
                    return
                    
                update_url = f"{web_app_url.rstrip('/')}/update_counts"
                print(f"Updating web interface at: {update_url}")
                
                response = requests.post(update_url, json=data)
                if response.status_code == 200:
                    print("Successfully updated web interface")
                else:
                    print(f"Failed to update web interface: {response.status_code}")
            except Exception as e:
                print(f"Failed to update web interface: {e}")

            # Check for projects with remaining tasks
            projects_with_tasks = [
                project for project in data
                if project["count"] > 0
            ]
            
            if projects_with_tasks:
                if should_send_email():
                    projects_info = "\n".join([
                        f"Project: {project_map[p['projectId']]['name']}\n"
                        f"Project ID: {p['projectId']}\n"
                        f"Remaining Tasks: {p['count']}\n"
                        for p in projects_with_tasks
                    ])
                    
                    subject = "Projects With Remaining Tasks Available!"
                    body = f"Found {len(projects_with_tasks)} projects with tasks:\n\n{projects_info}"
                    send_email(subject, body)
                    
                    # Update last email sent time for all projects
                    now = datetime.now()
                    for project in projects_with_tasks:
                        LAST_EMAIL_SENT[project['projectId']] = now
                        
                    print(f"Found {len(projects_with_tasks)} projects with tasks and sent email notification!")
                else:
                    print("Found projects with tasks but outside email notification hours")
            else:
                print("No projects with remaining tasks.")
                
        except requests.exceptions.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {str(e)}")
            print("Status code:", response.status_code)
            print("Response headers:", dict(response.headers))
            print("Raw response content:", response.content[:200].hex())
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {str(e)}")

def send_email(subject, body):
    """Send an email notification using SendGrid."""
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=subject,
        plain_text_content=body)
    try:
        response = sg_client.send(message)
        print("Email sent successfully!")
        print(response.status_code)
    except Exception as e:
        print("Failed to send email:", str(e))

def check_marketplace(session, headers):
    """Check the marketplace for monitored projects."""
    # Update headers specifically for marketplace request
    marketplace_headers = headers.copy()
    marketplace_headers.update({
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate, br",  # Change from just "br"
        "Content-Type": "application/json",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://app.outlier.ai/internal/experts/project/marketplace",
        "Origin": "https://app.outlier.ai",
        "x-csrf-token": session.cookies.get('_csrf')
    })

    # print("Using CSRF token:", session.cookies.get('_csrf'))  # Debug print

    params = {
        "pageSize": 10,
        "page": 0,
        "filter": "available"
    }

    try:
        response = session.get(
            MARKETPLACE_URL, 
            params=params, 
            headers=marketplace_headers
        )
        
        # Debug prints
        print("Request URL:", response.url)
        print("Response status:", response.status_code)
        print("Raw response content:", response.text[:200])  # Add this line

        if response.status_code == 200:
            try:
                if not response.text:
                    print("Empty response received")
                    return
                    
                data = response.json()
                print("Successfully parsed JSON response")
                
                # Check for monitored projects
                found_projects = [
                    project for project in data["results"]
                    if project["projectName"] in MONITORED_PROJECTS
                ]
                
                if found_projects:
                    projects_info = "\n".join([
                        f"Project: {p['projectName']}\n"
                        f"Description: {p['projectDescription']}\n"
                        f"Latest Activity: {p['latestActivity']}\n"
                        for p in found_projects
                    ])
                    
                    subject = "Monitored Projects Available!"
                    body = f"Found {len(found_projects)} monitored projects:\n\n{projects_info}"
                    send_email(subject, body)
                    print(f"Found {len(found_projects)} monitored projects! Exiting...")
                    # Exit the program after sending email
                else:
                    print("No monitored projects available.")
                    
            except requests.exceptions.JSONDecodeError as e:
                print(f"Failed to parse JSON response from marketplace: {str(e)}")
                print("Status code:", response.status_code)
                print("Response headers:", dict(response.headers))
                print("Raw response content:", response.content[:200].hex())  # Print hex representation
        else:
            print(f"Failed to access marketplace: {response.status_code}")
            print("Headers:", dict(response.headers))
            print("Response:", response.text)
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {str(e)}")

def check_projects():
    """Check the marketplace for available projects."""
    # Start a session
    session = requests.Session()

    # Set headers to mimic a real browser more accurately
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }

    # Login payload
    login_payload = {
        "email": EMAIL,
        "password": PASSWORD
    }

    # Perform login
    login_response = session.post(LOGIN_URL, json=login_payload, headers=headers)
    if login_response.status_code == 200:
        print("Login successful!")
        csrf_token = session.cookies.get('_csrf')
        
        # Increase delay to ensure session is properly established
        sleep(random.uniform(3, 5)) 

        # Print cookies and CSRF token for debugging
        # print("Cookies:", session.cookies.get_dict())
        # print("CSRF Token:", csrf_token)

        # Update headers with CSRF token and additional required headers
        headers.update({
            "x-csrf-token": csrf_token,
            "Referer": "https://app.outlier.ai/internal/experts/project/marketplace",
            "Origin": "https://app.outlier.ai"
        })
        
        # Print final headers for debugging
        # print("Request headers:", headers)

        # check_marketplace(session, headers)
        check_remaining_tasks(session, headers)
    else:
        print(f"Login failed with status code {login_response.status_code}. Check your credentials.")
        print("Response:", login_response.text)

running = True

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global running
    print(f"Received signal {signum}. Shutting down gracefully...")
    running = False

def run_schedule():
    """Run the schedule in a separate thread."""
    global running
    while running:
        schedule.run_pending()
        sleep(1)  # Change this line

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Fix the time formatting
    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"Starting scheduler at {start_time}")
    
    try:
        # Schedule check_projects to run every 2 minutes
        schedule.every(2).minutes.do(check_projects)

        # Start the scheduler in a separate thread
        scheduler_thread = threading.Thread(target=run_schedule)
        scheduler_thread.daemon = True
        scheduler_thread.start()

        # Run check_projects immediately on startup
        check_projects()

        # Keep main thread alive
        while running:
            sleep(1)  # Change this line

    except Exception as e:
        print(f"Error in main thread: {str(e)}")
    finally:
        print("Shutting down...")
        running = False
        scheduler_thread.join(timeout=5)
        print("Shutdown complete")