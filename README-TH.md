# แอป Camfrog แยกตามฟีเจอร์

ใช้ได้เฉพาะ Windows และใช้ UI Automation ค่า selector เริ่มต้นได้จากการวิเคราะห์ Camfrog 8.5.0.51219 แบบ static ควรกด **Auto-detect** กับรุ่นที่ติดตั้งจริงก่อนเปิดการทำงาน live มี smoke test บน Windows 11 เฉพาะเส้นทาง IM local echo แล้ว แต่ยังไม่ได้ยืนยันว่าผู้รับได้รับข้อความหรือทดสอบ control ทุกตัว

คู่มือผู้ใช้ฉบับเต็ม: `docs/USER-MANUAL-TH.md` (English: `docs/USER-MANUAL-EN.md`)

## แอปแยกตามฟีเจอร์

แต่ละ exe อยู่คนละโฟลเดอร์และสร้างไฟล์ตั้งค่า/ข้อมูลของตัวเอง ไม่ใช้ config, log, รายการสถานะ หรือประวัติร่วมกัน แอป Status Changer รวมหน้าจอไว้หน้าต่างเดียว แต่ยังแยก pool ของ Random กับ Marquee และนำ pool เดิมมาใช้หากพบโฟลเดอร์ข้อมูลเดิม

| แอป | ขอบเขต |
|---|---|
| `room-control\room-control.exe` | ควบคุมห้องและตอบอัตโนมัติในห้อง ปิดการเปลี่ยนสถานะและการตอบ IM ส่วนตัว |
| `chat-im-private\chat-im-private.exe` | แสดงและโฟกัสหน้าต่างแชทส่วนตัวที่เปิดอยู่ใน Camfrog ไม่มีการอ่านหรือส่งข้อความ |
| `status-changer\status-changer.exe` | หน้าต่างเดียว มีแท็บ **Random**, **Marquee** และ **Activity Log** โดยแยก config, pool สิบช่อง และการตั้งค่า worker ของแต่ละโหมด เก็บข้อมูลของตัวเองใน `status-data\` |
| `status-random\status-random.exe` | สลับสถานะแบบสุ่ม มี pool สิบช่องของตัวเองใน `random-data\` |
| `status-marquee\status-marquee.exe` | สลับสถานะแบบเลื่อน มี pool สิบช่องของตัวเอง ตัวควบคุม Loop ใน `marquee-data\` |
| `im-autoreply\im-autoreply.exe` | ตอบ IM ส่วนตัวอย่างเดียว ไม่ตอบในห้อง ไม่เปลี่ยนสถานะ |
| `music-dj\music-dj.exe` | ZeaZDev room ดีเจเพลงในห้อง: รับคำสั่ง `!request` / `!randommusic` / `!queue` / `!current` / `!skip` / `!help` จากแชท เก็บคิวและลิงก์ค้นหาเพลงที่สุ่มได้ลง `youtube_random_music.json` และเล่นเสียงในเครื่องหรือวิดีโอ YouTube ที่ player มาตรฐานมองเห็นได้ `!current` แสดงผู้ขอ เวลาเล่น และเพลงถัดไป ในแท็บ Music มี Windows TTS อ่านข้อความหรือสรุปคิว โดยต่อเอาต์พุตเสียงเข้าไมโครโฟน Camfrog ปิดการเปลี่ยนสถานะและการตอบ IM ส่วนตัว |
| `web-status\web-status.exe` | รุ่นทดลองแบบ GUI + console: เปลี่ยนสถานะผ่าน profiles.camfrog.com (ค่าเริ่มต้นโหมดทดลอง; GUI ตรวจสอบ session `PHPSESSID` จากเบราว์เซอร์ก่อนเริ่มเปลี่ยนสถานะจริง) ไม่ใส่ argument เปิด GUI ใส่ argument ใช้ CLI ใน GUI มีช่องข้อความบรรทัดเดียว 10 ช่อง บันทึกใน `webtext.db`; ปุ่ม **Start** วนเฉพาะบรรทัดที่ไม่ว่างทุก N วินาที |
| `name-color-changer\name-color-changer.exe` | สลับสีชื่อเล่นผ่าน profiles.camfrog.com: ไม่ใส่ argument หรือใส่ `--gui` เปิด GUI ใส่ argument อื่นใช้ CLI ค่าเริ่มต้นเป็นโหมดทดลอง (`--confirm` เพื่อเปลี่ยนสีจริง) ตรวจสอบ session `PHPSESSID` จากเบราว์เซอร์ก่อนเริ่มสลับจริง บังคับช่วงเวลาสลับขั้นต่ำและจำนวนรอบสูงสุด และคืนค่าสีเดิมเมื่อออกจากโปรแกรม |
| `icon-changer\icon-changer.exe` | สลับไอคอนห้องผ่าน profiles.camfrog.com: ไม่ใส่ argument หรือใส่ `--gui` เปิด GUI ใส่ argument อื่นใช้ CLI (`--alternate-icons --icon-a/--icon-b`) ค่าเริ่มต้นเป็นโหมดทดลอง (`--confirm` เพื่อเปลี่ยนไอคอนจริง) ผ่านการตรวจสอบ session แบบ fail-closed พร้อมข้อจำกัดเดียวกัน และคืนค่าไอคอนเดิมเมื่อออกจากโปรแกรม |
| `name-changer\name-changer.exe` | สลับชื่อแสดงในหน้าต่าง Camfrog รองรับภาษาไทยเต็มรูปแบบและฟอนต์ไทย: กำหนดรายชื่อได้ สลับแบบ manual/กำหนดเองหรือ `--cycle` และตรวจสอบชื่อแบบ fail-closed ไม่ใส่ argument หรือใส่ `--gui` เปิด GUI; `--set-name` และ `--names` ใช้ CLI |

หน้า **Bot Manager** ของ Web Status ควบคุมคิวใน `music-dj.exe` แยกตัวผ่านไฟล์ในเครื่อง และอ่าน/ควบคุม vMix บนเครื่องเดียวกันได้ Music DJ รับ URL หรือ ID YouTube เข้าคิว เปิด player มาตรฐานที่มองเห็นได้ และเลื่อนคิวตาม event เล่น/จบเพลง ดูขอบเขตและรายการตรวจรับ Windows ที่ [`docs/zBOT-Youtube-MUSIC-DJ.md`](docs/zBOT-Youtube-MUSIC-DJ.md)

Room Control และ IM Auto-reply ใช้ `config.json` แยกกัน ส่วน Chat IM Private ใช้ `private-chat-config.json` และ Random/Marquee ใช้ `camfrog-status-config.json` ในโฟลเดอร์ข้อมูลคนละชุด แต่ละแอปแยก pool, log, ประวัติ และ worker standalone ทุกแอปที่ใช้ Camfrog controls จะลองค้นหา control หลังเปิดหน้าต่าง และบันทึก selector เมื่อ config ผ่าน validation; Web Status ใช้ control บนเว็บไซต์จึงไม่รวมอยู่ด้วย ปุ่ม **Start** สร้างโฟลเดอร์ข้อมูล เปิด worker และรอการลงทะเบียน/อนุญาต PID ก่อนเริ่มส่งสถานะ

## สร้าง exe

ดับเบิลคลิก `build.bat` (ต้องมี Python 64-bit) ระบบเลือก Python 3.12 และสร้างสิบเอ็ดแอปแยกโฟลเดอร์ใน `dist\` โดยเก็บ config/ข้อมูลเดิมของแต่ละแอปไว้เมื่อ build ใหม่ ไม่คัดลอก config กลาง และสร้าง zip ที่มีเฉพาะ exe กับ checksums นอกจากนี้ยังเขียน `dist\SHA256SUMS.txt`

ใช้ `full-build.bat` หรือ `full-build.ps1` เพื่อ build แอปทั้งหมดรวมแอป LINE โดยจะ publish release ก็ต่อเมื่อระบุ `--release`; PowerShell รับ `--release vX.Y.Z` เมื่อเวอร์ชันตรงกับ `APP_VERSION` เท่านั้น

- **ไอคอน:** เปลี่ยนไฟล์ `app.ico` (ไฟล์ `.ico` หลายขนาดจริง 16–256 px) แล้ว build ใหม่ ถ้า Explorer ยังโชว์ไอคอนเก่าคือ cache ของ Windows ให้ย้ายหรือเปลี่ยนชื่อ exe หรือรีสตาร์ท Explorer
- **Checksums และ zip:** `SHA256SUMS.txt` ระบุ path ของ exe แต่ละตัว และสร้าง zip ด้วย Python ตรวจแฮชด้วย `certutil -hashfile dist\room-control\room-control.exe SHA256`

### แก้ปัญหา build

รัน **`doctor.bat`** ก่อน จะบอกเวอร์ชัน/บิตของ Python ตรวจรุ่นที่ใช้ไม่ได้ ตรวจว่าไฟล์โปรเจกต์เป็นของเก่าไหม และลองโหลด `pywin32`

ถ้า pip ติดตั้ง dependency ไม่ได้ (`pywin32 ... no matching distribution`) โปรเจกต์นี้รองรับเฉพาะ CPython 3.12 รุ่นปกติ แบบ 64-bit ให้ติดตั้ง `winget install Python.Python.3.12` ลบ `.venv` แล้วรัน `build.bat` ใหม่

ถ้า error ขึ้น `requirements.txt (line 1)` และพูดถึงแต่ pywinauto แปลว่าใช้ไฟล์เก่า ให้แตก zip ล่าสุดลงโฟลเดอร์ใหม่

## เริ่มต้นใช้งาน

1. เปิด Camfrog แล้วเข้าห้องก่อนเปิดแอป standalone แอปจะลองค้นหา control ให้อัตโนมัติ ใช้ **Setup → Auto-detect** หรือ **Discover controls** เพื่อลองใหม่ ผลวิเคราะห์: `docs/CAMFROG-8.5-FINDINGS.md`
2. แก้ `config.json` ของแอปนั้น ตั้งนิค ตั้งกฎตอบ คง `dry_run` ไว้ขณะทดสอบ
3. ใช้ `im-autoreply.exe` สำหรับตอบ IM ส่วนตัว config และ log แยกจาก Room Control
4. ใช้ `status-random.exe` หรือ `status-marquee.exe` สำหรับแอปแยก หรือ `status-changer.exe` สำหรับหน้ารวมพร้อม Activity Log แต่ละแอปเก็บข้อมูลของตัวเอง โหมด strict background ยังหยุดด้วย `BACKGROUND_UNSUPPORTED` โหมดเดิม `best_effort_background` ถูกปิดหลังทดสอบบน Windows พบว่า Camfrog ดึง focus ได้และทำให้คีย์บอร์ดถูกรบกวน worker จึงปฏิเสธจนกว่าจะยืนยันเส้นทางแยก desktop ได้ โหมด interactive จะรอให้ผู้ใช้หยุดใช้งาน desktop ก่อนส่ง ดู [`docs/STATUS-SUBMIT-MODES.md`](docs/STATUS-SUBMIT-MODES.md) ส่วน `web-status.exe` เป็นฟีเจอร์แยก
5. เปิดแชทส่วนตัวใน Camfrog แล้วใช้ `chat-im-private.exe` refresh และโฟกัสหน้าต่าง แอปจะลองค้นหา selector ตอนเปิด และยังใช้ **Discover controls** เพื่อบันทึกรายการ control ลง `controls.txt` สำหรับตรวจวิเคราะห์ได้

คำสั่ง `python camfrog_auto.py ...` ยังใช้เป็น CLI สำหรับพัฒนา โดยใช้ `config.json` ในเครื่องซึ่ง Git จะไม่ติดตาม สร้างได้ด้วย `python camfrog_auto.py init --config config.json` หรือคัดลอกจาก `config.example.json` ส่วน CI และ build ตรวจด้วยตัวอย่างที่ปลอดภัย CLI นี้ไม่ใช่หนึ่งในสิบเอ็ดแอปที่ build คำสั่งใน `extras\*.bat` เป็นทางลัด ยกเว้น `extras\gui.bat` ที่เปิด `dist\room-control\room-control.exe` ที่ build แล้ว

คำสั่ง source-only `python camfrog_presence.py probe NICKNAME` เรียก probe หนึ่งครั้งแบบมีขอบเขตและเก็บหลักฐานรูปสถานะสาธารณะของ Camfrog ลง SQLite ในเครื่อง โดยจัดสถานะเป็น `UNKNOWN` เสมอ อ่าน [`docs/CAMFROG-PRESENCE.md`](docs/CAMFROG-PRESENCE.md) ก่อนแปลผลตัวอย่าง

## ZChat Control Server

Control plane แยกต่างหากสำหรับใช้งานในเครื่อง ใช้ FastAPI และ PostgreSQL ใน Docker พร้อม volume ถาวรบนเครื่องนี้ มี dashboard สำหรับผู้ดูแล ระบบบัญชีและ agent คิวคำสั่งแบบ lease, audit และ snapshot โปรไฟล์/presence ที่ตัดข้อมูลอ่อนไหวแล้ว การดึงข้อมูล Camfrog ด้วย session จริง, Cloud Bot transport, การชำระ Store และ production deployment ยังไม่ยืนยันหรือยังปิดไว้ อ่านวิธีติดตั้ง ขอบเขต API และข้อจำกัดที่ [`control-server/README.md`](control-server/README.md)

## รันเบื้องหลัง

Room Control และ IM Auto-reply มีปุ่ม **Save & Start** กับ **Stop** ส่วนแอปสถานะมีปุ่มของตัวเองและ worker ที่มากับ exe

| คำสั่ง | คำอธิบาย |
|---|---|
| `python camfrog_auto.py start --config config.json` | CLI จาก source: รันแบบซ่อน |
| `python camfrog_auto.py state --config config.json` | CLI จาก source: ดูสถานะ |
| `python camfrog_auto.py stop --config config.json` | CLI จาก source: หยุดการทำงาน |

แต่ละแอปบันทึก log ในโฟลเดอร์ของ config ตัวเอง ส่วน source CLI ใช้ `camfrog_auto.log` ในโฟลเดอร์ repository

**การส่งสถานะ:** โหมด strict background คืน `BACKGROUND_UNSUPPORTED` ก่อนเขียนหรือส่ง เพราะยังไม่มีเส้นทาง Camfrog 8.5 ที่ยืนยันว่าไม่ activate หน้าต่าง โหมด `best_effort_background` ถูกปิด เพราะ Camfrog อาจดึง focus ระหว่างส่งสถานะและขัดจังหวะการพิมพ์ worker ปฏิเสธโหมดนี้ก่อนเขียนข้อความ เส้นทาง interactive ที่ใช้โฟกัส เมาส์ หรือปุ่มจริงจะรอให้ไม่มีการใช้งาน 2 วินาที (ปรับด้วย `ZCHAT_USER_IDLE_MS`; 0 ปิดการรอ) และเลื่อนการส่งหากผู้ใช้ยังทำงานต่อเนื่องเกิน 120 วินาที วิธีนี้ลดการชนกับผู้ใช้ แต่ไม่ได้ทำให้การส่งไม่ activate หน้าต่าง การเริ่ม worker สร้างโฟลเดอร์ข้อมูลและรอการอนุญาต PID ก่อนเข้าลูป ส่วนการตอบแชทในห้องใช้ช่องกรอกอีกชนิดหนึ่งและต้องให้หน้าต่างห้องอยู่หน้าสุด

## สถานะเลื่อน + สลับจากประวัติ

- **สถานะเลื่อน** (`status.marquee`): ข้อความยาวเลื่อนผ่านหน้าต่างกว้าง `width` ตัวอักษร เฟรมละ `step_seconds` วินาที (ขั้นต่ำ 0.3 วินาที; ปรับใน GUI ทีละ 0.1 วินาที) แต่ละช่องที่มีข้อความจะเลื่อนจนจบก่อนเปลี่ยนไปช่องถัดไป **Loop slots** จะเริ่มช่อง 1 ใหม่หลังช่องสุดท้าย หากปิดไว้จะหยุดหลังช่องสุดท้าย ค่าเริ่มต้นเลื่อน 2 กลุ่มอักษรทุก 0.5 วินาที ตัวรันตื่นตามกำหนดเฟรม โดยไม่ตัดสระ/วรรณยุกต์และไม่สร้างเฟรมว่าง
- **สลับจากประวัติ** (`status.history`): ทุก status ที่ตั้งถูกเก็บลง `status_history.json` ถ้าเปิด `use_as_source` จะสลับเลือกจากประวัติให้อัตโนมัติ (`rotate` = วนรอบ, `random`, `most_used`) ข้อความใน `status.messages` ถูกใส่เข้าไปอัตโนมัติ
- **สลับไทย-อังกฤษอัตโนมัติ** (`status.language_cycle: ["th","en"]`): สลับไทย→อังกฤษ→ไทย… ทุกครั้งที่เปลี่ยน status ใช้ได้ทั้งประวัติและข้อความปกติ
- **ความปลอดภัย:** ทุกเฟรมคือการเปลี่ยน status จริง จึงจำกัดความถี่ขั้นต่ำ 0.3 วินาที ควรตั้งช่วงเวลาให้เหมาะสมและเริ่มด้วย `dry_run: true`
- **ส่งสถานะจริง:** แอปสถานะเริ่มในโหมดทดลอง ปุ่ม **Save Data** บันทึกค่าเท่านั้น โหมด strict คืน `BACKGROUND_UNSUPPORTED`; `best_effort_background` ถูกปิด เพราะอาจดึง focus และรบกวนคีย์บอร์ด โหมด interactive รอให้ desktop ว่างก่อนใช้โฟกัสหรืออินพุตจริง `web-status.exe` ใช้เส้นทางเว็บไซต์แยกต่างหาก
- **ตัวเลือก background status:** เลือก `strict_background` เพื่อปฏิเสธแบบ fail-closed และอย่าเลือกโหมดเดิม `best_effort_background` ซึ่งถูกปิดเพราะอาจดึง focus ดู `status.execution_mode` ใน `CONFIG-TH.md` และ `docs/STATUS-SUBMIT-MODES.md`

ดูตัวอย่างโดยไม่ต้องเปิด Camfrog:
```
python camfrog_auto.py marquee "ข้อความยาว ๆ ที่อยากให้เลื่อน" --width 20
python camfrog_auto.py history                       # ดูประวัติ
python camfrog_auto.py history-add "สวัสดีครับ"        # เพิ่ม
python camfrog_auto.py history-import old_status.txt # ไฟล์ละบรรทัด
```

## เครื่องมือเสริม

| คำสั่ง | คำอธิบาย |
|---|---|
| `python camfrog_auto.py init --config config.json` | CLI จาก source: สร้าง config เริ่มต้น |
| `python camfrog_auto.py test-rules "text" --config config.json` | CLI จาก source: ทดสอบกฎแบบออฟไลน์ |
| `python camfrog_auto.py state --config config.json` | CLI จาก source: ดูสถานะและสถิติ |

- **แก้แล้วมีผลทันที:** แก้ไฟล์ตอนรันได้ ถ้าไม่ถูกต้องจะไม่รับและ log ไว้ (การแก้ `dry_run` เป็น `false` โดยไม่มี `own_nickname` จะถูกปฏิเสธ)
- **ตัวแปร:** `{sender}` `{me}` `{time}` `{date}` ใช้ได้ทั้ง status และคำตอบ
- **ตั้งเวลา:** ข้อความ status ต่างกันตามช่วงเวลา (`status.schedules`)
- **ตอบเมื่อถูกเรียกชื่อ:** `"mention": true` ตอบเมื่อมีคนพิมพ์นิคคุณ
- **กันสแปม:** ไม่ตอบข้อความที่มีลิงก์หรือตรงรูปแบบที่กำหนด (`ignore_links` เปิดเป็นค่าเริ่มต้น และ `skip_patterns`)

## พัฒนา

ติดตั้ง `requirements-dev.txt` แล้วรัน test suite แบบออฟไลน์จาก source CI (`.github/workflows/ci.yml`) รัน test บน Ubuntu และ build แอป Camfrog กับ LINE บน Windows หมายเหตุ audit: `AUDIT.md` คู่มือผู้ใช้ฉบับเต็ม: `docs/USER-MANUAL-TH.md`

## ภาษา

`"language": "auto"` ตามภาษาหน้าจอ Windows เปลี่ยนด้วย `--lang th` หรือ `--lang en` รายการสถานะเป็นคู่ `{th, en}` ได้ และคำตอบตามภาษาข้อความที่เข้ามา — ดู `CONFIG-TH.md`

## ตอบแชทส่วนตัว (ต้องเปิดใช้เอง)

ใช้ `im-autoreply.exe` config ของแอปนี้แยกเฉพาะ แชทในห้องไม่เคยยุ่งกับแชทส่วนตัว การตอบ IM เป็นฟีเจอร์แยก มีกฎของตัวเอง **ต้องระบุรายชื่อเพื่อน** (`only_nicknames`) มี dry-run แยก และเพดานที่เข้มกว่า (ค่าเริ่มต้น: ตอบเพื่อนคนละ 1 ครั้งต่อชั่วโมง วันละ 3 ครั้ง รวมชั่วโมงละ 10 ครั้ง) ทุกคำตอบขึ้นต้นด้วย `[auto] ` และจะไม่ตอบบรรทัดที่มีคำนำหน้านี้ หน้าต่างที่เปิดอยู่ก่อนบอทเริ่มจะไม่ถูกตอบ หน้าต่างที่เปิดใหม่ทีหลังและมีแค่ 1 บรรทัดจากเพื่อนในรายชื่อจะถูกตอบ

ตั้งค่า `autoreply.own_nickname`, `autoreply_im.only_nicknames` และ `autoreply_im.rules` ใน config ของแอปนี้ และคง `dry_run: true` จนตรวจสอบการทำงานแล้ว แล้วค่อยเปิดโหมดจริงเฉพาะแอปนี้เมื่อพร้อม

หลักฐาน smoke test บน Windows 11: worker จับหน้าต่างแชตส่วนตัวที่เลือก และพบ local echo ของข้อความขาออกในประวัติ MSHTML ของหน้าต่างนั้นแล้ว แอปเลื่อนบรรทัดดังกล่าวเข้าพื้นที่แชตด้วย แต่ไม่ได้ยืนยันว่า Camfrog ส่งถึงผู้รับ เพราะ UI ไม่มีการตอบรับการส่ง ชนิดหน้าต่าง `wb-log-mtim-data` ถูกข้ามโดยตั้งใจ และจะไม่รับคำขอเปิดแชต (`private_chat_request`) อัตโนมัติ ดู `CONFIG-TH.md`

## ความปลอดภัย

`dry_run` เปิดเป็นค่าเริ่มต้น คำตอบถูกจำกัดความถี่ (คูลดาวน์รายคน ช่องว่างรวม เพดานรายชั่วโมง) บรรทัดที่ไม่ใช่รูป `ชื่อ: ข้อความ` ถูกข้าม คำตอบที่ขึ้นต้นด้วย `/` ถูกบล็อก ไม่มีการเตะอัตโนมัติ ตรวจเงื่อนไข Camfrog และกฎห้องก่อนเปิดระบบอัตโนมัติ
