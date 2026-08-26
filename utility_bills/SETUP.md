# Gmail API Setup for Utility Bill Scraper

## Quick Setup (5 minutes)

### 1. Create Google Cloud Project
1. Go to: https://console.cloud.google.com/
2. Click "Select a project" → "New Project"
3. Name it: "Utility Bill Scraper" (or anything)
4. Click "Create"

### 2. Enable Gmail API
1. In the left menu: **APIs & Services** → **Library**
2. Search "Gmail API"
3. Click **Gmail API** → **Enable**

### 3. Create OAuth Credentials
1. Left menu: **APIs & Services** → **Credentials**
2. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
3. If prompted, configure **OAuth consent screen**:
   - User Type: **External**
   - App name: "Utility Bill Scraper"
   - User support email: your email
   - Developer contact: your email
   - Save & Continue through scopes (no changes needed)
   - Add test user: `timothyroessel@gmail.com`
4. Back to **Credentials** → **+ CREATE CREDENTIALS** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: "Utility Bill Scraper"
   - Click **Create**
5. **Download JSON** → Save as `credentials.json` in:
   ```
   C:\Users\timot\OneDrive\Documents\VERITAS\utility_bills\credentials.json
   ```

### 4. Run the Scraper
```bash
cd C:\Users\timot\OneDrive\Documents\VERITAS\utility_bills
python scrape_bills.py
```

### 5. First Run - Authorize
- Browser opens → Sign in with `timothyroessel@gmail.com`
- Click "Continue" on "Google hasn't verified this app" warning
- Grant Gmail read access
- Token saved to `token.pickle` (no re-auth needed next time)

---

## Alternative: App Password (Simpler, No Google Cloud)

If you prefer not to use Google Cloud Console:

1. **Enable 2FA** on your Google account (required)
2. Go to: https://myaccount.google.com/apppasswords
3. Select app: **Mail** → Device: **Windows Computer**
4. Copy the **16-character password** (e.g., `abcd efgh ijkl mnop`)

Then I'll give you an IMAP-based script that uses this app password instead of OAuth.

---

## Which do you prefer?
- **OAuth (above)** - More secure, standard, works indefinitely
- **App Password** - Quicker setup, but Google may deprecate

Let me know and I'll adjust the script accordingly!