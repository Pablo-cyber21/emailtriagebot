# ⚡ Artan Email Triage Bot

An intelligent, self-hosted Python Telegram bot designed to filter, parse, and summarize your inbox using IMAP and the Gemini API, backed by a resilient local whitelist management system.

---

## 🚀 Features

* **Smart Inbox Filtering:** Connects securely via IMAP to check unread emails with flexible date windows (`today`, `week`, `month`)[cite: 2].
* **AI Summarization:** Leverages the Gemini API with exponential backoff retry logic to bypass API traffic spikes and rate limits[cite: 2].
* **Local Whitelist Management:** Instant offline control over rules (`senders`, `domains`, `keywords`) stored locally in `whitelist.json`[cite: 2].
* **Interactive UI:** Inline Telegram action buttons for one-tap whitelisting and alert dismissal[cite: 2].

---

## 🛠️ Command Reference

| Command | Arguments Format | Description |
| :--- | :--- | :--- |
| `/scan` | `[today \| week \| month]` | Scans unread messages and generates AI executive summaries. |
| `/add` | `[sender/domain/keyword] [value]` | Adds a new rule to local whitelist storage[cite: 2]. |
| `/list` | *None* | Displays all active whitelist rules grouped by type[cite: 2]. |
| `/remove` | `[sender/domain/keyword] [value]` | Deletes an existing rule from local storage[cite: 2]. |
| `/testbtn` | *None* | Sends a test message with interactive UI buttons to verify local UI responses[cite: 2]. |

---

## ⚙️ Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd EmailTriage