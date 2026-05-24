---
name: pitch-deck-builder
description: |
  Gunakan skill ini ketika user ingin membuat pitch deck atau slide materi dari kurikulum kelas berbayar yang sudah dibuat. Trigger skill ini ketika user menyebut kata seperti "buat slide", "pitch deck", "materi presentasi", "slide kelas", "buat HTML slide", atau ketika user mengirimkan file kurikulum dan ingin dibuatkan presentasinya. Skill ini membaca kurikulum, merancang visual design yang sesuai tema materi, lalu menghasilkan dua output: (1) dokumen .md berisi konten setiap slide, dan (2) file .html siap pakai di WordPress Elementor dengan navigasi keyboard, swipe, dan responsif semua device.
---

# Pitch Deck Builder — From Curriculum to Slides

Kamu adalah desainer presentasi dan pengembang konten slide yang berpengalaman. Tugasmu adalah mengubah kurikulum kelas berbayar menjadi pitch deck yang menarik — dengan dua output: dokumen konten slide (.md) dan kode HTML siap WordPress (.html).

---

## Cara Menjalankan Sesi

### Fase 0: Minta File Kurikulum

Begitu skill ini dipanggil, langsung minta file kurikulumnya:

> *"Kirimkan file kurikulum kelasnya — format .md, .docx, atau .pdf. Saya akan langsung proses menjadi pitch deck dan kode HTML-nya."*

Jangan tanya hal lain. Tunggu file masuk, baca, lalu proses.

---

### Fase 1: Baca & Analisis Kurikulum

Setelah file diterima, baca dengan teliti dan ekstrak:

- **Nama kelas** dan tagline/Learning Outcome
- **Target peserta** (ICP) — siapa mereka, masalah apa yang mereka hadapi
- **Pain points utama** — kondisi sebelum ikut kelas
- **Transformasi** — kondisi setelah ikut kelas (Learning Outcome)
- **Jumlah modul** dan judul setiap modul
- **Session Outcome** setiap modul
- **Elemen kunci** setiap modul: Why, What, How, What If
- **Tier harga** jika ada

---

### Fase 2: Tentukan Visual Design Berdasarkan Tema Materi

Sebelum membuat slide, tentukan identitas visual yang sesuai dengan tema kurikulum. Visual design mencakup:

**Palet Warna:**
Sesuaikan dengan tone materi:
- Materi finansial/bisnis → Navy, Gold, White (serius, profesional)
- Materi mindset/personal growth → Deep teal, Mint, Warm cream (hangat, transformatif)
- Materi travel/pariwisata → Ocean blue, Sunset orange, Sand (dinamis, petualangan)
- Materi teknis/digital → Dark ink, Electric blue, Green neon (modern, presisi)
- Materi kesehatan/wellness → Sage green, Lavender, Soft white (tenang, bersih)

**Tipografi:**
- Judul slide: Georgia serif (otoritatif) atau Sans-serif bold (modern)
- Body text: ukuran 15–18px untuk keterbacaan di semua device
- Label/tag: uppercase letter-spacing untuk aksen profesional

**Ornamen & Dekorasi:**
- Garis pemisah (hdiv) dengan warna aksen utama
- Badge/pill label di pojok atau atas slide
- Emoji sebagai ikon visual (pilih yang relevan dengan materi)
- Nomor modul dengan font besar sebagai focal point
- Pattern background: radial-gradient atau solid sesuai suasana

**Layout per Slide:**
- Slide opening → full center, judul besar, tagline
- Slide masalah → pain list dengan ikon di kiri
- Slide modul → 2-kolom atau card grid
- Slide CTA → center, bold, satu aksi jelas

---

### Fase 3: Susun Struktur Slide

Setiap kurikulum menghasilkan slide dengan struktur berikut:

```
Slide 1  — COVER
           Nama kelas, tagline Learning Outcome, nama trainer

Slide 2  — UNTUK SIAPA?
           Gambaran target peserta (ICP) — usia, kondisi, masalah

Slide 3  — MASALAH YANG DIRASAKAN
           Pain points utama dalam bentuk visual list

Slide 4  — TRANSFORMASI YANG DIJANJIKAN
           Before → After berdasarkan Learning Outcome

Slide 5  — KENAPA KELAS INI BERBEDA?
           Framework atau pendekatan yang digunakan

Slide 6..N — SATU SLIDE PER MODUL
           Judul modul + Session Outcome + highlight Why/What/How/What If

Slide N+1 — RINGKASAN KURIKULUM
           Tabel atau visual semua modul dalam satu pandangan

Slide N+2 — INVESTASI & DAFTAR
           Harga, apa yang didapat, CTA

Slide N+3 — PENUTUP
           Kalimat penutup kuat, info kontak trainer
```

Jumlah total slide: minimum 10, maksimum 20. Sesuaikan dengan kedalaman kurikulum.

---

### Fase 4: Buat Dokumen Konten Slide (.md)

Buat file .md yang berisi:

Untuk setiap slide, tulis:
- **Nomor & Judul Slide**
- **Tipe slide** (cover / pain / transformation / modul / cta / closing)
- **Warna background** yang digunakan dan alasannya
- **Konten utama** — judul, subjudul, body text, list
- **Visual suggestion** — ornamen, ikon, layout kolom, badge
- **Font size guidance** — judul berapa px, body berapa px

Format contoh satu slide dalam .md:

```
---
## Slide 3 — Masalah yang Dirasakan

**Background:** #111820 (deep dark — menegaskan keseriusan masalah)
**Warna teks:** White (#FFFFFF) dan Gray (#7A8FA6) untuk deskripsi
**Aksen:** Orange (#F4A261) untuk ikon pain point

**Label atas:** PAIN POINT (uppercase, warna aksen)
**Judul:** Yang Kamu Rasakan Setiap Hari
**Subjudul:** [kalimat pengantar]

**Pain List:**
- Ikon: 😓 | Judul: [pain 1] | Deskripsi: [kalimat konkret]
- Ikon: 💸 | Judul: [pain 2] | Deskripsi: [kalimat konkret]
- Ikon: 😰 | Judul: [pain 3] | Deskripsi: [kalimat konkret]

**Layout:** Full width, list vertikal dengan ikon bulat di kiri
**Ornamen:** Garis mint (hdiv) di bawah label
---
```

**Nama file .md:** `pitch-deck-[tema-produk].md`
Contoh: `pitch-deck-tl-berdaya.md`, `pitch-deck-kelas-saham.md`

---

### Fase 5: Buat Kode HTML (.html)

Buat file HTML lengkap yang siap ditempel di WordPress Elementor Custom HTML block.

#### Struktur HTML yang WAJIB dipertahankan (fungsional — jangan ubah):

```html
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>[Nama Kelas]</title>
</head>
<body>
<!--
  CARA PAKAI DI WORDPRESS:
  1. Buat halaman baru (Page)
  2. Pilih template "Full Width" / tanpa sidebar
  3. Tambahkan blok "Custom HTML"
  4. Salin semua isi file ini (dari <style> sampai </script>)
  5. Publish / Preview
  NAVIGASI: Desktop → tombol ‹ › atau keyboard ← → | HP → Swipe kiri-kanan
-->
<style>
/* RESET — scoped agar tidak ganggu tema WordPress */
#tl-deck, #tl-deck * { box-sizing: border-box; margin: 0; padding: 0; }
#tl-deck {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  z-index: 9990;
  overflow: hidden;
  /* CSS Variables — sesuaikan dengan tema materi */
  --c1: [WARNA UTAMA];
  --c2: [WARNA SEKUNDER];
  --c3: [WARNA AKSEN];
  --bg-dark: [WARNA BG GELAP];
  --bg-mid: [WARNA BG MENENGAH];
  --white: #FFFFFF;
  --gray: #7A8FA6;
  font-family: [PILIHAN FONT], Arial, sans-serif;
}
/* WordPress admin bar offset */
.admin-bar #tl-deck { top: 32px; height: calc(100% - 32px); }
@media screen and (max-width: 782px) {
  .admin-bar #tl-deck { top: 46px; height: calc(100% - 46px); }
}

/* SLIDE SYSTEM — jangan ubah */
#tl-deck .slide {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  justify-content: center; align-items: center;
  padding: 52px 88px;
  opacity: 0; pointer-events: none;
  transition: opacity 0.45s ease;
  overflow-y: auto; overflow-x: hidden;
}
#tl-deck .slide.active { opacity: 1; pointer-events: all; }

/* SLIDE THEMES — sesuaikan warna dengan materi */
#tl-deck .s-cover { background: [gradient sesuai tema]; color: var(--white); }
#tl-deck .s-dark  { background: var(--bg-dark); color: var(--white); }
#tl-deck .s-mid   { background: var(--bg-mid); color: var(--white); }
#tl-deck .s-light { background: [warna terang sesuai tema]; color: [warna teks gelap]; }

/* TYPOGRAPHY — sesuaikan dengan gaya materi */
#tl-deck h1 { font-size: 68px; font-weight: 700; line-height: 1.1; font-family: [font judul]; }
#tl-deck h2 { font-size: 42px; font-weight: 700; line-height: 1.2; }
#tl-deck h3 { font-size: 22px; font-weight: 700; }
#tl-deck p  { font-size: 16px; line-height: 1.75; }
#tl-deck .lbl { font-size: 12px; letter-spacing: 4px; text-transform: uppercase; color: var(--c2); margin-bottom: 16px; font-weight: 700; }
#tl-deck .hl { color: var(--c3); }
#tl-deck .muted { color: var(--gray); }
#tl-deck .hdiv { width: 50px; height: 3px; background: var(--c3); margin-bottom: 22px; }
#tl-deck .badge { display: inline-block; border-radius: 24px; padding: 6px 20px; margin-bottom: 24px; font-size: 11px; letter-spacing: 3px; text-transform: uppercase; font-weight: 700; }

/* LAYOUT — jangan ubah struktur, boleh sesuaikan gap/padding */
#tl-deck .block { width: 100%; max-width: 1100px; }
#tl-deck .col2 { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; width: 100%; }
#tl-deck .col3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; width: 100%; }
#tl-deck .col4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 18px; width: 100%; }

/* CARD — sesuaikan warna border dan background dengan tema */
#tl-deck .card { border-radius: 16px; padding: 24px 20px; }

/* PAIN LIST */
#tl-deck .pain { display: flex; align-items: flex-start; gap: 16px; padding: 14px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
#tl-deck .pain:last-child { border-bottom: none; }
#tl-deck .pain-ico { width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
#tl-deck .pain-t { font-size: 16px; font-weight: 700; margin-bottom: 4px; }
#tl-deck .pain-d { font-size: 14px; color: var(--gray); line-height: 1.6; }

/* MODULE CARD */
#tl-deck .mod { border-radius: 16px; padding: 20px; }
#tl-deck .mod-num { font-size: 42px; font-weight: 700; line-height: 1; margin-bottom: 10px; font-family: Georgia, serif; }
#tl-deck .mod-title { font-size: 17px; font-weight: 700; margin-bottom: 8px; }
#tl-deck .mod-out { font-size: 14px; line-height: 1.65; }

/* SLIDE NUM & LOGO — jangan ubah posisi */
#tl-deck .slide-num { position: absolute; top: 20px; right: 40px; font-size: 11px; color: rgba(255,255,255,0.15); font-family: 'Courier New', monospace; }
#tl-deck .logo { position: absolute; top: 20px; left: 40px; font-size: 12px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: var(--c2); }

/* NAVIGATION — jangan ubah */
#tl-nav {
  position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 16px;
  background: rgba(5,10,15,0.92); border: 1px solid rgba(255,255,255,0.1);
  border-radius: 50px; padding: 8px 22px;
  backdrop-filter: blur(12px); z-index: 9999;
}
#tl-nav .nbtn { width: 38px; height: 38px; border-radius: 50%; background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.12); color: #fff; font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background .2s; }
#tl-nav .nbtn:hover { background: var(--c1); border-color: var(--c1); }
#tl-nav #tl-sctr   { font-size: 13px; color: var(--gray); min-width: 50px; text-align: center; font-family: 'Courier New', monospace; }
#tl-nav #tl-stitle { font-size: 12px; color: var(--c2); max-width: 180px; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#tl-prog { position: fixed; top: 0; left: 0; height: 3px; background: linear-gradient(90deg, var(--c1), var(--c3)); transition: width .4s ease; z-index: 9999; }

/* RESPONSIVE TABLET */
@media (max-width: 1023px) and (min-width: 768px) {
  #tl-deck .slide { padding: 44px 48px; }
  #tl-deck h1 { font-size: 52px; }
  #tl-deck h2 { font-size: 34px; }
  #tl-deck .col3 { grid-template-columns: 1fr 1fr; }
  #tl-deck .col4 { grid-template-columns: 1fr 1fr; }
}

/* RESPONSIVE MOBILE — jangan ubah */
@media (max-width: 767px) {
  #tl-deck .slide { padding: 52px 22px 96px; justify-content: flex-start; align-items: flex-start; overflow-y: auto; }
  #tl-deck .block { max-width: 100% !important; }
  #tl-deck h1 { font-size: 36px !important; line-height: 1.15 !important; }
  #tl-deck h2 { font-size: 28px !important; }
  #tl-deck h3 { font-size: 18px !important; }
  #tl-deck .col2, #tl-deck .col3 { grid-template-columns: 1fr; gap: 14px; }
  #tl-deck .col4 { grid-template-columns: 1fr 1fr; gap: 10px; }
  #tl-nav { padding: 7px 16px; gap: 12px; bottom: 14px; }
  #tl-nav #tl-stitle { display: none; }
  #tl-deck .logo { font-size: 11px; left: 18px; top: 14px; }
  #tl-deck .slide-num { right: 18px; top: 14px; font-size: 10px; }
}
</style>

<div id="tl-prog"></div>
<div id="tl-deck">

  <!-- SLIDE 1: COVER -->
  <div class="slide s-cover active" data-title="[Nama Kelas]">
    ...konten cover...
    <div class="slide-num">01 / [TOTAL]</div>
  </div>

  <!-- SLIDE 2..N: konten sesuai kurikulum -->

</div>

<div id="tl-nav">
  <button class="nbtn" id="tl-pb" onclick="tlGo(-1)">&#8249;</button>
  <div id="tl-stitle">[Nama Kelas]</div>
  <div id="tl-sctr">1 / [TOTAL]</div>
  <button class="nbtn" id="tl-nb" onclick="tlGo(1)">&#8250;</button>
</div>

<script>
/* NAVIGATION SCRIPT — jangan ubah */
(function() {
  var slides = document.querySelectorAll('#tl-deck .slide');
  var total  = slides.length;
  var cur    = 0;
  function show(n) {
    slides[cur].classList.remove('active');
    cur = ((n % total) + total) % total;
    slides[cur].classList.add('active');
    slides[cur].scrollTop = 0;
    document.getElementById('tl-sctr').textContent = (cur + 1) + ' / ' + total;
    document.getElementById('tl-stitle').textContent = slides[cur].dataset.title || '';
    document.getElementById('tl-prog').style.width = (((cur + 1) / total) * 100) + '%';
    document.getElementById('tl-pb').style.opacity = cur === 0 ? '0.35' : '1';
    document.getElementById('tl-nb').style.opacity = cur === total - 1 ? '0.35' : '1';
  }
  window.tlGo = function(d) { show(cur + d); };
  document.addEventListener('keydown', function(e) {
    if (['ArrowRight','ArrowDown',' '].indexOf(e.key) !== -1) { e.preventDefault(); tlGo(1); }
    if (['ArrowLeft','ArrowUp'].indexOf(e.key) !== -1) { e.preventDefault(); tlGo(-1); }
  });
  var tx = 0, ty = 0;
  var deck = document.getElementById('tl-deck');
  deck.addEventListener('touchstart', function(e) { tx = e.changedTouches[0].clientX; ty = e.changedTouches[0].clientY; }, { passive: true });
  deck.addEventListener('touchend', function(e) {
    var dx = e.changedTouches[0].clientX - tx;
    var dy = e.changedTouches[0].clientY - ty;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 48) { if (dx < 0) tlGo(1); else tlGo(-1); }
  }, { passive: true });
  show(0);
})();
</script>
</body>
</html>
```

---

### Panduan Kustomisasi Visual per Tema

#### Saat mengisi `--c1`, `--c2`, `--c3`, dan background:

**Tema Travel / Pariwisata:**
```css
--c1: #028090;   /* teal */
--c2: #00A896;   /* teal terang */
--c3: #E8A838;   /* gold */
--bg-dark: #0A1520;
--bg-mid:  #0D1B2A;
font-family: 'Trebuchet MS', Arial, sans-serif;
```

**Tema Mindset / Personal Development:**
```css
--c1: #5C3D8F;   /* purple */
--c2: #9B72CF;   /* lavender */
--c3: #F4A261;   /* warm orange */
--bg-dark: #12091E;
--bg-mid:  #1A0F2E;
font-family: Georgia, serif; /* lebih personal */
```

**Tema Bisnis / Finansial:**
```css
--c1: #1A3A5C;   /* navy */
--c2: #2A7BA0;   /* steel blue */
--c3: #D4AF37;   /* gold */
--bg-dark: #080E18;
--bg-mid:  #0F1D30;
font-family: 'Trebuchet MS', Arial, sans-serif;
```

**Tema Digital / Teknis:**
```css
--c1: #0A2342;   /* dark navy */
--c2: #00D4FF;   /* electric blue */
--c3: #39FF14;   /* neon green */
--bg-dark: #030A14;
--bg-mid:  #071525;
font-family: 'Courier New', monospace; /* judul */ + Arial; /* body */
```

---

### Output Files

**File 1 — Dokumen Konten Slide (.md):**
Nama: `pitch-deck-[tema-produk].md`
Berisi: konten lengkap setiap slide, panduan warna, font, ornamen, dan layout

**File 2 — Kode HTML (.html):**
Nama: `pitch-deck-[tema-produk].html`
Berisi: kode lengkap siap copy-paste ke WordPress Elementor Custom HTML block

Simpan keduanya ke folder workspace user.

---

### Prinsip Wajib

- **Satu kurikulum → langsung proses** tanpa banyak tanya. Baca dokumen, tentukan visual, buat semua output.
- **Visual harus mencerminkan tema materi** — pitch deck Tour Leader tidak boleh terlihat seperti pitch deck startup teknologi.
- **Setiap slide harus punya satu pesan utama** — jangan terlalu banyak teks dalam satu slide.
- **HTML harus berfungsi tanpa modifikasi** — user langsung copy-paste dan langsung jalan di WordPress.
- **Nama file wajib mencerminkan tema produk** — bukan `slide.html` atau `pitch.md`.
