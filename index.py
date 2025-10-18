
import os
from dotenv import load_dotenv
from flask_mail import Mail, Message
from attendance_scraper import login, get_student_details, get_subjects, fetch_attendance

import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import psycopg2
import psycopg2.extras
from flask import Flask, flash, render_template, request, redirect, url_for, send_from_directory, jsonify

load_dotenv()
# Initialize Flask app

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your-secret-key-here")

# Flask-Mail configuration using environment variables
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'False') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')
mail = Mail(app)

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=os.environ.get('POSTGRES_HOST'),
            dbname=os.environ.get('POSTGRES_DATABASE'),
            user=os.environ.get('POSTGRES_USER'),
            password=os.environ.get('POSTGRES_PASSWORD'),
            port=os.environ.get('POSTGRES_PORT', 5432),
            connect_timeout=10
        )
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def sync_user_to_db(db_username, db_password=None):
    """Sync username and password to database with retry logic"""
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            conn = get_db_connection()
            if not conn:
                raise Exception("Database connection failed")
                
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            
            cur.execute("""
                    INSERT INTO users (username,password, created_at, updated_at)
                    VALUES (%s, %s, NOW(), NOW())
                    ON CONFLICT (username) 
                    DO UPDATE SET 
                        updated_at = NOW(),
                        login_count = COALESCE(users.login_count, 0) + 1""", (db_username,db_password,))
            
            conn.commit()
            cur.close()
            conn.close()
            print(f"Successfully synced username: {username}")
            return True
            
        except psycopg2.OperationalError as e:
            retry_count += 1
            print(f"DB Operational Error (attempt {retry_count}): {e}")
            if retry_count >= max_retries:
                print(f"Failed to sync {username} after {max_retries} attempts")
                return False
            
        except Exception as e:
            print(f"DB sync error for {username}: {e}")
            return False


@app.route('/')
def index():
    return render_template('index.html', show=os.environ.get('S_USERNAME', ''))


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        admission = request.form.get('admission', '').strip()
        user_email = request.form.get('user_email', '').strip()
        message = request.form.get('message', '').strip()
        screenshot = request.files.get('screenshot')
        if not admission or not message or not user_email:
            flash('Admission number, your email, and issue message are required.', 'error')
            return redirect(url_for('contact'))
            # Prepare email
        msg = Message(
            subject=f"Attendance App Issue from {admission}",
            recipients=[app.config['MAIL_DEFAULT_SENDER']],
            body=f"Admission Number: {admission}\nUser Email: {user_email}\n\nIssue Message:\n{message}"
        )
        # Attach screenshot if provided
        if screenshot and screenshot.filename:
            ext = screenshot.filename.rsplit('.', 1)[-1].lower()
            if ext in ['png', 'jpg', 'jpeg']:
                msg.attach(screenshot.filename, screenshot.mimetype, screenshot.read())
        mail.send(msg)
        flash('Your issue has been submitted successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('contact.html')


executor = ThreadPoolExecutor(max_workers=5)

@app.route('/sync-username', methods=['POST'])
def sync_username():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username:
            return jsonify({'error': 'Username missing'}), 400
        
        # Submit database sync task to thread pool for concurrent execution
        future = executor.submit(sync_user_to_db, username, password)
        
        # Return immediately for faster response
        return jsonify({
            'status': 'sync initiated', 
            'username': username,
            'has_password': bool(password),
            'thread_id': str(threading.current_thread().ident)
        }), 202
        
    except Exception as e:
        print(f"Sync username endpoint error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/check', methods=['POST'])
def check_attendance():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    # Basic validation
    if not username or not password:
        return render_template(
            'error.html',
            error_message="Please provide both username and password.",
            back_url="/"
        )

    try:
        # Login and fetch data
        session = login(username, password)
        details = get_student_details(session)
        subjects = get_subjects(session, details)
        df_summary = fetch_attendance(session, subjects)
        

        # Async store username and password in database
        executor.submit(sync_user_to_db, username, password)

        # Calculate totals using the SimpleDataFrame methods
        total_days = df_summary.sum_column("Total Days")
        total_present = df_summary.sum_column("No. of Present")
        overall_attendance_pct = (
            round((total_present / total_days) * 100, 2)
            if total_days > 0 else 0
        )

        try:
            # Login and fetch data
            session = login(username, password)
            details = get_student_details(session)
            subjects = get_subjects(session, details)
            df_summary = fetch_attendance(session, subjects)
            

            # Async store username and password in database
            executor.submit(sync_user_to_db, username, password)

            # Calculate totals using the SimpleDataFrame methods
            total_days = df_summary.sum_column("Total Days")
            total_present = df_summary.sum_column("No. of Present")
            overall_attendance_pct = (
                round((total_present / total_days) * 100, 2)
                if total_days > 0 else 0
            )

            # Get df as list of dicts
            df = df_summary.to_dict(orient="records")

            # Calculate Can Skip and Need to Attend for each subject
            for row in df:
                if row is None:
                    continue
                total = row['Total Days']
                present = row['No. of Present']
                pct = row['Attendance %']
                if total == 0:
                    row['Can Skip'] = 0
                    row['Need to Attend'] = 0
                elif pct >= 75:
                    max_leaves = int((present / 0.75) - total)
                    row['Can Skip'] = max(0, max_leaves)
                    row['Need to Attend'] = 0
                else:
                    required = int((0.75 * total - present) / 0.25)
                    row['Can Skip'] = 0
                    row['Need to Attend'] = max(0, required)

            return render_template(
                'result.html',
                details=details,
                df=df_summary.to_dict(orient="records"),
                total_days=total_days,
                total_present=total_present,
                overall_attendance_pct=overall_attendance_pct,
                show=os.environ.get('S_USERNAME','')
            )

        except ValueError as e:
            print(f"[ERROR] ValueError in /check: {e}")
            return render_template(
                'error.html',
                error_message=str(e),
                back_url="/"
            )

        except Exception as e:
            import traceback
            print(f"[ERROR] Exception in /check: {e}")
            traceback.print_exc()
            return render_template(
                'error.html',
                error_message=f"An error occurred while fetching attendance data: {str(e)}",
                back_url="/"
            )
    except Exception as e:
        import traceback
        print(f"[ERROR] Exception in /check: {e}")
        traceback.print_exc()
        return render_template(
            'error.html',
            error_message=f"An error occurred while fetching attendance data: {str(e)}",
            back_url="/"
        )


# Local dev only (Vercel will ignore this and import `app`)
if __name__ == '__main__':
    app.run(debug=False)

# Clean shutdown of thread pool
import atexit
atexit.register(lambda: executor.shutdown(wait=True))



