# Troubleshooting

## Error umum

| Error | Penyebab | Solusi |
|---|---|---|
| `claude: command not found` | Claude CLI belum di PATH | Install ulang Claude Code, restart terminal |
| `python: command not found` | Python belum terinstall | Install Python 3.11+ sesuai OS |
| `tmux: command not found` (Mac/Linux) | tmux belum ada | Mac: `brew install tmux` / Linux: `sudo apt install tmux` |
| Bot tidak balas pesan | Service mati atau OWNER_ID salah | Cek log, cek service |
| `OWNER_ID mismatch` di log | User ID salah | Chat @userinfobot, copy ID ke `.env`, restart |
| `Invalid token` di Telegram | Token salah atau expired | Buka @BotFather → /mybots → copy token baru ke `.env` |
| Bot lambat balas (>30 detik) | Claude Code sedang berpikir | Normal untuk pesan panjang. >2 menit = cek log |
| `Python version too low` | Python <3.11 | Upgrade Python, jangan pakai versi sistem |
| Wizard stuck di step Claude CLI | Belum login | Jalankan `claude` manual sekali → ikuti OAuth |

## Cek log

```bash
# Mac/Linux
tail -f /tmp/{{BOT_SLUG}}.error.log

# Windows
type %AppData%\Local\{{BOT_SLUG}}\{{BOT_SLUG}}.error.log
```

## Cek status service

```bash
# Mac
launchctl list | grep {{SERVICE_LABEL}}

# Linux
systemctl --user status {{BOT_SLUG}}.service

# Windows
schtasks /query /tn {{BOT_SLUG}}
```

## Restart manual

```bash
# Mac
launchctl stop {{SERVICE_LABEL}}
launchctl start {{SERVICE_LABEL}}

# Linux
systemctl --user restart {{BOT_SLUG}}.service

# Windows
schtasks /end /tn {{BOT_SLUG}}
schtasks /run /tn {{BOT_SLUG}}
```

## Bot bocor token di Git (DARURAT)

Kalau kamu tidak sengaja commit `.env` ke Git:

1. **Rotate token segera**: @BotFather → `/mybots` → pilih bot → `/revoke`
2. Update `.env` dengan token baru
3. Hapus commit yang mengandung secret:
   ```bash
   git filter-repo --path .env --invert-paths
   git push --force
   ```
4. Pastikan `.env` ada di `.gitignore`

## Reset setup (mulai ulang dari awal)

```bash
rm .setup_progress.json .env CLAUDE.md
rm -rf .venv
python setup.py
```

## Self-fix otomatis

Kalau bot berperilaku aneh, kirim pesan ke bot:

```
halu [deskripsi singkat masalah]
```

Contoh: `halu bot tidak balas sama sekali`

Bot akan otomatis: baca error log → identifikasi bug → perbaiki kode → restart.
Timeout 5 menit. Kalau gagal, debug manual pakai langkah di atas.
