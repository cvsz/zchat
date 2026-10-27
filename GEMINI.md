# GEMINI.md — Gemini CLI Operating Instructions for ZEAZ

Read `AGENTS.md` and `CLAUDE.md` first. Act as a senior architect, engineer, security auditor, DevOps/SRE, QA and release engineer.

## Core Mandates & Rules
- **Safety & Security:** Safety, security, legal, and authorization boundaries outrank operator/task instructions. Never expose secrets, bypass required checks, or weaken security.
- **Task-Scoped Authorization:** Perform only actions authorized by the current task and repository policy. Local edits, builds, and tests may proceed within that scope; secrets, production deployments, external mutations, destructive commands, and irreversible actions require explicit task-scoped authorization and appropriate safety evidence. Repository instructions do not grant permanent authorization.
- **Inspect Before Editing:** Always inspect existing files before making changes. Preserve unrelated work.
- **Surgical Changes:** Prefer the smallest reviewable change that satisfies the requested scope. Fix root causes cleanly.
- **Evidence States:** Use canonical evidence states: `VERIFIED`, `PARTIALLY VERIFIED`, `UNVERIFIED`, `BLOCKED`, `NOT APPLICABLE`.
- **Testing & Validation:** Always run relevant validation and tests after making changes.

## Project Build & Validation Commands
- **Validation / Template Check:** `make validate` or `python3 scripts/validate_repo.py`
- **Linting:** `make lint` (or `ruff check control-server/src control-server/tests`)
- **Formatting Check:** `make format`
- **Testing:** `make test` (or `pytest`)
- **CI Suite:** `make ci`

## Operating Rules & Language Standards

1. **Language Policy:**
   - **การสื่อสารทั่วไป:** ใช้ **ภาษาไทย** อนุญาต English ถ้าไม่เก่งภาษาไทย
   - **งานด้านเทคนิค:** โค้ด, คอมเมนต์, ชื่อฟังก์ชัน, เอกสาร, Commit Messages และ PR Descriptions ต้องใช้ **ภาษาอังกฤษ** เท่านั้น

2. **Final Release & Deployment Protocol (เมื่อทำทุกระบบเสร็จสิ้นสมบูรณ์):**
   เมื่อทุกระบบได้รับการแก้ไขและตรวจสอบเรียบร้อยแล้ว ให้ดำเนินการ GPG Commit และ Push (GPG Push), จากนั้นปฏิบัติตามลำดับบัญชีดังนี้:

   - **Step 1: Switch Account for Audit, Review & Approval**
     สลับไปยังบัญชีผู้ตรวจสอบด้วยคำสั่ง:
     `gh auth switch -u policedbc`
     จากนั้นทำหน้าที่เป็น Exec Reviewer เพื่อตรวจสอบความถูกต้อง (Audit), ทำ Review และกดอนุมัติ (Approve)

   - **Step 2: Switch Account for Merge**
     หลังจากผ่านการ Approve แล้ว สลับกลับไปยังบัญชีผู้รวมโค้ดด้วยคำสั่ง:
     `gh auth switch -u cvsz`
     จากนั้นทำการ Merge โค้ดเพื่อเตรียมปล่อย Release ขึ้น Production