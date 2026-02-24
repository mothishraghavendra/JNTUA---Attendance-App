import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS login_stats (
                    user_id       TEXT      NOT NULL,
                    password      TEXT,
                    name          TEXT,
                    branch        TEXT,
                    date          DATE      NOT NULL,
                    first_login   TIMESTAMP NOT NULL,
                    last_login    TIMESTAMP NOT NULL,
                    success_count INTEGER   DEFAULT 0,
                    failure_count INTEGER   DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                );

                CREATE TABLE IF NOT EXISTS analytics (
                    id                      SERIAL        PRIMARY KEY,
                    week_start              DATE,
                    week_end                DATE,
                    unique_users            INTEGER,
                    total_successful_logins INTEGER,
                    total_failed_logins     INTEGER,
                    success_rate            NUMERIC(5,2),
                    failure_rate            NUMERIC(5,2),
                    user_engagement_rate    NUMERIC(5,2),
                    unique_success_users    INTEGER,
                    unique_failed_users     INTEGER,
                    median_logins_per_user  NUMERIC(8,1),
                    p95_logins_per_user     NUMERIC(8,1),
                    top_3_users             TEXT,
                    top_3_branches          TEXT,
                    branch_with_max_logins  TEXT,
                    most_active_user        TEXT,
                    peak_day                DATE,
                    peak_login_hour         TEXT,
                    peak_login_window       TEXT,
                    avg_daily_logins        NUMERIC(8,1),
                    generated_at            TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()
    print("Tables created successfully.")

if __name__ == "__main__":
    init_db()
