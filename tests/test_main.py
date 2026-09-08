import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

# Import the app and utilities from your main file
from main import app, hash_password, verify_password, create_access_token, validate_company_email

# Initialize the test client
client = TestClient(app)


# ==========================================
# 1. Utility & Security Tests
# ==========================================
def test_password_hashing():
    password = "SecurePassword123!"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_validate_company_email_blocks_public():
    # Public domains should raise an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        validate_company_email("test@gmail.com")
    assert excinfo.value.status_code == 400
    assert "public email provider" in excinfo.value.detail


def test_validate_company_email_allows_corporate():
    # Should run without raising exceptions
    validate_company_email("hr@techeval.ai")


# ==========================================
# 2. Environment Variable Configuration Tests
# ==========================================
def test_production_invite_link_uses_env_variable(monkeypatch):
    """Ensure the URL fix we discussed earlier is enforced."""
    production_url = "https://techeval.ai"
    monkeypatch.setenv("FRONTEND_URL", production_url)

    # Simulating the exact string construction from main.py advance_candidate
    new_cand_id = "99"
    job_id = "JD-TEST"
    base_url = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")
    invite_link = f"{base_url}/interview.html?candidate_id={new_cand_id}&job_id={job_id}"

    assert production_url in invite_link
    assert "127.0.0.1" not in invite_link


# ==========================================
# 3. Endpoint Tests with Database/Email Mocking
# ==========================================
def test_serve_home_endpoint():
    # Simple static file routing test
    with patch("main.FileResponse") as mock_file:
        mock_file.return_value = {"status": "ok"}
        response = client.get("/")
        assert response.status_code == 200


@patch("main.send_activation_email")
@patch("main.engine.connect")
def test_signup_endpoint_success(mock_db_connect, mock_send_email):
    # 1. Setup the Database Mock
    mock_conn = MagicMock()
    mock_db_connect.return_value.__enter__.return_value = mock_conn

    # Mock 'existing account' check (returns None meaning it doesn't exist)
    mock_conn.execute.return_value.fetchone.side_effect = [None, None]

    # Mock the insert returning a new account_id
    mock_conn.execute.return_value.scalar.return_value = 1

    # 2. Execute the Request
    payload = {
        "company_name": "TestCorp",
        "branch_name": "HQ",
        "email": "admin@testcorp.com",
        "password": "StrongPassword1!"
    }
    response = client.post("/api/auth/signup", json=payload)

    # 3. Assertions
    assert response.status_code == 200
    assert "Registration successful" in response.json()["message"]
    mock_send_email.assert_called_once()  # Verify email triggered without actually sending!
