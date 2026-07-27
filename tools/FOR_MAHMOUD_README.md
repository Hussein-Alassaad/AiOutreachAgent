# For Mahmoud — logging into your accounts safely

This lets you log into your LinkedIn/Instagram accounts yourself, and only
send back a small file that proves you're logged in — **never your password,
never your 2-step verification codes.**

## What you need

1. A computer with Python installed.
2. This file (`capture_session.py`) — Hussein sent it to you alongside this doc.

## One-time setup

Open a terminal (Command Prompt on Windows, Terminal on Mac) and run:

```
pip install playwright
playwright install chromium
```

That installs the tool this script uses to open a browser. Only needs doing once.

## Every time you log into an account

1. Run:
   ```
   python capture_session.py
   ```
2. It'll ask you two quick questions:
   - **Which platform?** — type `1` for LinkedIn or `2` for Instagram
   - **Which account is this?** — type the label Hussein gave you (e.g. `Account 1`)
3. A real browser window opens to the login page. **Log in exactly as you
   normally would** — your username, your password, your 2-step code if you
   have one. This is the real LinkedIn/Instagram site — nothing about this
   step is different from logging in normally.
4. Once you can see your normal feed/home page (fully logged in), go back to
   the terminal window and press **Enter**.
5. It'll print something like:
   ```
   Done! Session saved to:
   captured_sessions/linkedin_Account_1.json
   ```
6. **Send that one file to Hussein** (via WhatsApp, email, whatever's easiest).

Repeat steps 1–6 for each of your 6 accounts (3 LinkedIn + 3 Instagram).

## Why this is safe

The file it creates only contains a "you're logged in" token, similar to how
a website keeps you logged in without asking for your password every time
you open it. It does **not** contain your password or your 2-step
verification method. If you're ever unsure, you're welcome to ask Hussein to
show you the file's contents before sending — it'll just look like technical
gibberish (cookies and tokens), not anything resembling a password.

## Questions?

Ask Hussein — he can walk you through the first one together if that's easier.
