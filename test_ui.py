import pytest
from playwright.sync_api import Page, expect

'''
Steps:
  1) python -m http.server 8000  (in one terminal)
  2) pytest .\test_ui.py   (in another terminal)
'''
# 1. Inject launch arguments to mock the camera, mic, and screen share
@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "args": [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream"
        ]
    }


def test_interview_setup_screen(page: Page):
    # 2. Load your local HTML file
    page.goto("http://127.0.0.1:8000/interview.html?candidate_id=123&job_id=456")

    # 3. Assert the correct headers are visible
    expect(page.locator("text=Interview Setup")).to_be_visible()

    # 4. Simulate a user clicking the permission button
    page.click("button#requestBtn")

    # 5. Assert the Start button appears after permissions are granted
    expect(page.locator("button#startBtn")).to_be_enabled()
