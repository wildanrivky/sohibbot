# HANDOFF — ERP SohibBot

**Tanggal**: 2026-05-25 ~03:00
**Sesi sebelumnya**: Planning + setup MCP (Fase 0 selesai)
**Status**: Menunggu Wildan restart Claude Code → lanjut Fase 1

---

## Konteks singkat

Wildan ingin punya ERP (admin panel) untuk SohibBot — fokus jualan SohibBot saja. Plan lengkap ada di `ERP_PLAN.md` di folder yang sama (87 task terpecah 5 fase, baru 7 yang selesai).

**Stack final**: Next.js 15 + Supabase (newildanr@gmail.com) + Vercel (newildanr@gmail.com) + domain `admin.sohibbot.com`.

---

## Yang sudah dilakukan sesi ini

1. ✅ Riset & buat ERP_PLAN.md di `/Volumes/Bukan OS/AI Agent/sohibbot/ERP_PLAN.md`
2. ✅ Pilih tech stack — alasan & trade-off Vercel vs Hostinger sudah ada di plan
3. ✅ Wildan sign up Supabase + buat project `sohibbot` di akun newildanr@gmail.com
4. ✅ Wildan sign up Vercel + generate access token di akun newildanr@gmail.com
5. ✅ Setup MCP Supabase newildanr (`@supabase/mcp-server-supabase`) di `.mcp.json` el-solver
6. ✅ Setup MCP Vercel newildanr (`@antidrift/mcp-vercel`) di `.mcp.json` el-solver
7. ✅ Update ERP_PLAN.md dengan checklist granular (87 task)

---

## Lokasi token / credential

- **Supabase PAT**: di `/Volumes/Bukan OS/AI Agent/el-solver/.mcp.json` (gak di-track git, ada di `.gitignore`)
- **Vercel Token**: di `~/.antidrift/vercel.json` (chmod 600, gak di-track git)
- **Hostinger API**: di `/Volumes/Bukan OS/AI Agent/el-solver/.mcp.json`
- **GitHub PAT existing**: `ghp_eNA...` di `/Volumes/Bukan OS/AI Agent/el-solver/.env`

⚠️ Token Supabase & Vercel sudah ke-share di chat history sesi ini. Setelah ERP jadi & stabil, sebaiknya Wildan revoke + regenerate.

---

## Langkah setelah Wildan restart Claude Code

### Step 1 — Verifikasi MCP baru aktif
Saat startup Claude Code akan minta approve 2 MCP server baru:
- `supabase-newildanr`
- `vercel-newildanr`

Approve dua-duanya.

### Step 2 — Lanjutkan dengan perintah ini ke Claude:

> "Lanjut bangun ERP SohibBot sesuai ERP_PLAN.md. Mulai dari Fase 1A — verifikasi MCP & ambil project ref Supabase."

Claude akan otomatis:
1. List project Supabase pakai akun newildanr, ambil project ref `sohibbot`
2. List project Vercel pakai akun newildanr (cek bekerja)
3. Cek scope GitHub PAT existing
4. Mulai apply schema migration 8 tabel di Supabase
5. Setup RLS policies
6. Inisialisasi Next.js project di `/Volumes/Bukan OS/AI Agent/sohibbot-erp/`
7. Deploy ke Vercel + setup CNAME `admin.sohibbot.com` lewat Hostinger MCP

Target Fase 1 selesai: Wildan bisa login ke `https://admin.sohibbot.com` dari HP/laptop, lihat sidebar kosong (29 task).

---

## File-file yang relevan

| File | Lokasi | Fungsi |
|---|---|---|
| Plan utama | `/Volumes/Bukan OS/AI Agent/sohibbot/ERP_PLAN.md` | Source of truth, ada checklist 87 task |
| Handoff ini | `/Volumes/Bukan OS/AI Agent/sohibbot/HANDOFF.md` | Konteks pause/resume |
| MCP config | `/Volumes/Bukan OS/AI Agent/el-solver/.mcp.json` | 4 MCP server: hostinger, supabase-newildanr, vercel-newildanr, meta |
| Vercel credential | `~/.antidrift/vercel.json` | Token Vercel newildanr |
| Landing page sohibbot | `/Volumes/Bukan OS/AI Agent/sohibbot/landing/index.html` | Nanti diupdate Fase 2 (POST ke API ERP) |
| WA bot | `/Volumes/Bukan OS/AI Agent/el-solver/agents/whatsapp-agent/index.js` | Nanti diupdate Fase 4 (POST ke `/api/orders/confirm`) |

---

## Risiko / hal yang perlu diperhatikan

- **MCP claude.ai Supabase** (akun tourismlance) masih aktif berdampingan dengan `supabase-newildanr`. Untuk ERP SohibBot, **selalu pakai `supabase-newildanr`** — jangan yang `claude.ai`.
- **HANDOFF.md WA bot** (di folder el-solver) belum selesai — itu konteks berbeda (re-auth WA), jangan ditimpa.
- Restart Claude Code mungkin perlu approve permission tambahan kalau ada hook baru.

---

## Komit terakhir sebelum handoff

Sesi ini belum commit apa-apa — semua perubahan masih di working tree:
- `ERP_PLAN.md` (NEW, di folder sohibbot)
- `HANDOFF.md` (NEW, di folder sohibbot)
- `.mcp.json` (MODIFIED, di folder el-solver, tapi di gitignore — gak akan ke-commit)

Kalau Wildan mau commit dokumentasi (ERP_PLAN.md + HANDOFF.md), boleh di-commit di repo sohibbot. Tapi pastikan `.mcp.json` el-solver TIDAK ikut.
