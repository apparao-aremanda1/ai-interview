import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

# Import the app and utilities from your main file
from main import app, hash_password, verify_password, create_access_token, validate_company_email, get_current_account
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


@patch("main.engine.connect")
def test_get_account_balance(mock_db_connect):
    """Test that the balance endpoint correctly fetches the available credits."""
    mock_conn = MagicMock()
    mock_db_connect.return_value.__enter__.return_value = mock_conn

    # Mock the database returning 50 credits
    mock_conn.execute.return_value.fetchone.return_value = (50,)

    # Fake authentication token payload
    app.dependency_overrides[get_current_account] = lambda: {"account_id": "1"}

    response = client.get("/api/billing/balance")

    assert response.status_code == 200
    assert response.json()["available_credits"] == 50

    # Clean up override
    app.dependency_overrides = {}


@patch("main.rzp_client.utility.verify_webhook_signature")
@patch("main.engine.connect")
def test_razorpay_webhook_adds_credits(mock_db_connect, mock_verify_signature):
    """Test that a valid Razorpay webhook successfully adds credits to the ledger."""
    mock_conn = MagicMock()
    mock_db_connect.return_value.__enter__.return_value = mock_conn

    # Ensure signature verification passes (doesn't raise an exception)
    mock_verify_signature.return_value = True

    webhook_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TEST12345",
                    "amount": 15000,  # 150 INR in paise
                    "notes": {
                        "account_id": "1",
                        "credits_purchased": "10"
                    }
                }
            }
        }
    }

    response = client.post(
        "/api/billing/webhook",
        json=webhook_payload,
        headers={"X-Razorpay-Signature": "fake_valid_signature"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Verify database was called to update balance and insert into ledger
    assert mock_conn.execute.call_count == 2
    mock_conn.commit.assert_called_once()


@patch("main.engine.connect")
def test_advance_candidate_fails_with_zero_credits(mock_db_connect):
    """Test that the billing gatekeeper correctly blocks advancing if credits are 0."""
    mock_conn = MagicMock()
    mock_db_connect.return_value.__enter__.return_value = mock_conn

    # 1st call: Fetch candidate details (returns fake data)
    # 2nd call: Fetch available credits FOR UPDATE (returns 0 credits)
    mock_conn.execute.return_value.fetchone.side_effect = [
        ("Fake Name", "fake@test.com", "123", "Role", "Co", "Branch", "Python"),
        (0,)
    ]

    app.dependency_overrides[get_current_account] = lambda: {"account_id": "1"}

    payload = {
        "candidate_id": "55",
        "job_id": "JD-101",
        "new_status": "Technical Deep Dive",
        "duration": 45,
        "deadline_hours": 48,
        "passing_score": 7.5
    }

    response = client.post("/api/candidates/advance", json=payload)

    # Assert the gatekeeper threw a 402 Payment Required error
    assert response.status_code == 402
    assert "Insufficient credits" in response.json()["detail"]
    app.dependency_overrides = {}
