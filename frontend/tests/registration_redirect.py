"""Check that successful registration opens login while invalid input stays put."""
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))

source = Path("app.py").read_text(encoding="utf-8").split(
    'if st.session_state.get("clear_browser_token"):', 1
)[0]
page = source + "\nregistration_page()\n"


def fill(app, confirmation):
    app.text_input[0].set_value("redirect_" + uuid4().hex[:12])
    app.text_input[1].set_value("Registration123!")
    app.text_input[2].set_value(confirmation)


invalid = AppTest.from_string(page).run()
fill(invalid, "different")
with patch("streamlit.switch_page") as switch:
    invalid.button[0].click().run()
assert not invalid.exception
assert invalid.error
switch.assert_not_called()

valid = AppTest.from_string(page).run()
fill(valid, "Registration123!")
with patch("streamlit.switch_page") as switch:
    valid.button[0].click().run()
assert not valid.exception
switch.assert_called_once()
assert switch.call_args.args[0].title == "登入"
print("PASS: successful registration switches to login; mismatched passwords stay on registration")
