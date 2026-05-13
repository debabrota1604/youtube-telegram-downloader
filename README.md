# 🎬 YouTube Telegram Downloader

**Send YouTube links from your phone via Telegram → Download & convert on your Mac → Receive the file back on your phone.**

A Python-based YouTube downloader and Telegram bot that converts videos/audio to formats optimized for **Android Auto** playback, with full remote control from your phone.

## ✨ Features

- **CLI Downloader** — Download YouTube videos/audio with configurable format, quality, and bitrate using `ffmpeg`
- **Playlist Download** — Download entire YouTube playlists with a single command (CLI + Bot)
- **Telegram Bot** — Send YouTube links from any device, receive converted files back via Telegram
- **Android Auto Optimized** — `--android-auto` flag for MP4/H.264+AAC (video) or M4A/AAC (audio) formats
- **Multiple Formats** — MP4, MKV, WebM, AVI for video; MP3, FLAC, M4A, AAC, OGG for audio
- **Per-User Settings** — Each bot user gets their own quality/format preferences
- **User Access Control** — Restrict bot to specific Telegram user IDs

### 🆕 New Features

- **📸 Preview Before Download** — See the video thumbnail, title, duration, uploader, and estimated file size before committing to a download
- **📊 Real-Time Progress Tracking** — Visual progress bar with download speed, ETA, and processing time
- **🛑 Cancel Downloads** — Stop any download mid-process with the Cancel button or `/cancel` command
- **📋 Download Queue** — Queue multiple links while one is downloading; they process automatically in order
- **🏷️ Auto-Embed Metadata** — Title, artist, year, and album artwork are embedded into audio files for proper display in Android Auto music players

## 📁 Project Structure

```
youtube-telegram-downloader/
├── main.py                  # CLI downloader (ffmpeg + yt-dlp)
├── youtube_telegram_bot.py  # Telegram bot
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variables template
├── .env                     # Your actual config (git-ignored)
└── README.md
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
# System dependencies (macOS)
brew install ffmpeg

# Python packages
pip install -r requirements.txt
```

### 2. CLI Downloader (Standalone)

```bash
# Download video at 1080p (default MP4/H.265+AAC)
python3 main.py https://www.youtube.com/watch?v=VIDEO_ID

# Android Auto optimized
python3 main.py https://www.youtube.com/watch?v=VIDEO_ID --android-auto

# Audio only as M4A
python3 main.py https://www.youtube.com/watch?v=VIDEO_ID --audio-only --audio-format m4a

# Custom format and quality
python3 main.py https://www.youtube.com/watch?v=VIDEO_ID --format mkv --video-quality 720p

# Download entire playlist (auto-detected)
python3 main.py https://www.youtube.com/playlist?list=PLAYLIST_ID

# Download playlist as audio only
python3 main.py https://www.youtube.com/playlist?list=PLAYLIST_ID --audio-only --audio-format m4a
```

### 3. Telegram Bot Setup

```bash
# 1. Create a bot on Telegram via @BotFather, get the token
# 2. Copy and configure .env
cp .env.example .env
# Edit .env with your BOT_TOKEN

# 3. Run the bot (must run on your Mac)
python3 youtube_telegram_bot.py
```

### 4. Telegram Bot Usage

Send these commands to your bot from any device:

| Command | Description |
|---------|-------------|
| `/start` | Start the bot, see your Chat ID |
| `/video` | Set mode to video download (MP4, 1080p) |
| `/audio` | Set mode to audio only (M4A, 256k) |
| `/quality 720p` | Set video quality (360p–2160p) |
| `/bitrate 320k` | Set audio bitrate (128k–320k) |
| `/format mp3` | Set output format |
| `/playlist` | Set mode to playlist download |
| `/settings` | Show current settings |
| `/queue` | Show download queue |
| `/queue-clear` | Clear download queue |
| `/cancel` | Cancel current download |
| `/help` | Show all commands |

**Or just paste a YouTube link** and the bot will show a preview before downloading using your last-used mode.
**Playlist URLs are auto-detected** — send a playlist URL and all videos will be downloaded and sent individually.

## 🆕 Feature Details

### 📸 Preview Before Download

When you send a YouTube link, the bot shows:
- Video thumbnail image
- Title, uploader, duration, and view count
- Estimated file size based on your current settings
- ✅ Download / ❌ Cancel buttons

This prevents accidental wrong downloads and lets you verify the content before waiting.

### 📊 Real-Time Progress Tracking

During downloads, the bot shows a live-updating progress message:
- **Visual progress bar** (▓▓▓▓▓▓▓▓░░) with percentage
- **Download speed** (e.g., 12.45MiB/s)
- **ETA** (estimated time remaining)
- **Processing time** during ffmpeg conversion
- Updates every 1-2 seconds for smooth tracking

### 🛑 Cancel Downloads

Stop any download mid-process:
- **Inline button** — A "❌ Cancel" button appears below every progress message
- **Command** — Send `/cancel` to stop the current download
- The bot will gracefully terminate the download and clean up temporary files

### 📋 Download Queue

Queue multiple downloads while one is in progress:
- Send a YouTube link while another is downloading → it's automatically added to the queue
- Queue processes items in order after the current download finishes
- **`/queue`** — View queued items with their positions
- **`/queue-clear`** — Remove all queued items
- Max queue size: 20 items per user
- Each queued item gets metadata embedding and progress tracking

### 🏷️ Auto-Embed Metadata

Audio files automatically get embedded metadata for proper display in Android Auto:
- **Title** — From the YouTube video title
- **Artist** — From the YouTube channel/uploader name
- **Year** — From the upload date
- **Album Artwork** — Downloaded from YouTube's thumbnail

Supported formats: MP3 (ID3v2.3), M4A/AAC (MP4 atoms), FLAC (Vorbis comments)

> Requires `mutagen` package (included in `requirements.txt`). If not installed, metadata embedding is silently skipped.

## ⚙️ Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `ALLOWED_USERS` | No | Comma-separated Telegram user IDs (empty = allow all) |

### CLI Options (`main.py`)

| Flag | Choices | Default | Description |
|------|---------|---------|-------------|
| `--format` | mp4, mkv, webm, avi | mp4 | Video container format |
| `--video-quality` | 144p–4320p | 1080p | Max video resolution |
| `--audio-format` | mp3, flac, m4a, aac, ogg | m4a | Audio output format |
| `--audio-bitrate` | 64k–320k | 192k | Audio bitrate |
| `--audio-only` | — | — | Download audio only |
| `--playlist` | — | — | Download entire playlist (auto-detected) |
| `--android-auto` | — | — | Optimize for Android Auto |
| `--output`, `-o` | any path | ~/Downloads | Output folder |

## 🚗 Android Auto Format Guide

For the best compatibility on Android head units:

| Mode | Format | Codec | Quality |
|------|--------|-------|---------|
| Video | MP4 | H.264 + AAC | 1080p, 192k audio |
| Audio | M4A | AAC | 256k |

Use `--android-auto` flag to apply these settings automatically.

## 🛠️ Tech Stack

- **Python 3** — Runtime
- **ffmpeg** — Video/audio download and conversion
- **yt-dlp** — YouTube stream URL extraction
- **python-telegram-bot** — Telegram Bot API
- **python-dotenv** — Environment variable management
- **mutagen** — Audio metadata embedding (ID3, MP4 atoms, Vorbis comments)

## 📄 License

MIT

## 🙋 FAQ

**Q: Can I use this without Telegram?**
A: Yes, `main.py` works standalone as a CLI tool.

**Q: What's the max file size on Telegram?**
A: 2GB per file (Telegram's limit).

**Q: Can I run the bot on a VPS instead of my Mac?**
A: Yes, just install ffmpeg and Python dependencies on the VPS.

**Q: Why MP4/H.264 for Android Auto?**
A: Most Android head units natively support H.264/AAC in MP4 containers. Formats like VP9, HEVC, MKV, and WebM are often unsupported.

**Q: Can I download an entire YouTube playlist?**
A: Yes! Send a playlist URL to the bot (auto-detected) or use `--playlist` flag in CLI. Each video is downloaded and sent individually via Telegram.

**Q: What happens if a video in the playlist fails to download?**
A: The bot continues downloading the rest of the playlist and shows a summary of successful/failed downloads at the end.

**Q: How does the download queue work?**
A: When you send a YouTube link while another download is in progress, it's added to a queue (max 20 items). After the current download finishes, queued items are processed one by one automatically.

**Q: Why is metadata embedding important for Android Auto?**
A: Without embedded metadata, Android Auto shows generic filenames like `dQw4w9WgXcQ.m4a`. With metadata, it shows "Song Title — Artist Name" with album artwork, making it look like a proper music library.

**Q: Can I cancel a long playlist download?**
A: Yes! Use the ❌ Cancel button below the progress message or send `/cancel`. The bot will stop the current download and clean up.
