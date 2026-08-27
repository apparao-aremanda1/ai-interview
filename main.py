import os
import io
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from passlib.context import CryptContext
from jose import jwt, JWTError

import PyPDF2
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

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
    model="anthropic/claude-haiku-4-5",
    api_key=ANTHROPIC_API_KEY,
    temperature=0
)


# --- Pydantic Schemas ---
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
    job_id: str = Field(description="A unique job reference code, e.g., 'JD-101' or 'REQ-9042'. If missing in text, create a sensible short code.")
    job_role: str = Field(description="The primary job title, e.g., 'Senior Backend Engineer'")
    seniority: str = Field(description="Seniority level, e.g., 'Software Engineer (SE)', 'Senior Software Engineer (SSE)', or 'Lead Engineer'")
    interview_type: str = Field(description="Type of interview, e.g., 'Technical Deep Dive' or 'System Design'")
    tech_stack: str = Field(description="Comma-separated list of required tools, frameworks, and languages.")

class ResumeParsedData(BaseModel):
    candidate_name: str = Field(description="Full name of the candidate")
    mobile_number: str = Field(description="Contact phone number")
    email_id: str = Field(description="Email address of the candidate")

class SaveJDRequest(BaseModel):
    job_id: str
    job_role: str
    seniority: Optional[str] = ""
    interview_type: Optional[str] = ""
    tech_stack: Optional[str] = ""
    interviewer_voice: Optional[str] = "Professional Female (Emma)"


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


# ==========================================
# Authentication Endpoints
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

@app.post("/api/auth/signup", response_model=TokenResponse)
def signup(payload: SignUpRequest):
    company = payload.company_name.strip()
    branch = payload.branch_name.strip()
    email = payload.email.strip().lower()

    with engine.connect() as conn:
        # Check if Company + Branch already exists
        check_query = text("""
            SELECT account_id FROM branch_accounts 
            WHERE LOWER(company_name) = LOWER(:c) AND LOWER(branch_name) = LOWER(:b)
        """)
        existing = conn.execute(check_query, {"c": company, "b": branch}).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="An account for this company and branch already exists.")

        # Check if email is already registered
        email_check = conn.execute(text("SELECT account_id FROM branch_accounts WHERE email = :e"), {"e": email}).fetchone()
        if email_check:
            raise HTTPException(status_code=400, detail="This email address is already registered.")

        # Insert new branch account
        hashed_pwd = hash_password(payload.password)
        insert_query = text("""
            INSERT INTO branch_accounts (company_name, branch_name, email, password_hash)
            VALUES (:c, :b, :e, :p)
            RETURNING account_id
        """)
        account_id = conn.execute(insert_query, {"c": company, "b": branch, "e": email, "p": hashed_pwd}).scalar()
        conn.commit()

    token_data = {"account_id": str(account_id), "company_name": company, "branch_name": branch, "email": email}
    access_token = create_access_token(token_data)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "company_name": company,
        "branch_name": branch,
        "email": email
    }

@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    email = payload.email.strip().lower()
    with engine.connect() as conn:
        query = text("SELECT account_id, company_name, branch_name, password_hash FROM branch_accounts WHERE email = :e")
        user = conn.execute(query, {"e": email}).mappings().fetchone()

        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

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
        ("system", "You are an expert technical recruiter. Extract the requested job details from the job description. Generate a sensible short job_id (e.g. JD-101) if not explicitly present."),
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
        ("system", "Extract the candidate's name, email, and phone number from the resume text. Return an empty string if a field is not found."),
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
            INSERT INTO job_descriptions (job_id, account_id, job_role, seniority, interview_type, tech_stack, interviewer_voice)
            VALUES (:jid, :aid, :role, :sen, :itype, :tech, :voice)
            ON CONFLICT (account_id, job_id) 
            DO UPDATE SET 
                job_role = EXCLUDED.job_role,
                seniority = EXCLUDED.seniority,
                interview_type = EXCLUDED.interview_type,
                tech_stack = EXCLUDED.tech_stack,
                interviewer_voice = EXCLUDED.interviewer_voice
        """)
        conn.execute(query, {
            "jid": payload.job_id,
            "aid": account_id,
            "role": payload.job_role,
            "sen": payload.seniority,
            "itype": payload.interview_type,
            "tech": payload.tech_stack,
            "voice": payload.interviewer_voice
        })
        conn.commit()
    return {"message": f"Job {payload.job_id} saved successfully"}

@app.get("/api/jobs")
def list_jobs(current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        query = text("""
            SELECT j.*, COUNT(c.candidate_id) as total_candidates
            FROM job_descriptions j
            LEFT JOIN candidates c ON j.account_id = c.account_id AND j.job_id = c.job_id
            WHERE j.account_id = :aid
            GROUP BY j.job_id, j.account_id
            ORDER BY j.created_at DESC
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
def add_candidate(job_id: str, payload: ResumeParsedData, current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        query = text("""
            INSERT INTO candidates (account_id, job_id, candidate_name, email, mobile, status)
            VALUES (:aid, :jid, :name, :email, :mobile, 'invite_sent')
            RETURNING candidate_id
        """)
        cand_id = conn.execute(query, {
            "aid": account_id,
            "jid": job_id,
            "name": payload.candidate_name,
            "email": payload.email_id,
            "mobile": payload.mobile_number
        }).scalar()
        conn.commit()
    return {"candidate_id": cand_id, "message": "Candidate added and invite queued"}

@app.get("/api/jobs/{job_id}/candidates")
def get_job_candidates(job_id: str, current_account: dict = Depends(get_current_account)):
    account_id = current_account["account_id"]
    with engine.connect() as conn:
        query = text("""
            SELECT * FROM candidates 
            WHERE account_id = :aid AND job_id = :jid
            ORDER BY created_at DESC
        """)
        rows = conn.execute(query, {"aid": account_id, "jid": job_id}).mappings().all()
    return list(rows)
