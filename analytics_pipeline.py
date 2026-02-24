import os
import json
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def run_pipeline():
    with get_conn() as conn:
        with conn.cursor() as cur:

            # Check if data exists
            cur.execute("SELECT COUNT(*) FROM login_stats;")
            if cur.fetchone()[0] == 0:
                print("No data this week. Skipping.")
                return

            # Basic aggregates
            cur.execute("""
                SELECT
                    MIN(date),
                    MAX(date),
                    COUNT(DISTINCT user_id),
                    SUM(success_count),
                    SUM(failure_count),
                    ROUND(SUM(success_count)::numeric /
                        NULLIF(SUM(success_count + failure_count), 0) * 100, 2),
                    ROUND(SUM(failure_count)::numeric /
                        NULLIF(SUM(success_count + failure_count), 0) * 100, 2),
                    COUNT(DISTINCT CASE WHEN success_count > 0 THEN user_id END),
                    COUNT(DISTINCT CASE WHEN failure_count > 0 THEN user_id END),
                    ROUND(
                        COUNT(DISTINCT CASE WHEN success_count > 0 THEN user_id END)::numeric /
                        NULLIF(COUNT(DISTINCT user_id), 0) * 100, 2),
                    ROUND(SUM(success_count)::numeric /
                        NULLIF(COUNT(DISTINCT date), 0), 1)
                FROM login_stats;
            """)
            base = cur.fetchone()
            (week_start, week_end, unique_users, total_success, total_failure,
             success_rate, failure_rate, unique_success_users, unique_failed_users,
             user_engagement_rate, avg_daily_logins) = base

            # Median logins per user
            cur.execute("""
                SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY user_total)
                FROM (
                    SELECT SUM(success_count) AS user_total
                    FROM login_stats GROUP BY user_id
                ) sub;
            """)
            median_logins = cur.fetchone()[0] or 0

            # P95 logins per user
            cur.execute("""
                SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY user_total)
                FROM (
                    SELECT SUM(success_count) AS user_total
                    FROM login_stats GROUP BY user_id
                ) sub;
            """)
            p95_logins = cur.fetchone()[0] or 0

            # Top 3 users
            cur.execute("""
                SELECT user_id, SUM(success_count) AS total
                FROM login_stats
                GROUP BY user_id
                ORDER BY total DESC LIMIT 3;
            """)
            top_3_users = json.dumps([
                {"user": r[0], "logins": r[1]} for r in cur.fetchall()
            ])

            # Top 3 branches
            cur.execute("""
                SELECT branch, SUM(success_count) AS total
                FROM login_stats
                WHERE branch IS NOT NULL AND branch != ''
                GROUP BY branch
                ORDER BY total DESC LIMIT 3;
            """)
            top_3_branches = json.dumps([
                {"branch": r[0], "logins": r[1]} for r in cur.fetchall()
            ])

            # Branch with max logins
            cur.execute("""
                SELECT branch FROM login_stats
                WHERE branch IS NOT NULL AND branch != ''
                GROUP BY branch
                ORDER BY SUM(success_count) DESC LIMIT 1;
            """)
            row = cur.fetchone()
            branch_with_max = row[0] if row else "N/A"

            # Most active user
            cur.execute("""
                SELECT user_id FROM login_stats
                GROUP BY user_id
                ORDER BY SUM(success_count) DESC LIMIT 1;
            """)
            row = cur.fetchone()
            most_active_user = row[0] if row else "N/A"

            # Peak day
            cur.execute("""
                SELECT date FROM login_stats
                GROUP BY date
                ORDER BY SUM(success_count) DESC LIMIT 1;
            """)
            row = cur.fetchone()
            peak_day = row[0] if row else None

            # Peak login hour
            cur.execute("""
                SELECT TO_CHAR(DATE_TRUNC('hour', first_login), 'HH12:00 AM')
                FROM login_stats
                GROUP BY DATE_TRUNC('hour', first_login)
                ORDER BY COUNT(*) DESC LIMIT 1;
            """)
            row = cur.fetchone()
            peak_login_hour = row[0] if row else "N/A"

            # Peak login window (2-hour block)
            cur.execute("""
                SELECT EXTRACT(HOUR FROM first_login)::int AS hr, COUNT(*) AS cnt
                FROM login_stats
                GROUP BY hr
                ORDER BY cnt DESC LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                hr         = int(row[0])
                start_hr   = datetime.strptime(str(hr), "%H").strftime("%I:00 %p")
                end_hr     = datetime.strptime(str((hr + 2) % 24), "%H").strftime("%I:00 %p")
                peak_window = f"{start_hr}–{end_hr}"
            else:
                peak_window = "N/A"

            # Save to analytics (permanent)
            cur.execute("""
                INSERT INTO analytics (
                    week_start, week_end,
                    unique_users, total_successful_logins, total_failed_logins,
                    success_rate, failure_rate, user_engagement_rate,
                    unique_success_users, unique_failed_users,
                    median_logins_per_user, p95_logins_per_user,
                    top_3_users, top_3_branches, branch_with_max_logins,
                    most_active_user, peak_day, peak_login_hour,
                    peak_login_window, avg_daily_logins
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
            """, (
                week_start, week_end,
                unique_users, total_success, total_failure,
                success_rate, failure_rate, user_engagement_rate,
                unique_success_users, unique_failed_users,
                median_logins, p95_logins,
                top_3_users, top_3_branches, branch_with_max,
                most_active_user, peak_day, peak_login_hour,
                peak_window, avg_daily_logins
            ))

            # Reset login_stats for next week
            cur.execute("DELETE FROM login_stats;")

        conn.commit()

    print(f"Week {week_start} → {week_end}")
    print(f"Users: {unique_users} | Logins: {total_success} | Failures: {total_failure}")
    print(f"Success: {success_rate}% | Failure: {failure_rate}% | Engagement: {user_engagement_rate}%")
    print(f"Median: {median_logins} | P95: {p95_logins} | Peak Window: {peak_window}")
    print(f"Top Users: {top_3_users}")
    print(f"Top Branches: {top_3_branches}")
    print("login_stats cleared. Fresh week starts now.")

if __name__ == "__main__":
    run_pipeline()
