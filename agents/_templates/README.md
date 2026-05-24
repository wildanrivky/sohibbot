# Agent Templates

Scaffold untuk bikin agent baru di SohibBot.

## Cara pakai

```bash
# Dari root folder sohibbot:
python scripts/create_agent.py --type=basic --name=nama-agent --desc="Deskripsi agent"
```

Atau langsung copy folder `_base/` dan modifikasi:

```bash
cp -r agents/_templates/_base agents/nama-agent
```

## Struktur agent

Setiap agent punya:
- `CLAUDE.md` — instruksi sistem untuk agent ini (baca pertama kali)
- `run.py` — entry point, dipanggil oleh bot
- `manifest.yaml` — metadata (nama, versi, tipe)
- `memory/` — long-term memory khusus agent ini

## Tipe agent

| Tipe | Gunakan untuk |
|---|---|
| `basic` | Task sederhana, satu input → satu output |
| `conversational` | Percakapan multi-turn, butuh konteks |
| `pipeline` | Multi-langkah berurutan (fetch → process → output) |
| `reactive` | Triggered oleh event, bukan user input langsung |
| `scheduled` | Task terjadwal (cron-style) |

## Cara jalankan agent

```bash
python agents/nama-agent/run.py "prompt atau task kamu"
```

## Cara panggil dari Telegram

Cukup chat ke bot dengan konteks yang cukup. Bot akan otomatis panggil agent yang relevan kalau kamu menyebut nama atau fungsinya.

Atau tambahkan command khusus di `scripts/claude_telegram.py` untuk trigger agent tertentu.
