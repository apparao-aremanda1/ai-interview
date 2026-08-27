import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing in environment variables.")

engine = create_engine(DATABASE_URL)


def init_db():
    with engine.connect() as conn:
        print("Dropping old tables if they exist...")
        conn.execute(text("""
            DROP TABLE IF EXISTS candidates CASCADE;
            DROP TABLE IF EXISTS job_descriptions CASCADE;
            DROP TABLE IF EXISTS branch_accounts CASCADE;
        """))

        print("Creating complete database schema...")

        # 1. Branch Accounts Table with activation support
        conn.execute(text("""
            CREATE TABLE branch_accounts (
                account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                company_name VARCHAR(128) NOT NULL,
                branch_name VARCHAR(128) NOT NULL,
                email VARCHAR(128) UNIQUE NOT NULL,
                password_hash VARCHAR(256) NOT NULL,
                is_active BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_company_branch UNIQUE (company_name, branch_name)
            );
        """))

        # 2. Job Descriptions Table with Persona, Passing Score, Skills & Must-Ask Questions
        conn.execute(text("""
            CREATE TABLE job_descriptions (
                job_id VARCHAR(64) NOT NULL,
                account_id UUID NOT NULL REFERENCES branch_accounts(account_id) ON DELETE CASCADE,
                job_role VARCHAR(128) NOT NULL,
                seniority VARCHAR(64),
                interview_type VARCHAR(64),
                tech_stack TEXT,
                interviewer_voice VARCHAR(64),
                persona VARCHAR(128) DEFAULT 'Strict & Professional Technical Lead',
                passing_score NUMERIC(3, 1) DEFAULT 7.0,
                skills_to_test TEXT,
                must_questions TEXT,
                status VARCHAR(32) DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, job_id)
            );
        """))

        # 3. Candidates Table with feedback and evaluation status
        conn.execute(text("""
            CREATE TABLE candidates (
                candidate_id SERIAL PRIMARY KEY,
                account_id UUID NOT NULL,
                job_id VARCHAR(64) NOT NULL,
                candidate_name VARCHAR(128),
                email VARCHAR(128),
                mobile VARCHAR(32),
                status VARCHAR(32) DEFAULT 'invite_sent',
                tech_score NUMERIC(3, 1),
                feedback TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id, job_id) REFERENCES job_descriptions(account_id, job_id) ON DELETE CASCADE
            );
        """))

        conn.commit()
        print("Database tables created successfully with all required updates!")


if __name__ == "__main__":
    init_db()
