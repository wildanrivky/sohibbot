# ERP SohibBot — Plan Implementasi

**Dibuat**: 2026-05-25
**Owner**: Wildan Rivky
**Status**: Fase 0 selesai (setup MCP), menunggu restart Claude Code → lanjut Fase 1

**Progress total**: 83 / 87 task selesai (95%)

---

## 1. Tujuan & Keputusan Awal

ERP ini = **dashboard admin pribadi Wildan** untuk kelola sisi backend SohibBot. Sohibbot.com tetap jadi sisi user (landing + checkout). ERP ini sisi owner — gak dilihat customer.

**Keputusan yang sudah final** (dari diskusi 2026-05-25):

| Aspek | Pilihan |
|---|---|
| Scope | SohibBot only — fokus, gak gabung sama tour/IG dulu |
| Bentuk | Sub-domain terpisah → **admin.sohibbot.com** |
| Stack | Next.js 15 + Supabase (Postgres + Auth + Realtime) |
| Akses | Multi-device (laptop & HP), online 24/7 |
| Data awal | Mulai dari nol — gak ada migrasi |
| Modul awal | Sales+Customer+Dashboard, Kupon, Akses, Feedback (semua dibangun bertahap) |

---

## 2. Apa Saja yang Harus Ada di ERP (Standar untuk Produk Digital)

Untuk produk digital one-time seperti SohibBot, modul standar yang masuk akal:

**Wajib ada** (Phase 1):
1. **Dashboard utama** — KPI cards + grafik revenue + daftar pesanan terbaru
2. **Orders** — daftar semua pesanan, status, filter & search
3. **Customers** — daftar pembeli, riwayat order per orang
4. **Revenue** — total penghasilan, breakdown per paket, per bulan, profit

**Sangat berguna** (Phase 2):
5. **Coupons** — kelola kupon, kuota, statistik penggunaan
6. **Delivery tracking** — siapa udah dikirim akses GitHub, follow-up otomatis
7. **Products/Paket** — kelola paket (founding/standard/bundle), harga, stok founding

**Penting jangka panjang** (Phase 3):
8. **Feedback & Beta Tester** — daftar tester, NPS, bug report
9. **Settings & Audit log** — preferensi, riwayat aktivitas

**Yang gak akan dibikin** (overkill untuk solo founder):
- Inventory warehouse (digital product, gak ada stok fisik)
- HR/Payroll (solo)
- Multi-currency (IDR only)
- Multi-branch
- Accounting full PSAK (cukup laporan penghasilan + biaya)

---

## 3. Tech Stack & Arsitektur

### Stack pilihan

```
Frontend & API   : Next.js 15 (App Router) + TypeScript
UI               : Tailwind CSS + shadcn/ui + lucide-icons
Database         : Supabase Postgres (free tier cukup untuk start)
Auth             : Supabase Auth (Wildan only, email+password, MFA optional)
Realtime         : Supabase Realtime (order baru muncul live)
Charts           : Recharts (sederhana, populer)
Deployment       : Vercel (free) untuk Next.js + Supabase managed
Domain           : admin.sohibbot.com (CNAME ke Vercel)
```

**Kenapa pilihan ini:**
- **Next.js + Vercel**: deploy 1-klik, gratis, fast. Standar industri.
- **Supabase**: Postgres beneran (bukan NoSQL), ada Auth + Realtime built-in. Free tier: 500MB DB, 2GB bandwidth, cukup banget untuk ratusan-ribuan pembeli pertama. Bisa upgrade kapan saja.
- **shadcn/ui**: komponen UI cantik, modern, copy-paste ke project (bukan dependency). Hasilnya mirip Linear/Vercel — clean.
- **Tidak pakai backend Python terpisah**: Next.js API routes sudah cukup. Simpler, satu repo.

### Arsitektur sistem (gambaran besar)

```
[ sohibbot.com landing ]                        [ admin.sohibbot.com ]
   ↓ checkout form                                ↑ Wildan login
   ↓ POST /api/orders                            ↑
   ↓                                              ↑
[ Next.js API (admin.sohibbot.com/api) ] ←──── [ Wildan browser ]
   ↓                                              ↑
   ↓ insert order                                 ↑ realtime push
   ↓                                              ↑
[ Supabase Postgres + Realtime ] ←──── [ WA bot ] (POST status)
                                       └─→ [ EL SOLVER Telegram ] (notif)
```

**Alur baru** (yang akan dibangun):
1. Customer isi form checkout di sohibbot.com → form **POST ke API ERP** (`/api/orders/create`) → order tercatat di DB dengan status `pending`
2. Customer klik WA → kirim pesan ke Wildan (sama seperti sekarang)
3. WA bot deteksi pola checkout → **POST ke API ERP** (`/api/orders/confirm`) → status order jadi `paid`, customer record dibuat/di-update
4. WA bot kirim akses → **POST ke API ERP** (`/api/deliveries/sent`) → status delivery `delivered`
5. Dashboard ERP **realtime** menampilkan order baru tanpa refresh

---

## 4. Database Schema (Postgres)

Ini schema awal. Bisa ditambah seiring waktu lewat migrasi.

### Tabel `customers`
```sql
id              uuid primary key default gen_random_uuid()
nama            text not null
email           text not null unique
wa_number       text                  -- nomor WA (boleh null)
github_username text                  -- diisi saat kirim akses
source          text                  -- 'organic', 'beta', 'referral', dll
notes           text
created_at      timestamptz default now()
updated_at      timestamptz default now()
```

### Tabel `products` (paket)
```sql
id              uuid primary key default gen_random_uuid()
slug            text unique           -- 'founding', 'standard', 'bundle'
name            text not null         -- 'Founding (50 pertama)'
price           integer not null      -- dalam rupiah, contoh: 297000
stock_limit     integer               -- 50 untuk founding, null kalau unlimited
sold_count      integer default 0
is_active       boolean default true
sort_order      integer default 0
created_at      timestamptz default now()
```

### Tabel `coupons`
```sql
id              uuid primary key default gen_random_uuid()
code            text unique not null  -- 'SOHIBBOT100%'
discount_type   text                  -- 'percentage' | 'fixed'
discount_value  integer not null      -- 100 (%) atau 50000 (rp)
max_uses        integer               -- null = unlimited
used_count      integer default 0
valid_until     timestamptz
is_active       boolean default true
notes           text
created_at      timestamptz default now()
```

### Tabel `orders`
```sql
id              uuid primary key default gen_random_uuid()
order_number    text unique not null  -- 'SB-26052501' (YYMMDD + sequence)
customer_id     uuid references customers(id)
product_id      uuid references products(id)
coupon_id       uuid references coupons(id) null
subtotal        integer not null      -- harga sebelum diskon
discount        integer default 0
total           integer not null      -- yang ditransfer pembeli
status          text not null         -- 'pending' | 'paid' | 'delivered' | 'refunded' | 'cancelled'
payment_method  text default 'manual_transfer'
payment_proof   text                  -- url bukti transfer (optional)
paid_at         timestamptz
delivered_at    timestamptz
notes           text
created_at      timestamptz default now()
updated_at      timestamptz default now()
```

### Tabel `deliveries`
```sql
id              uuid primary key default gen_random_uuid()
order_id        uuid references orders(id)
type            text                  -- 'github_invite', 'manual_share', 'email'
github_username text
status          text                  -- 'pending', 'invited', 'accepted'
sent_at         timestamptz
accepted_at     timestamptz
notes           text
created_at      timestamptz default now()
```

### Tabel `feedback`
```sql
id              uuid primary key default gen_random_uuid()
customer_id     uuid references customers(id)
type            text                  -- 'bug', 'feature_request', 'testimonial', 'nps'
title           text
content         text not null
nps_score       integer               -- 0-10
status          text                  -- 'new', 'in_progress', 'resolved', 'wont_fix'
priority        text                  -- 'low', 'medium', 'high'
created_at      timestamptz default now()
resolved_at     timestamptz
```

### Tabel `expenses` (untuk hitung profit)
```sql
id              uuid primary key default gen_random_uuid()
description     text not null         -- 'Hostinger bulanan', 'Vercel Pro'
category        text                  -- 'hosting', 'tools', 'marketing'
amount          integer not null
date            date not null
recurring       boolean default false
notes           text
created_at      timestamptz default now()
```

### Tabel `audit_log` (penting untuk produk berbayar)
```sql
id              uuid primary key default gen_random_uuid()
actor           text                  -- 'wildan' atau 'system' atau 'wabot'
action          text not null         -- 'order.create', 'order.refund', dll
entity_type     text                  -- 'order', 'customer', 'coupon'
entity_id       uuid
metadata        jsonb
created_at      timestamptz default now()
```

**Row Level Security (RLS)**: semua tabel akan diaktifkan RLS — hanya user yang login (Wildan) yang bisa baca/tulis. Endpoint publik (dari checkout form) pakai **service_role key** yang disimpan aman di env Vercel.

---

## 5. Detail Tiap Modul

### 5.1 Dashboard utama (`/`)

Halaman yang Wildan lihat pertama kali setelah login.

**KPI Cards (atas):**
- Revenue bulan ini (vs bulan lalu, % change)
- Total pesanan bulan ini (vs bulan lalu)
- Average order value
- Conversion rate (kalau ada data traffic dari Plausible/GA — opsional)

**Chart:**
- Revenue 30 hari terakhir (line chart)
- Breakdown paket pie chart (founding vs standard vs bundle)

**Tabel:**
- 5 pesanan terbaru (live update via Realtime)
- Quick action: klik order → buka detail

**Alert (kalau ada):**
- "5 pesanan menunggu konfirmasi akses"
- "Kuota founding sisa 12 dari 50"

---

### 5.2 Orders (`/orders`)

**Daftar pesanan:**
- Tabel dengan kolom: Order #, Customer, Paket, Total, Status, Tanggal, Aksi
- Filter: status, paket, rentang tanggal, pakai kupon
- Search: nama/email customer, order number
- Bulk action: mark as paid, mark as delivered

**Detail pesanan** (`/orders/[id]`):
- Info lengkap order + customer + coupon
- Timeline: pending → paid → delivered (dengan timestamp)
- Form upload bukti transfer (opsional)
- Catat GitHub username + tombol "Kirim invite" (panggil GitHub API atau buka link manual)
- Notes (catatan internal Wildan)
- Aktivitas log (audit trail order ini)

**Buat manual order** — kalau ada pembeli yang transfer tanpa lewat website (misal Wildan jual offline), bisa buat order manual.

---

### 5.3 Customers (`/customers`)

**Daftar customer:**
- Tabel: Nama, Email, WA, Total order, Total spent, Last order, Status (beta tester / customer / lead)
- Filter: beta tester only, repeat buyer, dll

**Detail customer** (`/customers/[id]`):
- Profile: nama, email, WA, GitHub username
- Tag: beta tester, founder, dll
- Riwayat order
- Feedback yang pernah dia kasih
- Catatan personal Wildan
- Quick action: kirim WA, email, github invite

---

### 5.4 Revenue & Reports (`/revenue`)

**Tab "Penghasilan":**
- Total revenue all-time
- Revenue per bulan (bar chart 12 bulan)
- Breakdown per paket
- Top customers (by spent)

**Tab "Biaya":**
- Form input expense (Hostinger, Vercel, tools, marketing)
- Daftar biaya per bulan
- Recurring expense (otomatis ditambahkan tiap bulan)

**Tab "Profit & Loss":**
- Revenue – Expenses = Net profit per bulan
- Grafik profit margin

**Export:**
- Tombol "Export ke CSV" — untuk catatan pajak / pribadi

---

### 5.5 Coupons (`/coupons`)

**Daftar kupon:**
- Code, Discount, Used / Max, Status, Valid until

**Form buat kupon baru:**
- Code (auto-uppercase)
- Tipe: % atau Rp fixed
- Nilai diskon
- Max usage
- Tanggal expired
- Aktif on/off

**Statistik:**
- Kupon paling banyak dipakai
- Revenue dari order yang pakai kupon (untuk evaluasi efektivitas)

---

### 5.6 Delivery / Akses GitHub (`/deliveries`)

**Tab "Pending"**: order yang sudah paid tapi akses belum dikirim
- Tombol: "Kirim GitHub invite" (otomatis panggil GitHub API kalau Wildan setup Personal Access Token, atau buka manual)
- Reminder: kalau >24 jam belum dikirim, badge merah

**Tab "Terkirim, menunggu accept"**: invite sudah dikirim tapi belum di-accept
- Auto-check status invite via GitHub API setiap 6 jam (background job sederhana)

**Tab "Selesai"**: customer udah accept & jadi collaborator

---

### 5.7 Feedback & Beta Tester (`/feedback`)

**Inbox feedback:**
- Tabel: tanggal, customer, tipe, judul, status, prioritas
- Tipe: bug, feature request, testimonial, NPS

**Beta tester management:**
- Daftar tester aktif (customer dengan tag `beta_tester`)
- Status partisipasi: invited / active / inactive
- Statistik: berapa feedback per tester

**NPS tracker:**
- Average NPS dari semua tester
- Trend chart

---

### 5.8 Settings (`/settings`)

- Profile Wildan
- API keys & integrations (GitHub PAT, Telegram bot token, webhook URLs)
- Backup database (export semua data ke JSON, sekali klik)
- Audit log viewer (riwayat semua aktivitas)

---

## 6. Integrasi dengan Sistem Existing

### 6.1 Sohibbot.com (landing + checkout form)

**Perubahan di `landing/index.html`:**
- Saat submit checkout form, sebelum buka WA, **POST dulu ke API ERP**:
  ```js
  fetch('https://admin.sohibbot.com/api/orders/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': 'PUBLIC_API_KEY' },
    body: JSON.stringify({ nama, email, wa, paket, kupon })
  })
  ```
- Response: order_number → embed ke pesan WA pre-filled supaya WA bot tahu order mana

### 6.2 WA bot (`agents/whatsapp-agent/index.js`)

**Perubahan:**
- Saat deteksi pola checkout, parse order_number dari pesan
- POST ke `/api/orders/confirm` dengan order_number → status jadi `paid`
- Saat "kirim akses" command dari Telegram → POST ke `/api/deliveries/sent`

### 6.3 EL SOLVER Telegram (`scripts/claude_telegram.py`)

- Tambah notifikasi: tiap order baru → push ke Telegram Wildan
- Command baru:
  - `/sales` → rekap penghasilan minggu/bulan ini
  - `/order [number]` → detail order
  - `/pending` → list order yang belum dikirim akses

---

## 7. Auth & Security

- **Single user**: Hanya Wildan yang login. Pakai Supabase Auth (email + password).
- **MFA**: nyalakan TOTP (Google Authenticator). Wajib untuk akun owner.
- **API endpoint untuk checkout form** (publik):
  - Pakai API key terpisah yang di-rotate berkala
  - Rate limit (Vercel built-in)
  - Validasi input ketat (zod schema)
  - CORS hanya allow `sohibbot.com`
- **Database**:
  - Row Level Security aktif di semua tabel
  - Service role key hanya di server-side Vercel (gak pernah expose ke client)
- **Audit log**: setiap perubahan data tercatat — siapa, kapan, apa.
- **Backup**: Supabase auto-backup harian (free tier dapat 7 hari). Tambah manual export mingguan ke Google Drive.

---

## 8. Roadmap Implementasi (estimasi 4–6 minggu)

### Fase 0 — Setup awal & akses (DONE, 7 task) ✅

- [x] Riset & buat plan ERP
- [x] Pilih tech stack (Next.js + Supabase + Vercel)
- [x] Wildan sign up Supabase pakai newildanr@gmail.com + buat project `sohibbot`
- [x] Wildan sign up Vercel pakai newildanr@gmail.com + generate access token
- [x] Setup MCP Supabase (`supabase-newildanr`) di `.mcp.json`
- [x] Setup MCP Vercel (`vercel-newildanr`) di `.mcp.json`
- [x] Buat checklist granular ERP_PLAN.md

### Fase 1 — Fondasi & infra (29 task)

**1A. Verifikasi MCP & akses (setelah restart Claude Code)**
- [x] Restart Claude Code → approve 2 MCP server baru
- [x] Verify Supabase MCP newildanr connected (list_projects bekerja)
- [x] Verify Vercel MCP newildanr connected (vercel_list_projects bekerja)
- [x] Ambil project ref Supabase `sohibbot`, lock MCP ke project itu
- [x] Cek scope GitHub PAT existing (`ghp_eNA...`), regenerate kalau kurang scope

**1B. Schema database Supabase**
- [x] Apply schema: tabel `customers`
- [x] Apply schema: tabel `products`
- [x] Apply schema: tabel `coupons`
- [x] Apply schema: tabel `orders`
- [x] Apply schema: tabel `deliveries`
- [x] Apply schema: tabel `feedback`
- [x] Apply schema: tabel `expenses`
- [x] Apply schema: tabel `audit_log`
- [x] Seed data: 3 products (founding/standard/bundle)
- [x] Seed data: coupon `SOHIBBOT100%`
- [x] Setup Row Level Security (RLS) di semua tabel
- [x] Buat user auth admin Wildan (Supabase Auth)

**1C. Inisialisasi Next.js project**
- [x] Buat folder repo `/Volumes/Bukan OS/AI Agent/sohibbot-erp/`
- [x] Init Next.js 15 + TypeScript + App Router
- [x] Install: Tailwind CSS + shadcn/ui + Recharts + Supabase client
- [x] Setup struktur folder (app/, lib/, components/, types/)
- [x] Config env vars lokal (.env.local)
- [x] Generate TypeScript types dari Supabase schema

**1D. Auth & layout dasar**
- [x] Halaman login `/login` dengan Supabase Auth
- [x] Middleware proteksi route (redirect kalau belum login)
- [x] Layout: sidebar navigation + header
- [ ] Dark/light theme toggle (opsional)

**1E. Deploy ke Vercel + domain**
- [x] Buat GitHub repo `sohibbot-erp` (private) via API
- [x] Push initial code ke GitHub
- [x] Buat project Vercel, connect ke GitHub repo
- [x] Set env vars Vercel (Supabase URL + anon key + service role key)
- [x] First deploy (verifikasi build sukses)
- [x] Tambah CNAME `admin.sohibbot.com` via Hostinger MCP → ke Vercel
- [x] Connect domain custom di Vercel project

**Deliverable Fase 1**: Wildan bisa login ke `https://admin.sohibbot.com` dari HP/laptop, lihat sidebar kosong.

### Fase 2 — Modul Customers, Orders, API publik (13 task)

- [x] Module Customers: list page `/customers` (table + filter)
- [x] Module Customers: detail page `/customers/[id]`
- [ ] Module Customers: form create/edit + delete
- [x] Module Orders: list page `/orders` (filter status/paket/tanggal)
- [x] Module Orders: detail page `/orders/[id]` + timeline status
- [ ] Module Orders: form create/edit manual order
- [ ] Module Products: seed page (read-only di awal)
- [x] API publik `POST /api/orders/create` (untuk checkout form)
- [x] API publik `POST /api/orders/confirm` (untuk WA bot)
- [x] Setup API key + rate limit untuk endpoint publik
- [x] Update `/Volumes/Bukan OS/AI Agent/sohibbot/landing/index.html` → POST ke API
- [x] Deploy update landing sohibbot.com ke Hostinger
- [x] Test end-to-end: checkout di sohibbot.com → order masuk ERP

**Deliverable Fase 2**: Order baru dari sohibbot.com otomatis tercatat di ERP, Wildan bisa lihat & manage.

### Fase 3 — Dashboard & Revenue (11 task)

- [x] Dashboard `/`: KPI cards (revenue MTD, orders MTD, AOV, conversion)
- [x] Dashboard `/`: line chart revenue 30 hari
- [x] Dashboard `/`: pie chart breakdown paket
- [x] Dashboard `/`: tabel latest 5 orders
- [x] Dashboard `/`: alert section (pending delivery, sisa kuota founding)
- [ ] Setup Supabase Realtime: order baru auto-muncul tanpa refresh
- [x] Module Revenue `/revenue`: tab Penghasilan (chart 12 bulan + top customers)
- [x] Module Revenue: tab Biaya (CRUD expenses)
- [x] Module Revenue: tab P&L (net profit per bulan)
- [x] Export CSV (orders, customers, revenue)
- [ ] Mobile responsive untuk dashboard + revenue

**Deliverable Fase 3**: Wildan buka admin.sohibbot.com → langsung lihat penghasilan real-time.

### Fase 4 — Kupon, Delivery, Feedback (13 task)

- [x] Module Coupons `/coupons`: list + filter + statistik
- [x] Module Coupons: form create/edit (code, tipe diskon, max usage, expiry)
- [x] Module Coupons: validasi otomatis (cek max_uses, valid_until)
- [x] Module Deliveries `/deliveries`: tab Pending
- [x] Module Deliveries: tab Sent (menunggu accept)
- [x] Module Deliveries: tab Selesai
- [x] Integrasi GitHub API: auto-invite collaborator pakai PAT existing
- [x] Cron job (Vercel): check status invite tiap 6 jam
- [x] Module Feedback `/feedback`: inbox + filter tipe
- [x] Module Feedback: form add feedback manual + tag beta tester
- [x] Module Feedback: NPS tracker + average score
- [x] Update WA bot: POST `/api/orders/confirm` saat detect checkout
- [x] Update WA bot: POST `/api/deliveries/sent` saat command "kirim akses"

**Deliverable Fase 4**: Workflow checkout → delivery → feedback fully connected & terotomatis.

### Fase 5 — Polish, integrasi EL SOLVER, launch (14 task)

- [x] EL SOLVER Telegram: notif order baru (auto-push)
- [x] EL SOLVER command: `/sales` rekap penghasilan minggu/bulan
- [x] EL SOLVER command: `/order [number]` detail order
- [x] EL SOLVER command: `/pending` list order belum delivery
- [x] Settings page `/settings`: profile Wildan
- [x] Settings: API keys & integrations management
- [x] Settings: backup database (export JSON 1-klik)
- [x] Audit log viewer `/settings/audit`
- [ ] Backup otomatis weekly ke Google Drive (cron + MCP)
- [ ] Enable MFA TOTP di Supabase Auth
- [x] Setup Vercel Analytics + Speed Insights (gratis)
- [ ] Mobile responsive QA semua halaman
- [ ] Dokumentasi pemakaian singkat di repo
- [ ] Sign-off: Wildan pakai ERP rutin selama 1 minggu, identifikasi pain point

**Deliverable Fase 5**: ERP production-ready, jadi tool harian Wildan.

---

### Ringkasan task per fase

| Fase | Jumlah task | Status |
|---|---|---|
| Fase 0 — Setup awal | 7 | ✅ DONE |
| Fase 1 — Fondasi & infra | 29 | 0/29 |
| Fase 2 — Customers + Orders + API | 13 | 0/13 |
| Fase 3 — Dashboard & Revenue | 11 | 0/11 |
| Fase 4 — Kupon, Delivery, Feedback | 13 | 0/13 |
| Fase 5 — Polish & launch | 14 | 0/14 |
| **Total** | **87** | **7/87 (8%)** |

---

## 9. Persiapan — Status Saat Ini

| Item | Status | Catatan |
|---|---|---|
| Akun Supabase (newildanr@gmail.com) | ✅ | Project `sohibbot` sudah dibuat |
| Akun Vercel (newildanr@gmail.com) | ✅ | Token Vercel sudah aktif |
| MCP Supabase newildanr | ✅ | Registered di `.mcp.json`, butuh restart Claude Code |
| MCP Vercel newildanr | ✅ | Registered di `.mcp.json`, butuh restart Claude Code |
| MCP Hostinger | ✅ | Sudah aktif sejak sesi sebelumnya |
| GitHub PAT | ✅ | `ghp_eNA...` di `.env`, scope perlu dicek |
| Domain sohibbot.com | ✅ | Sudah di-manage Hostinger MCP |
| Komit waktu Wildan | ⏳ | ~2-4 jam per minggu untuk testing & review |

---

## 10. Estimasi Biaya Bulanan

Untuk skala pertama (sampai ~10.000 order/bulan):

| Layanan | Tier | Biaya/bulan |
|---|---|---|
| Vercel | Hobby (free) | Rp 0 |
| Supabase | Free | Rp 0 |
| Domain admin.sohibbot.com | sub-domain dari sohibbot.com | Rp 0 |
| **Total** | | **Rp 0** |

**Kalau nanti scale up** (>10rb order, banyak storage):
- Supabase Pro: ~Rp 400rb/bulan ($25)
- Vercel Pro: ~Rp 320rb/bulan ($20)
- **Total scale**: ~Rp 720rb/bulan

Untuk fase awal (beta + soft launch), free tier cukup banget — bisa handle ratusan order/bulan tanpa upgrade.

---

## 11. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Supabase down → ERP gak bisa dibuka | Supabase uptime 99.9%. Backup harian + export mingguan ke Google Drive |
| API key bocor | Rotate API key bulanan, monitor audit log, RLS aktif |
| Order dari checkout form gak masuk DB | Fallback: WA bot tetap deteksi pola, bisa create order retroactive |
| Wildan lupa MFA | Setup recovery codes saat enable MFA, simpan di password manager |
| Free tier kena limit | Set alert di Supabase + Vercel sebelum kena hard cap |

---

## 12. Pertanyaan Terbuka (untuk diputuskan saat eksekusi)

1. **Otomatisasi GitHub invite**: Wildan mau kirim invite otomatis dari ERP (perlu PAT), atau cukup tampilkan tombol "Buka GitHub" yang membawa ke halaman invite manual?
2. **Email transaksional**: perlu kirim email konfirmasi ke pembeli (via Resend/SendGrid free tier), atau cukup WA?
3. **Multi-currency / pajak**: sekarang IDR + tanpa PPN. Kalau nanti omzet >Rp 500jt/tahun, perlu modul pajak — bahas saat itu.
4. **Bundling dengan EL SOLVER**: ERP ini standalone. Tapi nanti bisa expose endpoint supaya EL SOLVER Telegram bisa query data ERP via natural language. Bisa ditambah Phase 6.

---

## Next Step

Kalau Wildan setuju plan ini secara garis besar, langkah konkret berikutnya:

1. **Wildan review file ini** — kasih feedback / koreksi
2. **Sign-up Supabase + Vercel** (15 menit, saya pandu)
3. **Setup repo Next.js** — saya bikinkan boilerplate di `/Volumes/Bukan OS/AI Agent/sohibbot-erp/`
4. **Mulai Fase 1** — fondasi & login (target 1 minggu)

Sebelum eksekusi, Wildan bisa jawab dulu pertanyaan terbuka di bagian 12 supaya gak ada ambiguitas saat coding.
