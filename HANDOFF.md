# HANDOFF — SohibBot ERP
**Terakhir diupdate**: 2026-05-25
**Status**: 83 / 87 task selesai (95%) — ERP production-ready

---

## Yang Sudah Selesai

### ERP (admin.sohibbot.com)
- Dashboard: KPI cards + recharts line chart + pie chart paket
- Revenue: form tambah biaya (modal), P&L summary
- Coupons: form buat kupon baru, toggle aktif/nonaktif
- Deliveries: tombol GitHub Invite langsung dari tabel pending
- Feedback: form tambah manual + NPS tracker
- Settings: profile, API keys, export CSV, Backup JSON, link Audit Log
- Audit Log viewer: `/settings/audit`
- Cron job Vercel: check GitHub invite acceptance tiap 6 jam
- Vercel Analytics + Speed Insights aktif
- Auto Telegram notif ke Wildan saat ada order baru

### EL SOLVER Telegram Bot
- `/sales` — ringkasan revenue dari ERP
- `/pending` — daftar delivery pending
- `/order [nomor]` — redirect ke admin panel

### WA Bot
- `postErpDeliverySent()` — sync ke ERP saat kirim akses
- `/kirim-akses` endpoint: support `order_number` + `github_username`

---

## Yang Masih Tersisa (4 task opsional)

| Task | Cara |
|---|---|
| Backup weekly Google Drive | Tambah cron di vercel.json + hit `/api/backup`, upload via Google Drive MCP |
| MFA Supabase | Supabase Dashboard → Auth → Settings → Enable MFA. Tidak butuh kode |
| Mobile responsive QA | Buka HP, cek tiap halaman |
| Dokumentasi repo | Tulis README.md di sohibbot-erp |

---

## URL Penting
- ERP: https://admin.sohibbot.com
- Landing: https://sohibbot.com
- GitHub ERP: wildanrivky/sohibbot-erp
- GitHub El Solver: wildanrivky/el-solver

---

## Perintah Resume
Kalau mau lanjut sisa 4 task, cukup bilang: `lanjut sisanya`
