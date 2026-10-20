# ZChat Comprehensive Implementation Plan - V7.0

## Phase 1: Auto-Reply Systems Fix

### 1.1 chat-im-private Auto-Reply Fix
- **File**: `src/features/chat_im_private_gui.py`
- **Issue**: Missing real auto-reply implementation
- **Fix**: Implement proper IM auto-reply that works with Camfrog's private chat windows
- **Key changes**:
  - Add rule-based pattern matching for incoming IM messages
  - Implement reply prefix "[auto]" with Thai/English support
  - Add nickname allowlist/blocklist
  - Add delay_range_seconds for reply timing
  - Implement write_text with proper HWND handling

### 1.2 im-autoreply Real Auto-Reply 100% Work
- **File**: `src/features/im_autoreply_gui.py`
- **Issue**: Auto-reply needs full implementation with proper delay and pattern matching
- **Fix**: Complete auto-reply engine with:
  - Rule-based pattern matching (regex supported)
  - Per-sender cooldown tracking
  - Global minimum gap enforcement
  - Own nickname validation
  - Link skipping
  - Delay range [min, max] seconds
  - Thai/English bilingual support

## Phase 2: DJ System Full Upgrade

### 2.1 music-dj Real Auto 100% Work
- **File**: `src/features/camfrog_music_gui.py`
- **Issue**: DJ system needs complete functional upgrade
- **Fix**:
  - Implement persistent queue with dj_queue.json
  - Add !request, !queue, !current, !skip, !help commands
  - Implement audio backend (chat + local .wav support)
  - Add max_per_user enforcement (1-50)
  - Implement proper chat plumbing (needs autoreply.enabled)
  - Add queue file validation inside Music DJ data folder
  - Support audio_backend: "chat", "local", "youtube"

## Phase 3: Hotkey System (F3/F4)

### 3.1 Add F3 Start and F4 Stop Functions
- **Files**: All feature GUI files
- **Implementation**:
  - Register global hotkey F3 to start the feature worker
  - Register global hotkey F4 to stop the feature worker
  - Ensure hotkeys work when application is in background
  - No focus stealing or mouse/keyboard injection
  - Proper shutdown cleanup

## Phase 4: Name-Changer System

### 4.1 Name-Changer Manual+Auto (2 Languages + Delay)
- **File**: Add to shared camfrog_auto.py engine
- **Features**:
  - Manual name change trigger
  - Automatic name change loop in Thai and English
  - Configurable delay time between name changes
  - Bilingual name queue (thai, english, thai, english, ...)
  - Enable/disable toggle
  - Delay validation (min 1 second, reasonable max)

## Phase 5: User Activity Detection

### 5.1 Wait if User Uses Mouse+Keyboard, Then Continue
- **File**: `src/engine/camfrog_auto.py` (shared engine)
- **Implementation**:
  - Detect foreground window activity (mouse movement, keyboard input)
  - When user activity detected: pause workers gracefully
  - Track last activity timestamp
  - Resume workers after no activity configured delay (e.g., 30 seconds)
  - Use Windows API to detect input without injecting
  - Fail-closed: if cannot detect, assume active and pause

## Phase 6: Color-Cycling Profile

### 6.1 Automatic Color Switching Loop
- **File**: Add to shared engine or each feature GUI
- **Implementation**:
  - Loop through a predefined color palette
  - Change Camfrog profile color at configured intervals
  - Support Thai and English color names
  - Configurable cycle speed
  - Enable/disable toggle
  - Random or ordered sequence

## Phase 7: Worker Delay Reduction

### 7.1 Reduce Delay, Continue After User Activity
- **File**: `src/engine/camfrog_auto.py`
- **Implementation**:
  - Reduce default poll_seconds from 1.5 to 0.8
  - Implement activity-aware polling
  - When resuming after user activity: start with full speed immediately
  - No unnecessary waiting when user is not active
  - Background mode: never steal focus, no mouse/keyboard hooks

## Phase 8: Final Validation & Build

### 8.1 Run Full CI, Build All EXEs, Verify SHA256
- Run `make ci` 
- Build all standalone executables
- Verify SHA256 checksums
- Test all features end-to-end