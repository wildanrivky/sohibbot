# ONBOARDING — Panduan Lengkap SohibBot

Panduan ini ditulis untuk yang **belum pernah coding sama sekali**.
Ikuti dari atas ke bawah, jangan skip.

---

## 1. Apa itu ini?

**Bot ini bukan seperti ChatGPT.**

Bedanya:
- **ChatGPT** jalan di server OpenAI. Selalu nyala 24/7. Tapi tiap chat baru, dia lupa segalanya.
- **Bot kamu** jalan di laptopmu sendiri. Otaknya Claude Code (sudah login pakai akun Pro/Max kamu). Bot punya akses penuh ke file di laptopmu — bisa baca, edit, jalankan apa saja. Dia juga punya **memory persisten** (ingat semua antara sesi).

**Konsekuensinya:**
- Bot hanya hidup kalau laptopmu **nyala dan tidak sleep**
- Tapi imbalannya: bot tahu konteksmu, bisa edit file, bisa kontrol browser, bahkan bisa perbaiki kode dirinya sendiri kalau rusak

**Cara pakai sehari-hari:**
- Buka Telegram → chat bot kamu seperti chat teman biasa
- Bot teruskan pesanmu ke Claude Code di laptopmu, balas via Telegram
- Bot ingat 20 pesan terakhir per-chat (short-term memory)
- Bot juga tulis catatan jangka panjang ke folder `memory/` (long-term memory)
- Makin lama dipakai, bot makin paham preferensi & ritme kamu

---

## 2. Apa yang kamu butuhkan

**Wajib punya:**

| Item | Kenapa | Cara dapat |
|---|---|---|
| Laptop Mac/Linux/Windows | Tempat bot hidup | Punya sendiri |
| Internet stabil | Bot ngobrol via Telegram + Claude API | ISP |
| Akun Telegram | Chat dengan bot | Install app Telegram, daftar pakai nomor HP |
| Akun **Claude Pro/Max** | Otak bot (Claude Code) | claude.ai → upgrade ke Pro ($20/bulan) |
| 30 menit waktu setup | Jalankan wizard, ikuti panduan | Sediakan |

**TIDAK perlu:**
- Anthropic API key (yang bayar per-token)
- Kartu kredit khusus API (sudah include di subscription Claude Pro)
- Skill coding/programming
- Pengalaman terminal/command line (wizard yang handle)
- Server/VPS (jalan di laptop sendiri)

> **Catatan:** Akun Claude **Free** tidak cukup. Claude Code CLI butuh subscription Pro minimal.

---

## 3. Step 1 — Install Claude Code CLI

Claude Code adalah otak dari bot ini. Kamu perlu install dulu.

1. Buka browser, pergi ke: `https://docs.anthropic.com/en/docs/claude-code/quickstart`
2. Ikuti panduan install sesuai OS kamu
3. Setelah install, buka Terminal (Mac/Linux) atau Command Prompt (Windows)
4. Ketik: `claude --version`
5. Kalau muncul angka versi (contoh: `claude 1.x.x`), berarti berhasil
6. Kalau diminta login, ikuti panduan OAuth yang muncul

**Verifikasi:** Ketik `claude "halo"` di terminal. Kalau Claude menjawab, berarti berhasil.

---

## 4. Step 2 — Install Python 3.11+

**Mac (pakai Homebrew):**
```bash
# Install Homebrew dulu (kalau belum ada):
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Lalu install Python:
brew install python@3.11
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install python3.11 python3.11-venv
```

**Windows:**
1. Buka `https://python.org/downloads`
2. Download Python 3.11 atau lebih baru
3. Saat install, **CENTANG** opsi "Add Python to PATH"
4. Klik Install Now

**Verifikasi:** Buka terminal baru, ketik `python3 --version` (Mac/Linux) atau `python --version` (Windows). Harus muncul `Python 3.11.x` atau lebih.

---

## 5. Step 3 — Download SohibBot

**Cara A (punya Git — lebih mudah untuk update nanti):**
```bash
git clone https://github.com/wildanrivky/sohibbot.git
cd sohibbot
```

**Cara B (belum punya Git):**
1. Buka `https://github.com/wildanrivky/sohibbot`
2. Klik tombol hijau "Code"
3. Klik "Download ZIP"
4. Extract ZIP ke folder Documents (atau folder lain yang mudah diakses)
5. Buka terminal/command prompt
6. Pindah ke folder yang sudah di-extract:
   - Mac/Linux: `cd ~/Documents/sohibbot`
   - Windows: `cd %USERPROFILE%\Documents\sohibbot`

---

## 6. Step 4 — Buat bot Telegram via @BotFather

Bot Telegram adalah "jembatan" antara HP kamu dan Claude Code di laptop.

1. Buka app Telegram di HP
2. Search `@BotFather` dan klik
3. Klik tombol **Start**
4. Ketik `/newbot`
5. BotFather akan tanya **display name** — ketik nama apa saja (contoh: `ARIA Personal Bot`)
6. BotFather akan tanya **username** — harus diakhiri `_bot` (contoh: `aria_budi_bot`)
7. BotFather akan kasih **token** — formatnya seperti ini: `1234567890:AAH...`
8. **Copy token ini dan simpan** — kamu akan butuh nanti

> **PENTING:** Jangan share token ke siapapun. Siapapun yang punya token bisa kontrol bot kamu.

---

## 7. Step 5 — Dapat Telegram User ID kamu

User ID adalah nomor identitas unik akun Telegram kamu. Bot perlu ini supaya hanya kamu yang bisa ngobrol dengannya.

1. Buka Telegram, search `@userinfobot`
2. Klik **Start**
3. Bot langsung balas dengan angka ID kamu (contoh: `987654321`)
4. **Catat angka ini**

---

## 8. Step 6 — Jalankan Setup Wizard

Ini langkah terpenting. Wizard akan mengatur semuanya otomatis.

**Mac/Linux:**
```bash
# Dari folder sohibbot yang sudah di-download:
python3 setup.py
```

**Windows:**
```cmd
python setup.py
```

Wizard akan tanya beberapa pertanyaan:
- Nama panggilan kamu
- Nama bot (terserah — contoh: ARIA, NOVA, ALFRED)
- Token dari @BotFather
- User ID dari @userinfobot

Ikuti satu per satu. Kalau ada yang error, ikuti petunjuk yang muncul.

---

## 9. Step 7 — Verifikasi bot jalan

1. Buka Telegram
2. Search username bot yang sudah kamu buat (contoh: `@aria_budi_bot`)
3. Klik **Start**
4. Ketik: `halo`
5. Bot harus balas dalam 30 detik

Kalau bot tidak balas dalam 30 detik → lihat [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## 10. Step 8 — Bikin agent pertama kamu

Wizard sudah tanya ini di langkah terakhir. Tapi kamu bisa bikin kapan saja:

```bash
python scripts/create_agent.py --type=basic --name=nama-agent
```

**Apa itu agent?**

Agent adalah "modul spesialis" untuk task tertentu. Contoh:
- `web-researcher` — cari & ringkas info dari web
- `daily-notes` — kelola catatan harian
- `tour-helper` — bantu urusan tour/perjalanan

Bot kamu bisa punya banyak agent sesuai kebutuhan.

---

## 11. Cara kerja sehari-hari — 3 lapis memory

Bot kamu punya 3 lapis memori:

**Layer 1 — Conversation history** (`data/conversations/telegram_history.json`)

Bot ingat 20 pesan terakhir per-chat. Ini buat percakapan terasa nyambung — bot tidak tiba-tiba lupa konteks di tengah obrolan.

**Layer 2 — Long-term memory** (`memory/`)

Tiap session, Claude bisa **tulis sendiri** catatan penting ke folder ini. Contoh: kalau kamu bilang "ingat aku alergi udang" — Claude akan simpan ke `memory/user/preferences.md`. Sesi berikutnya, dia tetap ingat.

Kamu juga bisa paksa simpan dengan: `/note [catatan kamu]`

**Layer 3 — CLAUDE.md** ("DNA" bot)

File ini adalah "instruksi dasar" yang ngajarin bot karakter & cara kerjanya. Edit di sini kalau mau ubah gaya bicara, prioritas, atau aturan dasar bot. File ini dibuat otomatis saat setup.

---

## 12. Cara debug error sendiri (fitur self-fix)

Kalau bot kamu rusak/aneh/macet:

1. Kirim pesan ke bot: `halu [deskripsi singkat masalah]`
2. Contoh: `halu pesanku tidak dibalas sama sekali`

Bot akan otomatis:
1. Baca 50 baris terakhir error log
2. Baca kode bot
3. Identifikasi bug
4. Perbaiki kode langsung
5. Restart bot sendiri
6. Kasih laporan apa yang diperbaiki

Timeout: 5 menit. Kalau lewat 5 menit, bot kasih tahu — kamu harus debug manual (lihat TROUBLESHOOTING.md).

---

## 13. Cara bikin agent baru kapan saja

```bash
python scripts/create_agent.py --type=basic --name=nama-agent --desc="Deskripsi agent"
```

Contoh:
```bash
python scripts/create_agent.py --type=basic --name=web-researcher --desc="Cari dan ringkas info dari web"
```

Folder `agents/nama-agent/` akan dibuat dengan:
- `CLAUDE.md` — instruksi untuk agent ini
- `run.py` — entry point
- `manifest.yaml` — metadata

Tipe agent yang tersedia: `basic`, `conversational`, `pipeline`, `reactive`, `scheduled`

---

## 14. Cara update template

Kalau ada fitur baru dari SohibBot:

```bash
# Dari folder sohibbot:
git pull origin main
```

Kalau ada perubahan di `.env` (variable baru):

```bash
python setup.py --migrate
```

---

## 15. Cara stop/uninstall

**Mac:**
```bash
# Stop service
launchctl unload ~/Library/LaunchAgents/{{SERVICE_LABEL}}.plist

# Hapus service file
rm ~/Library/LaunchAgents/{{SERVICE_LABEL}}.plist
```

**Linux:**
```bash
systemctl --user disable --now {{BOT_SLUG}}.service
rm ~/.config/systemd/user/{{BOT_SLUG}}.service
```

**Windows:**
```cmd
schtasks /delete /tn {{BOT_SLUG}} /f
```

---

*Ada pertanyaan? Lihat [TROUBLESHOOTING.md](TROUBLESHOOTING.md) atau buka issue di GitHub.*
