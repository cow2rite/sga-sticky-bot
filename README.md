# SGA Coach Discord Sticky Announcement

A lightweight scheduled Discord webhook repost for **one designated channel** (`1518960183500476591`). The program checks only the latest message ID using the SGA bot token, then reposts the configured red embed **only when a newer message exists**. No Discord Gateway connection, Python installation on a local PC, or `Message Content Intent` is required.

## Configure (GitHub → Settings → Secrets and variables → Actions → New repository secret)

Add **three repository secrets**:

- `DISCORD_BOT_TOKEN`: the Discord application bot token. Keep private.
- `DISCORD_WEBHOOK_URL`: a Discord webhook URL **pointing to the target channel**, with the desired sender name and avatar.
- `STICKY_DESCRIPTION`: the full Markdown content for the embed body, including the private Discord announcement links. Do not commit it in this public repository.

The embed title, red sidebar color and footer text are provided in `sticky.py`.

## Start and monitor

In the repository's **Actions** tab, choose **SGA sticky announcement** → **Run workflow**. The first successful run creates a new sticky post. After that, GitHub Actions checks for new messages at roughly five-minute intervals and moves the managed sticky to the bottom as needed. Actual scheduled execution can be delayed or skipped.

**Important:** The first run creates a new announcement. Any old Discohook announcement is **not** automatically removed. Verify the new post, then manually delete or unpin any duplicate old post. The program only deletes messages it previously created itself.

## Stop

Repository **Actions** → **SGA sticky announcement** → **Disable workflow**. The last posted message remains unless deleted manually.

## Operational notes

- The GitHub repository is public. **Never commit bot tokens, webhook URLs, or private Discord message links**. Keep these in Actions secrets.
- The code uses GitHub commits to persist only the latest auto-posted message ID in `state.json`. No private message content is stored in the repository.
- The webhook URL must belong to the designated channel; the program validates this before posting.
- The bot needs channel access to read `last_message_id`. It does not need `Message Content Intent`.
- This **is not instantaneous**. GitHub's scheduled workflows support a minimum five-minute interval, may run late, and can be automatically disabled in public repositories after 60 days of repository inactivity.
- The automation is free on public-repository standard hosted runners under GitHub's current free public repository policies, subject to GitHub policies and usage limits.
