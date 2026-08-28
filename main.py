import io
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

import PyPDF2
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from passlib.context import CryptContext
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import create_engine, text

load_dotenv()

# --- Config & Security ---
DATABASE_URL = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-change-in-prod-12345")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing in environment variables.")

# Database Engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Password hashing & JWT
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# FastAPI App
app = FastAPI(title="AI Interview ATS Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Anthropic Claude 3 Haiku Model
llm = ChatAnthropic(
    model="claude-haiku-4-5",
    api_key=ANTHROPIC_API_KEY,
    temperature=0
)


# --- Helper Utilities ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    text_content = ""
    for page in reader.pages:
        text_content += (page.extract_text() or "") + "\n"
    return text_content


def get_current_account(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        account_id: str = payload.get("account_id")
        if account_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


BLOCKED_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "zoho.com", "mail.com", "live.com"
}


def validate_company_email(email: str):
    domain = email.split("@")[-1].lower()
    if domain in BLOCKED_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Registration restricted. '{domain}' is a public email provider. Please use your official company mail ID."
        )


def send_activation_email(to_email: str, token: str):
    activation_link = f"http://127.0.0.1:8000/api/auth/verify?token={token}"

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not smtp_host or not smtp_user or not smtp_pass:
        print("\n==============================")
        print(f"ACTIVATION LINK FOR {to_email}:")
        print(activation_link)
        print("==============================\n")
        return

    msg = EmailMessage()
    msg["Subject"] = "Activate Your Kovi.ai Branch Account"
    msg["From"] = smtp_user
    msg["To"] = to_email

    msg.set_content(
        f"Hello,\n\nClick the link below to activate your Kovi.ai branch account:\n{activation_link}\n\nIf you did not request this, please ignore this email.")

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #0b0f19; padding: 30px; color: #f8fafc;">
        <div style="max-width: 500px; background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155;">
          <h2 style="color: #38bdf8; margin-top: 0;">Activate Your Kovi.ai Account</h2>
          <p style="color: #94a3b8; font-size: 14px;">Hello,</p>
          <p style="color: #94a3b8; font-size: 14px;">Thank you for registering your company branch. Please click the button below to verify your email and activate your account:</p>
          <div style="text-align: center; margin: 30px 0;">
            <a href="{activation_link}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">Activate Account</a>
          </div>
          <p style="color: #64748b; font-size: 12px;">If the button doesn't work, copy and paste this link into your browser:</p>
          <p style="color: #38bdf8; font-size: 11px; word-break: break-all;">{activation_link}</p>
        </div>
      </body>
    </html>
    """
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")
        raise HTTPException(status_code=500, detail="Failed to send verification email.")


def send_candidate_invite_email(candidate_email: str, candidate_name: str, job_role: str, interview_type: str, tech_stack: str,
                                company_name: str, branch_name: str, invite_link: str):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    deadline_str = (datetime.now() + timedelta(hours=48)).strftime("%B %d, %Y at %I:%M %p")

    if not smtp_host or not smtp_user or not smtp_pass:
        print("\n==============================")
        print(f"CANDIDATE INTERVIEW LINK FOR {candidate_email}:")
        print(invite_link)
        print("==============================\n")
        return

    msg = EmailMessage()
    msg["Subject"] = f"Interview Invitation: {job_role} at {company_name} ({branch_name})"
    msg["From"] = smtp_user
    msg["To"] = candidate_email

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #0b0f19; padding: 30px; color: #f8fafc; line-height: 1.6;">
        <div style="max-width: 600px; background: #1e293b; padding: 35px; border-radius: 12px; border: 1px solid #334155;">
          <p style="font-size: 15px; color: #f8fafc;">Dear <b>{candidate_name}</b>,</p>

          <p style="font-size: 14px; color: #cbd5e1;">Congratulations on advancing to the next stage of our selection process for the <b>{job_role}</b> position.</p>

          <p style="font-size: 14px; color: #cbd5e1;">To better understand your technical background and problem-solving skills, we invite you to complete a conversational AI-driven technical screening.</p>

          <p style="font-size: 14px; color: #f8fafc; font-weight: bold; margin-top: 20px;">Interview Details:</p>
          <ul style="font-size: 14px; color: #cbd5e1; padding-left: 20px; margin-top: 5px;">
            <li><b>Assessment Type:</b> {interview_type} (AI-driven)</li>
            <li><b>Time Commitment:</b> Approximately 30 minutes</li>
            <li><b>Core Topics:</b> {tech_stack}</li>
          </ul>

          <p style="font-size: 14px; color: #cbd5e1; margin-top: 20px;">Please ensure you complete this assessment no later than <b>{deadline_str}</b> to be considered for the current hiring cycle.</p>

          <p style="font-size: 14px; color: #f8fafc; font-weight: bold; margin-top: 20px;">Preparation Checklist:</p>
          <ul style="font-size: 14px; color: #cbd5e1; padding-left: 20px; margin-top: 5px;">
            <li>Choose a quiet environment with a reliable internet connection.</li>
            <li>Log in from a laptop or desktop computer.</li>
            <li>Ensure your microphone and camera permissions are enabled.</li>
            <li>Prepare to share your screen if prompted during the technical questions.</li>
          </ul>

          <p style="font-size: 14px; color: #cbd5e1; margin-top: 25px;">Access your personalized interview session below:</p>

          <div style="text-align: center; margin: 30px 0;">
            <a href="{invite_link}" style="background-color: #2563eb; color: white; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block; font-size: 14px; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);">Launch Assessment</a>
          </div>

          <p style="font-size: 14px; color: #cbd5e1; margin-top: 20px;">We wish you the best of luck and look forward to reviewing your profile!</p>

          <p style="font-size: 14px; color: #f8fafc; margin-top: 30px; margin-bottom: 0;">Sincerely,<br><b>The {company_name} Hiring Team</b></p>
        </div>
      </body>
    </html>
    """

    plain_text = f"Dear {candidate_name},\n\nCongratulations on advancing to the next stage of our selection process for the {job_role} position.\n\nTo better understand your technical background and problem-solving skills, we invite you to complete a conversational AI-driven technical screening.\n\nFormat: {interview_type}\nCore Topics: {tech_stack}\n\nPlease complete it before: {deadline_str}\n\nLaunch Assessment here: {invite_link}\n\nSincerely,\nThe {company_name} Hiring Team"

    msg.set_content(plain_text)
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            print(f"Successfully sent candidate email to {candidate_email} via {smtp_user}")
    except Exception as e:
        print(f"Failed to send candidate email: {e}")


# ==========================================
# Pydantic Schemas
# ==========================================
class SignUpRequest(BaseModel):
    company_name: str
    branch_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    company_name: str
    branch_name: str
    email: str


class JDParsedData(BaseModel):
    job_id: str = Field(description="A unique job reference code, e.g., 'JD-AI-101'")
    job_role: str = Field(description="The primary job title, e.g., 'Senior AI Engineer'")
    seniority: str = Field(description="Seniority level, e.g., 'Senior / Lead Engineer'")
    interview_type: str = Field(description="Type of interview, e.g., 'Technical Deep Dive'")
    tech_stack: str = Field(description="Comma-separated list of STRICTLY MANDATORY tools and languages. EXCLUDE any skills listed as 'optional', 'bonus', 'nice to have', or 'added advantage'.")
    persona: str = Field(description="Interviewer persona tone, e.g., 'Strict Technical Lead' or 'Friendly Mentor'")
    passing_score: float = Field(description="Minimum passing score out of 10, default to 7.5 if not specified.")
    skills_to_test: str = Field(description="Core competencies or skills to test. EXCLUDE optional/bonus skills.")
    must_questions: str = Field(description="Mandatory technical questions that must be asked during the interview.")


class SaveJDRequest(BaseModel):
    job_id: str
    job_role: str
    seniority: Optional[str] = ""
    interview_type: Optional[str] = ""
    tech_stack: Optional[str] = ""
    interviewer_voice: Optional[str] = "Professional Female (Emma)"
    persona: Optional[str] = "Strict Technical Lead"
    passing_score: Optional[float] = 7.0
    skills_to_test: Optional[str] = ""
    must_questions: Optional[str] = ""


class ResumeParsedData(BaseModel):
    candidate_name: str = Field(description="Full name of the candidate")
    mobile_number: str = Field(description="Contact phone number")
    email_id: str = Field(description="Email address of the candidate")


class AddCandidateRequest(BaseModel):
    candidate_name: str
    email: str
    mobile: str
    interview_type: Optional[str] = ""
    tech_stack: Optional[str] = ""
    persona: Optional[str] = ""
    passing_score: Optional[float] = None
    must_questions: Optional[str] = ""


# ==========================================
# Frontend Page Routing Endpoints
# ==========================================
@app.get("/")
def serve_home():
    return FileResponse("index.html")


@app.get("/auth.html")
def serve_auth():
    return FileResponse("auth.html")


@app.get("/dashboard.html")
def serve_dashboard():
    return FileResponse("dashboard.html")


# ==========================================
# Authentication Endpoints
# ==========================================
@app.post("/api/auth/signup")
def signup(payload: SignUpRequest):
    company = payload.company_name.strip()
    branch = payload.branch_name.strip()
    email = payload.email.strip().lower()

    validate_company_email(email)

    with engine.connect() as conn:
        check_query = text("""
            SELECT account_id FROM branch_accounts 
            WHERE LOWER(company_name) = LOWER(:c) AND LOWER(branch_name) = LOWER(:b)
        """)
        existing = conn.execute(check_query, {"c": company, "b": branch}).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="An account for this company and branch already exists.")

        email_check = conn.execute(text("SELECT account_id FROM branch_accounts WHERE email = :e"),
                                   {"e": email}).fetchone()
        if email_check:
            raise HTTPException(status_code=400, detail="This email address is already registered.")

        hashed_pwd = hash_password(payload.password)
        insert_query = text("""
            INSERT INTO branch_accounts (company_name, branch_name, email, password_hash, is_active)
            VALUES (:c, :b, :e, :p, FALSE)
            RETURNING account_id
        """)
        account_id = conn.execute(insert_query, {"c": company, "b": branch, "e": email, "p": hashed_pwd}).scalar()
        conn.commit()

    token_data = {"account_id": str(account_id), "email": email}
    activation_token = create_access_token(token_data)
    send_activation_email(email, activation_token)

    return {
        "message": "Registration successful! Please check your company email inbox to activate your account before logging in."
    }


@app.get("/api/auth/verify")
def verify_account(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        account_id = payload.get("account_id")
        if not account_id:
            raise HTTPException(status_code=400, detail="Invalid activation token.")

        with engine.connect() as conn:
            conn.execute(
                text("UPDATE branch_accounts SET is_active = TRUE WHERE account_id = :aid"),
                {"aid": account_id}
            )
            conn.commit()

        return {
            "message": "Account activated successfully! You can now close this window and log in to your dashboard."}
    except JWTError:
        raise HTTPException(status_code=400, detail="Activation link is invalid or has expired.")


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    email = payload.email.strip().lower()
    with engine.connect() as conn:
        query = text(
            "SELECT account_id, company_name, branch_name, password_hash, is_active FROM branch_accounts WHERE email = :e")
        user = conn.execute(query, {"e": email}).mappings().fetchone()

        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        if not user["is_active"]:
            raise HTTPException(status_code=403,
                                detail="Account not activated. Please check your company email for the verification link.")

    token_data = {
        "account_id": str(user["account_id"]),
        "company_name": user["company_name"],
        "branch_name": user["branch_name"],
        "email": email
    }
    access_token = create_access_token(token_data)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "company_name": user["company_name"],
        "branch_name": user["branch_name"],
        "email": email
    }


# ==========================================
# AI Document Parsing Endpoints (Claude 3)
# ==========================================
@app.post("/api/parse-jd", response_model=JDParsedData)
async def parse_jd(file: UploadFile = File(...), current_account: dict = Depends(get_current_account)):
    content = await file.read()
    text_content = extract_text_from_pdf(content) if file.filename.endswith(".pdf") else content.decode("utf-8")

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are an expert technical recruiter. Extract the requested job details from the job description. "
         "Generate a sensible short job_id (e.g. JD-101) if not explicitly present. "
         "IMPORTANT: When extracting the tech_stack and skills_to_test, include ONLY mandatory requirements. Completely ignore any skills listed as 'optional', 'added advantage', 'bonus', or 'nice to have'."),
        ("user", "{jd_text}")
    ])
    structured_llm = llm.with_structured_output(JDParsedData)
    chain = prompt | structured_llm
    return chain.invoke({"jd_text": text_content})


@app.post("/api/parse-resume", response_model=ResumeParsedData)
async def parse_resume(file: UploadFile = File(...), current_account: dict = Depends(get_current_account)):
    content = await file.read()
    text_content = extract_text_from_pdf(content) if file.filename.endswith(".pdf") else content.decode("utf-8")

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "Extract the candidate's name, email, and phone number from the resume text. Return an empty string if a field is not found."),
        ("user", "{resume_text}")
    ])
    structured_llm = llm.with_structured_output(ResumeParsedData)
    chain = prompt | structured_llm
    return chain.invoke({"resume_text": text_content})


# ==========================================
# JD & Candidate Database Operations
# ==========================================
@app.post("/api/jobs")
def save_job(payload: SaveJDRequest, current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        query = text("""
            INSERT INTO job_descriptions (job_id, account_id, job_role, seniority, interview_type, tech_stack, interviewer_voice, persona, passing_score, skills_to_test, must_questions)
            VALUES (:jid, :aid, :role, :sen, :itype, :tech, :voice, :persona, :score, :skills, :questions)
            ON CONFLICT (account_id, job_id) 
            DO UPDATE SET 
                job_role = EXCLUDED.job_role,
                seniority = EXCLUDED.seniority,
                interview_type = EXCLUDED.interview_type,
                tech_stack = EXCLUDED.tech_stack,
                interviewer_voice = EXCLUDED.interviewer_voice,
                persona = EXCLUDED.persona,
                passing_score = EXCLUDED.passing_score,
                skills_to_test = EXCLUDED.skills_to_test,
                must_questions = EXCLUDED.must_questions
        """)
        conn.execute(query, {
            "jid": payload.job_id,
            "aid": account_id,
            "role": payload.job_role,
            "sen": payload.seniority,
            "itype": payload.interview_type,
            "tech": payload.tech_stack,
            "voice": payload.interviewer_voice,
            "persona": payload.persona,
            "score": payload.passing_score,
            "skills": payload.skills_to_test,
            "questions": payload.must_questions
        })
        conn.commit()
    return {"message": f"Job {payload.job_id} saved successfully with passing score {payload.passing_score}"}


@app.get("/api/jobs")
def list_jobs(current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        # Added sorting: 'open' status first, then by created_at DESC
        query = text("""
            SELECT j.*, COUNT(c.candidate_id) as total_candidates
            FROM job_descriptions j
            LEFT JOIN candidates c ON j.account_id = c.account_id AND j.job_id = c.job_id
            WHERE j.account_id = :aid
            GROUP BY j.job_id, j.account_id
            ORDER BY 
                CASE WHEN j.status = 'open' THEN 1 ELSE 2 END ASC,
                j.created_at DESC
        """)
        rows = conn.execute(query, {"aid": account_id}).mappings().all()
    return list(rows)


@app.put("/api/jobs/{job_id}/toggle-status")
def toggle_job_status(job_id: str, current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE job_descriptions 
            SET status = CASE WHEN status = 'open' THEN 'closed' ELSE 'open' END
            WHERE account_id = :aid AND job_id = :jid
        """), {"aid": account_id, "jid": job_id})
        conn.commit()
    return {"message": "Job status toggled successfully"}


@app.post("/api/jobs/{job_id}/candidates")
def add_candidate_and_invite(job_id: str, payload: AddCandidateRequest,
                             current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    company_name = current_account["company_name"]
    branch_name = current_account["branch_name"]

    with engine.connect() as conn:
        job = conn.execute(
            text("SELECT job_role, interview_type FROM job_descriptions WHERE account_id = :aid AND job_id = :jid"),
            {"aid": account_id, "jid": job_id}
        ).mappings().fetchone()

        if not job:
            raise HTTPException(status_code=404, detail="Job opening not found.")

        query = text("""
            INSERT INTO candidates (account_id, job_id, candidate_name, email, mobile, status)
            VALUES (:aid, :jid, :name, :email, :mobile, 'invite_sent')
            RETURNING candidate_id
        """)
        cand_id = conn.execute(query, {
            "aid": account_id,
            "jid": job_id,
            "name": payload.candidate_name,
            "email": payload.email,
            "mobile": payload.mobile
        }).scalar()
        conn.commit()

    invite_link = f"http://127.0.0.1:8000/interview.html?candidate_id={cand_id}&job_id={job_id}"

    send_candidate_invite_email(
        candidate_email=payload.email,
        candidate_name=payload.candidate_name,
        job_role=job["job_role"],
        interview_type=payload.interview_type or job["interview_type"] or "Technical Deep Dive",
        tech_stack=payload.tech_stack or "General Technical Evaluation",
        company_name=company_name,
        branch_name=branch_name,
        invite_link=invite_link
    )

    return {"candidate_id": cand_id, "message": "Candidate added and invitation email sent successfully!"}


@app.get("/api/jobs/{job_id}/candidates")
def get_job_candidates(job_id: str, current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        # Added sorting: 'invite_sent' status first, then by created_at DESC
        query = text("""
            SELECT * FROM candidates 
            WHERE account_id = :aid AND job_id = :jid
            ORDER BY 
                CASE WHEN status = 'invite_sent' THEN 1 ELSE 2 END ASC,
                created_at DESC
        """)
        rows = conn.execute(query, {"aid": account_id, "jid": job_id}).mappings().all()
    return list(rows)