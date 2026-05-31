# SohibBot — Plan Finalisasi Website & Launch sebagai Produk Digital

> **Versi**: 1.0 — 2026-05-24
> **Status**: Beta phase (repo public untuk testing dengan beberapa orang)
> **Goal akhir**: Repo private + dijual sebagai produk digital one-time purchase

---

## 1. Analisa Pricing & Positioning

### Komparable produk di pasar Indonesia

| Produk | Tipe | Harga |
|---|---|---|
| Premium Notion template | One-time digital | 99–499rb |
| Kelas online "Build AI" (Skill Academy, Udemy) | Course | 199–599rb |
| Custom GPT / automation setup | Service | 1–5jt |
| Indie SaaS (Cursor-like) | Subscription | $20–50/bulan |

### Rekomendasi pricing tier

| Tier | Harga | Isi | Target |
|---|---|---|---|
| **Founding price** (50 pembeli pertama) | Rp 297.000 | SohibBot Pro + lifetime update | Validate willingness to pay |
| **Launch price** | Rp 597.000 | SohibBot Pro + lifetime update | Default setelah founding habis |
| **Bundle premium** | Rp 1.197.000 | SohibBot + 3 Agent Pack + setup live 30 menit | Buyer gaptek total |
| **Upsell agent pack** (post-launch) | Rp 99–199rb / pack | Single agent pack (Tour, Content, Productivity) | Repeat purchase |

### Alasan one-time vs subscription

- Pasar Indonesia masih resist subscription untuk produk baru (kecuali nilai jelas Netflix-tier).
- One-time = lower friction → konversi lebih tinggi.
- Recurring revenue bisa datang dari agent pack tambahan + update major berbayar nantinya.

### Positioning shift saat masuk closed-source

- Sekarang: "Open Source · Gratis selamanya"
- Setelah launch: **"Akses Lifetime · Update Selamanya"**
- Tetap personal (Wildan signature), tapi premium-positioned.

---

## 2. Roadmap 4 Fase

### Fase 0 — Beta & Feedback (1–2 minggu) — SEKARANG

Repo tetap public, fokus dapat data nyata sebelum commit ke closed-source.

- Rekrut 5–10 beta tester (network IG, teman dev, tour leader colleagues).
- Tambahkan **form feedback** di website (Tally.so / Google Form embed).
- Tambahkan **banner beta** di hero website: "🧪 Beta — gabung tester batch 1, gratis selamanya" → CTA WA/email.
- Kumpulkan data berikut dari tester:
  - Bug report
  - Kesulitan setup (di langkah mana mereka stuck?)
  - Fitur yang diminta
  - **Willingness to pay**: "Kalau berbayar, harga wajar berapa untukmu?"
- Catat progress harian di `memory/projects/sohibbot-launch.md` (di el-solver memory).

### Fase 1 — Polish Produk (2–3 minggu)

Berdasarkan feedback beta, perbaiki product-market fit.

**A. Polish landing page sohibbot.com**

Section yang perlu ditambah:
- 🆕 **Demo Video** (video 60 detik: kirim pesan → bot eksekusi)
- 🆕 **Pricing** (founding price + launch price + bundle)
- 🆕 **Testimoni beta tester** (foto + quote pendek)
- 🆕 **FAQ sales-oriented** (refund policy, garansi, "bedanya dengan ChatGPT Plus?", "harus install apa saja?", dsb)
- 🆕 **Agent Showcase** (galeri agent siap-pakai dengan screenshot)
- 🆕 **Email capture waitlist** (untuk yang belum siap beli)

Perubahan existing section:
- 🔧 Hero CTA: "Clone di GitHub" → "Akses Sekarang"
- 🔧 Tagline "Open Source · Gratis selamanya" → "Lifetime Access · Update Selamanya"

**B. Bikin 3 Agent Pack siap-pakai** (value addon premium)

1. **Tour Leader Pack** (paling familiar untuk Wildan)
   - Pengumuman grup
   - Checklist destinasi internasional
   - Info mata uang & colokan per negara
   - Itinerary builder

2. **Content Creator Pack**
   - Caption Instagram (sesuai tone & brand)
   - Thumbnail generator (sudah ada di el-solver)
   - Transcript-to-cheatsheet (sudah ada skill)

3. **Productivity Pack**
   - Note taker harian
   - Scheduler / reminder
   - Email draft

**C. Polish repo & UX**

- Tutorial video YouTube "Setup SohibBot 10 Menit Tanpa Coding"
- Re-test setup wizard di Mac/Linux/Windows
- Polish error messages (lebih human-friendly)
- Tambah mode `python setup.py --migrate` untuk update config

### Fase 2 — Infrastruktur Sales (1 minggu)

Setelah produk siap, tinggal pasang infrastruktur jualan.

| Komponen | Tool rekomendasi | Biaya |
|---|---|---|
| Payment gateway | **Mayar.id** atau **Lemonsqueezy** | 4–5% per transaksi |
| Delivery digital product | Mayar (built-in) atau Gumroad | Termasuk fee transaksi |
| Email automation | **Brevo** (free tier 300/hari) atau ConvertKit | 0–rb |
| Access control closed-source | GitHub private + invite per buyer | 0 (GitHub Pro 65rb/bln) |
| Customer support | WhatsApp Business + auto-reply | 0 |

**Switch ke closed-source flow:**

1. Rename `wildanrivky/sohibbot` → `wildanrivky/sohibbot-public` (versi demo / free trial 7 hari)
2. Bikin repo private `wildanrivky/sohibbot-pro` (full version)
3. Setiap buyer: dapat email instruksi → invite ke repo private + akses agent pack

### Fase 3 — Soft Launch & Marketing (1 minggu)

- **Konten pre-launch IG/TikTok** (10–15 posts edukasi)
  - "AI yang ingat alergimu" (story)
  - "Setup bot pribadi tanpa coding" (reel)
  - "Demo: aku minta bot baca PDF dan ringkasin" (reel)
  - dst.
- **Email blast** ke waitlist + beta testers (founding price 297rb)
- **YouTube demo** publish (video panjang 5–8 menit)
- **Affiliate mini**: kode diskon 20rb ke beta tester yang share ke 3 orang.

### Fase 4 — Post-Launch (ongoing)

- Customer support flow (WA template, FAQ).
- Update produk berkala (release notes via email).
- **Agent pack baru** sebagai upsell (Rp 99–199rb per pack).
- Mungkin: tier "SohibBot Studio" Rp 2–3jt (multi-user untuk tim kecil).

---

## 3. Action Items Minggu Ini

| Hari | Task | Output |
|---|---|---|
| Senin–Selasa | Tambah banner beta + form feedback di sohibbot.com | Website ada CTA beta |
| Rabu–Kamis | Rekrut 5 beta tester (1 tour leader, 1 content creator, 2 freelancer, 1 mahasiswa) | List 5 nama + cara kontak mereka |
| Jumat–Sabtu | Mulai bikin **Tour Leader Pack** (prototipe agent pack pertama) | Folder `agents/tour-leader-pack/` jalan |
| Minggu | Review feedback minggu pertama → keep iterate atau extend beta? | Update plan ini |

---

## 4. Decision yang Masih Open

- [ ] **Beta tester sourcing**: ada calon konkret, atau perlu strategi rekrut?
- [ ] **Payment gateway**: Mayar.id vs Lemonsqueezy (Mayar lebih ramah pasar IDN, Lemonsqueezy lebih clean UX tapi USD)
- [ ] **Domain strategy**: setelah jadi closed-source, sohibbot.com ditujukan ke landing page sales, atau ada subdomain `app.sohibbot.com` untuk dashboard buyer?
- [ ] **Brand color & visual**: landing page current mostly text-based, perlu visual identity yang lebih kuat?
- [ ] **Target launch date**: kalau Fase 0 dimulai sekarang, realistis launch 4–6 minggu lagi. Apakah Wildan mau target lebih cepat / lambat?

---

## 5. Risk & Mitigation

| Risk | Mitigation |
|---|---|
| Beta tester tidak ada yang bayar setelah free trial | Survey willingness to pay di awal, jangan tunggu launch |
| User stuck di setup (Claude CLI, Telegram BotFather) | Tutorial video + sesi setup live (sebagai upsell bundle) |
| Repo public sudah ke-clone banyak orang sebelum jadi private | Tidak masalah — yang dijual bukan kode mentah, tapi: lifetime update + agent pack premium + setup support |
| Claude Code CLI berubah behavior (breaking change) | Pinning versi di setup wizard + monitoring channel rilis Anthropic |
| Pasar terlalu niche (butuh Claude Pro + Telegram + ngerti install) | Hybrid model: setup-as-a-service untuk yang gaptek total (Rp 1.5jt include setup) |

---

*Plan akan di-update setiap akhir minggu berdasarkan progress aktual.*
