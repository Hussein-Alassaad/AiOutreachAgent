"""
Detects account problems and records warning TYPE plus specific REASON.

Built in Phase 2. Not implemented yet.

Warnings must be specific ('CAPTCHA triggered', 'Login challenge detected'),
never generic. On warning: pause the account and set the redistribute flag for
Hussein to decide. Quota is NEVER redistributed automatically.
"""
