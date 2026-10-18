# คู่มือ config

English version: `CONFIG-EN.md`.

key ที่ไม่ใส่จะใช้ค่าเริ่มต้น คู่มือนี้อธิบาย config ของ source CLI ส่วนแอปที่ build แล้วสร้าง config แยกในโฟลเดอร์ของแต่ละแอป `config.json` อยู่ในเครื่องและ Git ไม่ติดตาม สร้างด้วย `python camfrog_auto.py init --config config.json` หรือคัดลอกจาก `config.example.json` ตรวจตัวอย่างด้วย `python camfrog_auto.py check --config config.example.json`

| คีย์ | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|
| `language` | `auto` | ภาษาของข้อความโปรแกรม/ล็อก (`--lang` ทับค่านี้) |
| `reload_config` | `true` | โหลด config ใหม่เองเมื่อไฟล์เปลี่ยน (ถ้าไม่ถูกต้องจะไม่รับ) |
| `stats.enabled/file/write_seconds` | `true` / `stats.json` / `60` | ตัวนับที่ `state` แสดง |
| `window_title_regex` | `.*Camfrog.*` | regex ชื่อหน้าต่าง Camfrog |
| `dry_run` | `true` | `true` = แค่ log ไม่ส่งจริง แอป Random/Marquee มีตัวเลือก **Send statuses live to Camfrog** ถ้าไม่เลือกจะยังเป็นโหมดทดลอง |
| `poll_seconds` | `1.5` | ความถี่ลูปหลัก (0.2–60) |
| `log.level/file/max_bytes/backups` | `INFO` / `camfrog_auto.log` | ล็อกหมุนไฟล์ **โหมดเบื้องหลังต้องมี `file`** |
| `log.log_message_text` | `true` | `false` = ไม่เก็บเนื้อข้อความใน log |
| `active_hours` | disabled | ทำงานเฉพาะช่วงเวลา (ข้ามเที่ยงคืนได้) นอกช่วงจะอ่านแต่ไม่ตอบ |
| `safety.stop_file` | `STOP` | สร้างไฟล์นี้เพื่อหยุด (ลบเองตอนเริ่มรอบใหม่) |
| `safety.pid_file` | `camfrog_auto.pid` | ไฟล์ PID สำหรับโหมดเบื้องหลัง/กันรันซ้ำ |
| `safety.require_foreground` | `true` | ไม่กด Enter ถ้า Camfrog ไม่อยู่หน้าสุด |
| `safety.discovery_mode` | `legacy` | `legacy` หาหน้าต่าง Camfrog ด้วย title regex; `verified` ระบุตัวตนด้วย executable identity, ลายเซ็น Authenticode, PID+creation time และ window role เชิงโครงสร้าง ถ้ากำกวมจะหยุดแบบ fail-closed |
| `safety.restore_previous_window` | `true` | ส่งเสร็จแล้วคืนโฟกัสให้หน้าต่างเดิม ตั้ง `false` เพื่อค้างหน้าต่างแชต Camfrog ไว้ด้านหน้า IM auto-reply จะเลื่อนข้อความ echo ฝั่งเครื่องเข้าพื้นที่แชตไม่ว่าค่านี้จะเป็นอะไร |
| `safety.max_consecutive_failures` | `5` | หยุดเมื่อล้มเหลวติดกัน N ครั้ง |
| `status.enabled` | `true` | หมุน status |
| `status.execution_mode` | `interactive` | `interactive` ใช้อินพุต desktop จริงและอาจเปลี่ยนโฟกัส โดยรอให้ผู้ใช้ไม่ใช้งาน (ค่าเริ่มต้น 2 วินาที ปรับด้วย `ZCHAT_USER_IDLE_MS` รอสูงสุด 120 วินาที); `strict_background` ปฏิเสธด้วย `BACKGROUND_UNSUPPORTED`; `best_effort_background` โพสต์ Enter ไปยัง combo โดยไม่ใช้อินพุตทั่วระบบ แต่ Camfrog อาจ activate/กะพริบและไม่ยืนยันผลจากผู้ให้บริการ |
| `status.edit` / `apply_button` | selectors / `null` | selector จาก `discover` ถ้าใส่ `apply_button` จะกดปุ่มแทน Enter |
| `status.background_enter_target` | `edit` | คงค่าเลือกเป้าหมาย submit รุ่นเดิมเพื่อความเข้ากันได้ แต่ไม่ข้าม capability gate ของ strict mode และไม่ใช่หลักฐานว่ารองรับการส่งเบื้องหลังโดยไม่รบกวนผู้ใช้ |
| `status.interval_seconds` | `600` | ขั้นต่ำ 0.3 |
| `status.retry_seconds` | `30` | รอก่อนลองส่งใหม่เมื่อล้มเหลว (1–3600) |
| `status.set_on_start` | `true` | ตั้ง status แรกหลัง worker เริ่มทำงาน 10 วินาที |
| `status.random` | `false` | สุ่มแทนเรียงลำดับ |
| `status.language_mode` | `both` | สำหรับรายการแบบ `{th,en}`: `th`, `en`, `both` (ต่อกัน), `alternate` (สลับรอบ) |
| `status.schedules[]` | `[]` | ชุดข้อความตามช่วงเวลา (ข้ามเที่ยงคืนได้) นอกช่วงใช้ `status.messages` |
| `status.language_cycle` | `[]` | สลับภาษาทุกครั้งที่เปลี่ยน status |
| `status.marquee.enabled` | `false` | เลื่อนข้อความยาวทีละเฟรม |
| `status.marquee.scroll` | `false` | `false` = ส่งทีละ 1 บรรทัดเต็มต่อรอบ (วน 1..N) `true` = เลื่อนทีละเฟรมตามด้านล่าง |
| `status.marquee.width` / `stride` | `28` / `2` | ความกว้างหน้าต่าง / จำนวนกลุ่มอักษรที่เลื่อนต่อเฟรม |
| `status.marquee.step_seconds` | `0.5` | วินาทีต่อเฟรม (**ขั้นต่ำ 0.3**); ตัวรันจะตื่นตามกำหนดเฟรมเพื่อลดเวลารอเกิน |
| `status.marquee.cycles` / `max_frames` | `1` / `80` | จำนวนรอบแบบจำกัด และจำนวนเฟรม ticker สูงสุดต่อสถานะ (5–300); แบบจำกัดจะจบที่ข้อความเต็ม |
| `status.marquee.infinite_loop` | `false` | `true` = วนชุดเฟรมซ้ำจนถึง `status.interval_seconds` แล้วเปลี่ยนข้อความถัดไป; ละเว้น `cycles` |
| `status.marquee.separator` | `   •   ` | ช่องว่างระหว่างท้ายข้อความกับจุดเริ่มใหม่ |
| `status.history.enabled` / `file` | `true` / `status_history.json` | เก็บทุก status ที่ตั้ง |
| `status.history.use_as_source` | `false` | `true` = เลือก status ถัดไปจากประวัติ (`schedules` จะไม่ถูกใช้) |
| `status.history.mode` | `rotate` | วนรอบ / สุ่ม / ใช้บ่อยสุด |
| `status.history.seed_from_messages` | `true` | คัดลอก `status.messages` เข้าประวัติตอนเริ่ม |
| `status.history.record` / `max_items` | `true` / `50` | บันทึกที่ส่ง / เก็บสูงสุด N รายการ |
| `status.max_length` | `120` | ตัดความยาว status |
| `status.messages` | `[]` | ข้อความล้วน หรือ object `{th,en}` |
| `autoreply.enabled` | `true` | ตอบแชทอัตโนมัติ |
| `autoreply.window_title_regex` | `""` | ตัวกรองเพิ่มชื่อหน้าต่างห้อง (ว่าง = ไม่กรองเพิ่ม) |
| `autoreply.log_kind` | `room` | log ที่บอทห้องอ่าน: `room`, `im` หรือ `any` |
| `autoreply.history_tail` | `150` | จำนวนบรรทัดแชทที่อ่านต่อรอบเพื่อตัดสินใจตอบ |
| `autoreply.own_nickname` | `""` | นิคของคุณ (ไม่ตอบตัวเอง) **จำเป็นเมื่อรันจริง** |
| `autoreply.history` / `input` | selectors | control ประวัติแชท / ช่องพิมพ์ |
| `autoreply.ignore_nicknames` / `only_nicknames` | `[]` | ข้ามนิคเหล่านี้ / ตอบเฉพาะนิคเหล่านี้ |
| `autoreply.per_sender_cooldown_seconds` | `300` | ระยะห่างขั้นต่ำต่อคน |
| `autoreply.global_min_gap_seconds` | `20` | ระยะห่างขั้นต่ำระหว่างคำตอบ |
| `autoreply.max_per_hour` | `30` | เพดานต่อชั่วโมง |
| `autoreply.delay_range_seconds` | `[2,5]` | หน่วงสุ่มก่อนตอบ |
| `autoreply.max_reply_length` / `max_incoming_length` | `200` / `500` | จำกัดความยาวคำตอบ / ข้อความขาเข้า |
| `autoreply.skip_patterns` | `[]` | regex ที่ตรงแล้วจะไม่ตอบ |
| `autoreply.ignore_links` | `true` | ไม่ตอบข้อความที่มีลิงก์ |
| `autoreply.rules[]` | `[]` | `pattern` (regex), `reply`, และเลือกใส่ `cooldown_seconds`, `enabled`, `lang`, `mention` (ตอบเมื่อมีนิคคุณในข้อความ) กฎแรกที่ตรงชนะ จึงควรวางกฎเฉพาะไว้ก่อน |

## ตอบแชทส่วนตัวอัตโนมัติ

ปิดไว้เป็นค่าเริ่มต้น แยกจากการตอบในห้อง มีกฎ รายชื่อเพื่อน และเพดานของตัวเอง ใช้ selector และ `own_nickname` ชุดเดียวกับ `autoreply` แตะเฉพาะหน้าต่างที่ log pane เป็น `wb-log-im-data` หน้าต่างห้องและชนิด `wb-log-mtim-data` ที่ยังไม่ยืนยันจะไม่ถูกตอบ ใน source CLI ใช้ `python camfrog_auto.py im-probe --config config.json` แสดงปัญหา config และแยกชนิดหน้าต่าง ทดสอบออฟไลน์ด้วย `python camfrog_auto.py test-rules "text" --sender Friend --im --config config.json`

| คีย์ | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|
| `autoreply_im.enabled` | `false` | เปิดใช้การตอบแชทส่วนตัว |
| `autoreply_im.dry_run` | `true` | IM มี dry-run ของตัวเอง ถ้า `dry_run` หลักเป็น `true` จะชนะเสมอ |
| `autoreply_im.only_nicknames` | `[]` | **ต้องมีอย่างน้อย 1 คน** ตอบเฉพาะเพื่อนในรายการนี้ |
| `autoreply_im.rules[]` | `[]` | รูปแบบเดียวกับ `autoreply.rules` แต่ใช้เฉพาะ IM กฎของห้องไม่ตอบแชทส่วนตัว |
| `autoreply_im.skip_patterns` / `ignore_links` | `[]` / `true` | ไม่ตอบข้อความที่ตรง / มีลิงก์ |
| `autoreply_im.prefix` | `"[auto] "` | ใส่หน้าทุกคำตอบให้เพื่อนรู้ว่าเป็นอัตโนมัติ ข้อความที่ขึ้นต้นด้วยค่านี้จะไม่ถูกตอบ ห้ามขึ้นต้นด้วย `/` |
| `autoreply_im.answer_first_message` | `true` | หน้าต่างที่เปิดใหม่ขณะบอทรันและมีแค่ 1 บรรทัดจากเพื่อนในรายการจะถูกตอบ หน้าต่างที่เปิดอยู่แล้วหรือมีหลายบรรทัดจะไม่ถูกตอบ |
| `autoreply_im.per_sender_cooldown_seconds` | `3600` | ระยะห่างขั้นต่ำต่อเพื่อน (ขั้นต่ำ 60) |
| `autoreply_im.max_per_sender_per_day` | `3` | เพดานต่อเพื่อนใน 24 ชม. (1–50) |
| `autoreply_im.max_per_hour` / `global_min_gap_seconds` | `10` / `20` | เพดานต่อชั่วโมง และระยะห่างระหว่างคำตอบ IM ทุกหน้าต่าง |
| `autoreply_im.delay_range_seconds` | `[2,5]` | หน่วงสุ่มก่อนตอบ |
| `autoreply_im.max_reply_length` / `max_incoming_length` | `200` / `500` | จำกัดความยาวคำตอบ (รวม prefix) / ข้อความขาเข้า |
| `autoreply_im.log_kinds` | `["im"]` | ชนิดหน้าต่างที่ถือว่าเป็นแชทส่วนตัว เพิ่ม `mtim` เฉพาะเมื่อ `im-probe` แสดงว่าแชทส่วนตัวของคุณเป็น `MTIM` |
| `autoreply_im.max_windows` | `5` | จำนวนหน้าต่างแชทส่วนตัวสูงสุดที่ติดตาม |
| `autoreply_im.window_title_regex` | `""` | กรองตามชื่อหน้าต่าง IM (ไม่บังคับ) |

การตรวจ (เป็น error ไม่ใช่ warning เมื่อ `enabled`): `only_nicknames` ว่าง, `autoreply.own_nickname` ว่าง (บอทจะตอบตัวเอง และถูกปฏิเสธแม้ dry-run), `autoreply.log_kind` ไม่ใช่ `room` ขณะเปิดตอบห้อง, regex ผิด, ค่าเกินช่วง เนื้อข้อความขาเข้าไม่ถูกเขียนลง log มีแค่นิคเพื่อนกับคำตอบสำเร็จรูปของคุณ

## ดีเจเพลง (ห้อง)

ต้องเปิด `autoreply.enabled` เพราะใช้ระบบอ่านแชทคำสั่งในแชท (คำนำหน้าตาม `dj.prefix` ค่าเริ่มต้น `!`): `!request <เพลง>` (หรือ `!req`, `!song`), `!queue`, `!current` (หรือ `!np`), `!skip`, `!help` คิวเก็บในไฟล์ `dj.queue_file` ข้างแอป

| คีย์ | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `dj.enabled` | `false` | เปิดใช้ดีเจ |
| `dj.prefix` | `"!"` | คำนำหน้าคำสั่ง ห้ามขึ้นต้นด้วย `/` |
| `dj.queue_file` | `"dj_queue.json"` | ไฟล์เก็บสถานะ `{current, queue[]}` |
| `dj.max_per_user` | `3` | เพลงที่อยู่ในคิวต่อคน (1–50) เทียบนิคไม่สนตัวใหญ่เล็ก |
| `dj.music_dir` | `"music"` | โฟลเดอร์ไฟล์ `.wav` (สำหรับ backend `local` และการจับคู่ `!request`) |
| `dj.audio_backend` | `"chat"` | `chat` = ประกาศในแชทอย่างเดียว; `local` = เล่นไฟล์ `.wav` บนเครื่องนี้ด้วย (ส่งเข้า Camfrog ด้วย virtual cable หรือ stereo mix; MP3 ต้องแปลงเป็น WAV ก่อน) |
| `dj.announce_now` | `"[dj] Now playing: {title} (requested by {user})"` | แม่แบบข้อความ ตัวแปร `{title}` `{user}` |
| `dj.announce_queued` | `"[dj] Queued #{pos}: {title}"` | แม่แบบข้อความ ตัวแปร `{pos}` `{title}` |

กฎ: คำขอแรกเล่นทันที; เพลงซ้ำหรือเพลงที่กำลังเล่นจะถูกปฏิเสธ (ไม่สนตัวใหญ่เล็ก); `!skip` ใช้ได้กับผู้ขอเพลงหรือเจ้าของบอท (นิคคุณใน `autoreply.own_nickname`); ถ้าใช้ backend `local` เพลงถัดไปจะเล่นเองเมื่อเพลงปัจจุบันจบ ถ้าเป็น `chat` ให้สั่งเพลงถัดไปเอง บรรทัดที่ขึ้นต้นด้วยคำนำหน้าดีเจจะไม่ถูกกฎตอบแชทธรรมดากับได้

## รูปแบบ `reply`

- `"text"` — คำตอบเดียว
- `["a","b"]` — สุ่มเลือก
- `{ "th": [...], "en": [...] }` — ภาษาตามข้อความที่เข้ามา (มีตัวอักษรไทย = `th`) ถ้าขาดภาษาใดจะใช้อีกภาษาแทน

ตัวแปรใน status และคำตอบ: `{sender}` นิคผู้ส่ง (เฉพาะคำตอบ), `{me}` นิคคุณ, `{time}` เวลา, `{date}` วันที่ คำตอบที่ขึ้นต้นด้วย `/` ถูกบล็อกเสมอ จึงไม่มีทางส่งคำสั่งห้อง

## หมายเหตุการตรวจ

- selector ที่ไม่มีเงื่อนไข (ไม่มี `control_type`, `auto_id`, `class_name`, `title`, `title_re` เลย) = error selector ซ้ำกันของ `status.edit`, `autoreply.history`, `autoreply.input` = คำเตือนตอนทดลอง และ **error ตอนรันจริง**
- ช่วงค่า: `poll_seconds` 0.2–60, `safety.max_consecutive_failures` 1–100, `status.max_length` 1–500, `status.retry_seconds` 1–3600, `autoreply.max_reply_length` 1–500, `autoreply.max_incoming_length` 1–2000
- ข้อความใหม่เกิน 25 บรรทัดต่อรอบ (สลับห้อง/โหลดประวัติใหม่) ถือเป็นการรีเซ็ต ไม่ตอบ
- อย่าครอบคำไทยด้วย `\b` (ภาษาไทยไม่มี word boundary): ใช้ `เว็บพนัน` ไม่ใช่ `\bเว็บพนัน\b`
