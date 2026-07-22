"""
One isolated Playwright browser context per account, on that account's proxy.

Built in Phase 2. Not implemented yet.

HARD RULE: contexts and proxy IPs never mix between accounts. Each account is
permanently assigned one dedicated sticky residential IP. Rotating proxies are
never used -- a new IP per request looks like a bot.
"""
