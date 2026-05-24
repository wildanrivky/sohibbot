---
name: transcript-to-cheatsheet
description: >
  Gunakan skill ini setiap kali pengguna ingin membuat cheat sheet, ringkasan, atau panduan cepat dari transkrip video — baik berupa file .sbv (subtitle YouTube), .srt, maupun .txt. Skill ini mengekstrak poin-poin actionable dari tiap chapter/modul, lalu menghasilkan file HTML berformat A4 yang siap dicetak atau dibagikan via WhatsApp/email. Trigger skill ini ketika pengguna menyebut: "buat cheat sheet dari transkrip", "rangkum video saya", "buat ringkasan kelas/course", "jadikan PDF dari caption YouTube", "buat panduan cepat dari materi", atau ketika ada folder berisi file .sbv/.srt/.txt yang merupakan transkrip per chapter.
---

# Transcript → Cheat Sheet

Skill ini mengubah transkrip video (per chapter) menjadi cheat sheet HTML berformat A4 yang profesional, ringkas, dan siap pakai.

## Kapan digunakan
- Ada file transkrip (.sbv, .srt, atau .txt) dari video course, webinar, atau pelatihan
- Pengguna ingin ringkasan materi dalam format yang bisa dibagikan atau dicetak
- Output diminta sebagai "cheat sheet", "panduan cepat", "rangkuman", atau sejenisnya

---

## Langkah 1 — Temukan dan baca semua file transkrip

Cari file transkrip di folder yang disebutkan pengguna. File bisa berformat:
- `.sbv` — format caption YouTube (ada timestamp per baris)
- `.srt` — format subtitle standar (ada nomor urut + timestamp)
- `.txt` — teks biasa

**Cara membersihkan timestamp dari .sbv/.srt:**
```python
import re

def extract_text(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.split('\n')
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\d+$', line):
            continue
        if re.match(r'^\d+:\d+:\d+', line):
            continue
        text_lines.append(line)
    return ' '.join(text_lines)
```

Jika ada banyak file (satu per chapter), baca semua sekaligus dengan loop.

---

## Langkah 2 — Ekstrak poin-poin kunci per chapter

Untuk setiap chapter, ambil **3–6 poin paling actionable**. Fokus pada:
- Hal yang bisa langsung dilakukan di lapangan
- Checklist, langkah berurutan, atau tips konkret
- Hindari teori panjang — ambil intisarinya saja

Setiap poin:
- Dimulai dengan kata kerja atau kata kunci yang bold
- Cukup 1–2 kalimat
- Bisa berdiri sendiri tanpa konteks tambahan

---

## Langkah 3 — Susun struktur halaman

Kelompokkan chapter ke dalam 2–4 bagian tematik. Target: **2–4 halaman A4**.

---

## Langkah 4 — Buat file HTML

Simpan dengan nama: `[C] Cheat Sheet - [Nama Course].html`

**CSS template yang sudah diuji untuk A4:**

```css
@page { size: A4; margin: 0; }
.page { width: 210mm; height: 297mm; overflow: hidden; padding: 9mm 11mm 8mm 11mm; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 3.5mm; }
```

Warna per kelompok chapter:
- `c-blue` (#1a4a7a) → SOP, persiapan, dasar
- `c-green` (#2e7d32) → pengetahuan lapangan, operasional
- `c-purple` (#5c0d8a) → lokasi, navigasi
- `c-red` (#b8000f) → problem solving, darurat

---

## Panduan distribusi konten per halaman

- Cover header: ~25mm
- Section label: ~6mm
- Tiap chapter (header + 4-5 bullet): ~28–35mm
- Footer: ~10mm
- Dua kolom = dua chapter per baris

Jika konten terlalu padat: kurangi bullet per chapter (5 → 3–4), jangan perkecil font.

---

## Output

File disimpan di folder project dengan prefix `[C]`. Beritahu pengguna cara convert ke PDF: buka di browser → Ctrl+P → Save as PDF.
