# Quick Start

Untuk yang sudah tahu cara pakai terminal dan Python.

```bash
git clone https://github.com/wildanrivky/sohibbot.git
cd sohibbot
python setup.py
```

Ikuti wizard → bot langsung jalan.

## Verifikasi

```bash
# Lihat log
tail -f /tmp/{{BOT_SLUG}}.error.log

# Status service (Mac)
launchctl list | grep {{SERVICE_LABEL}}

# Status service (Linux)
systemctl --user status {{BOT_SLUG}}.service
```

## Bikin agent baru

```bash
python scripts/create_agent.py --type=basic --name=nama-agent --desc="Deskripsi agent"
```

## Jalankan manual (tanpa service)

```bash
# Mac/Linux
scripts/start_bot.sh

# Windows
scripts\start_bot.bat

# Atau langsung
.venv/bin/python scripts/claude_telegram.py
```

## Self-fix bot rusak

Kirim ke Telegram bot: `halu` (dengan deskripsi singkat masalah)

Bot akan otomatis: baca log → identifikasi bug → perbaiki → restart.
