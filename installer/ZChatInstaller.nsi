; ============================================================================
; ZChat / AutoDJ Automated Installer for Windows 11
; Single source of truth for Windows deployment
; Built with NSIS 3.0+
; ============================================================================

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinCore.nsh"
!include "x64.nsh"

; ============================================================================
; CONFIGURATION - SINGLE SOURCE OF TRUTH
; ============================================================================

; Application metadata
!define APP_NAME "ZChat"
!define APP_VERSION "2.20.0"
!define APP_PUBLISHER "ZEAZDEV / ZCoder"
!define APP_WEBSITE "https://github.com/cvsz/zchat"
!define APP_SUPPORT "https://github.com/cvsz/zchat/issues"
!define APP_COPYRIGHT "Copyright (C) 2024-2026 ZEAZDEV"

; Installation paths
!define INSTALL_DIR_DEFAULT "$PROGRAMFILES64\ZChat"
!define INSTALL_DIR_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ZChat"
!define INSTALL_DIR_REG_VALUE "InstallLocation"

; Components (match build outputs)
!define COMPONENT_CORE "Core"
!define COMPONENT_AUTODJ "AutoDJ"
!define COMPONENT_ROOM_CONTROL "RoomControl"
!define COMPONENT_CHAT_IM "ChatIM"
!define COMPONENT_STATUS_RANDOM "StatusRandom"
!define COMPONENT_STATUS_MARQUEE "StatusMarquee"
!define COMPONENT_STATUS_CHANGER "StatusChanger"
!define COMPONENT_IM_AUTOREPLY "IMAutoreply"
!define COMPONENT_WEB_STATUS "WebStatus"
!define COMPONENT_CLOUDBOT "CloudBotManager"
!define COMPONENT_NAME_COLOR "NameColorChanger"
!define COMPONENT_ICON_CHANGER "IconChanger"
!define COMPONENT_NAME_CHANGER "NameChanger"
!define COMPONENT_LINE_STATUS "LineStatusChanger"
!define COMPONENT_ZCHAT_CLIENT "ZChatClient"

; Required external dependencies
!define DEP_MPV_URL "https://mpv.io/installation/"
!define DEP_YTDLP_URL "https://github.com/yt-dlp/yt-dlp/releases/latest"

; Code signing (set via environment or .env)
!define SIGN_TOOL "signtool"
!define SIGN_CERT_THUMBPRINT ""  ; Set via env: ZCODER_CERT_THUMBPRINT
!define SIGN_TIMESTAMP_URL "http://timestamp.digicert.com"

; ============================================================================
; INSTALLER SETTINGS
; ============================================================================

Name "${APP_NAME} ${APP_VERSION}"
OutFile "ZChat-${APP_VERSION}-Setup.exe"
InstallDir "${INSTALL_DIR_DEFAULT}"
InstallDirRegKey HKLM "${INSTALL_DIR_REG_KEY}" "${INSTALL_DIR_REG_VALUE}"
RequestExecutionLevel admin
ShowInstDetails show

; Modern UI
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${NSISDIR}\Contrib\Graphics\Wizard\modern-wizard.bmp"
!define MUI_FINISHPAGE_RUN "$INSTDIR\AutoDJ.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch AutoDJ"
!define MUI_FINISHPAGE_SHOWREADME ""

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Korean"  ; For Thai/English bilingual support

; ============================================================================
; RESOURCE FILES
; ============================================================================

; License
!define MUI_LICENSEPAGE_TEXT_TOP "Please read the license agreement before installing ${APP_NAME}."
!define MUI_LICENSEPAGE_TEXT_BOTTOM "If you accept the terms, click I Agree to continue."

; ============================================================================
; VARIABLES
; ============================================================================

Var /GLOBAL PreviousInstallDir
Var /GLOBAL HasPreviousInstall
Var /GLOBAL InstallMPV
Var /GLOBAL InstallYTDLP
Var /GLOBAL StartMenuFolder
Var /GLOBAL DesktopShortcut
Var /GLOBAL AutoLaunch

; ============================================================================
; FUNCTIONS
; ============================================================================

Function .onInit
    ; Check for previous installation
    StrCpy $HasPreviousInstall 0
    ReadRegStr $PreviousInstallDir HKLM "${INSTALL_DIR_REG_KEY}" "${INSTALL_DIR_REG_VALUE}"
    StrCmp $PreviousInstallDir "" NoPreviousInstall
    StrCpy $HasPreviousInstall 1
    NoPreviousInstall:

    ; Detect Windows 11
    ${GetWindowsVersion} $0 $1
    ${If} $0 = 10
        ${If} $1 >= 22000
            ; Windows 11 detected
        ${EndIf}
    ${EndIf}

    ; Initialize variables
    StrCpy $InstallMPV 1
    StrCpy $InstallYTDLP 1
    StrCpy $AutoLaunch 0
FunctionEnd

Function .onVerifyInstDir
    ; Ensure directory ends with ZChat
    StrCmp $INSTDIR "$PROGRAMFILES64\ZChat" 0 +2
    StrCpy $INSTDIR "$PROGRAMFILES64\ZChat"
    StrCmp $INSTDIR "$PROGRAMFILES\ZChat" 0 +2
    StrCpy $INSTDIR "$PROGRAMFILES64\ZChat"
FunctionEnd

Function SelectComponents
    ; Core is always required
    !insertmacro SelectSection ${SEC_CORE}
    !insertmacro SectionSetFlags ${SEC_CORE} ${SF_RO}
FunctionEnd

Function WriteUninstallRegistry
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "URLInfoAbout" "${APP_WEBSITE}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "URLUpdateInfo" "${APP_WEBSITE}/releases"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "HelpLink" "${APP_SUPPORT}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "QuietUninstallString" "$INSTDIR\uninstall.exe /S"
    WriteRegDWORD HKLM "${INSTALL_DIR_REG_KEY}" "NoModify" 1
    WriteRegDWORD HKLM "${INSTALL_DIR_REG_KEY}" "NoRepair" 1
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "DisplayIcon" "$INSTDIR\app.ico"
FunctionEnd

Function CreateShortcuts
    ; Start Menu
    CreateDirectory "$SMPROGRAMS\ZChat"
    CreateShortCut "$SMPROGRAMS\ZChat\AutoDJ.lnk" "$INSTDIR\auto-dj\AutoDJ.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Room Control.lnk" "$INSTDIR\room-control\room-control.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Chat IM Private.lnk" "$INSTDIR\chat-im-private\chat-im-private.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Status Random.lnk" "$INSTDIR\status-random\status-random.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Status Marquee.lnk" "$INSTDIR\status-marquee\status-marquee.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Status Changer.lnk" "$INSTDIR\status-changer\status-changer.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\IM Autoreply.lnk" "$INSTDIR\im-autoreply\im-autoreply.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Web Status.lnk" "$INSTDIR\web-status\web-status.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\CloudBot Manager.lnk" "$INSTDIR\cloudbot-manager\CloudBot-Manager.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Name Color Changer.lnk" "$INSTDIR\name-color-changer\name-color-changer.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Icon Changer.lnk" "$INSTDIR\icon-changer\icon-changer.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Name Changer.lnk" "$INSTDIR\name-changer\name-changer.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Line Status Changer.lnk" "$INSTDIR\line\dist\line-status-changer.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\ZChat Client.lnk" "$INSTDIR\zchat-client\dist\ZChat.exe" "" "$INSTDIR\app.ico" 0
    CreateShortCut "$SMPROGRAMS\ZChat\Uninstall.lnk" "$INSTDIR\uninstall.exe" "" "$INSTDIR\app.ico" 0

    ; Desktop shortcut (if selected)
    ${If} $DesktopShortcut = 1
        CreateShortCut "$DESKTOP\AutoDJ.lnk" "$INSTDIR\auto-dj\AutoDJ.exe" "" "$INSTDIR\app.ico" 0
    ${EndIf}
FunctionEnd

Function CreateAutoLaunch
    ${If} $AutoLaunch = 1
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AutoDJ" '"$INSTDIR\auto-dj\AutoDJ.exe" --gui'
    ${EndIf}
FunctionEnd

Function RemoveShortcuts
    Delete "$SMPROGRAMS\ZChat\AutoDJ.lnk"
    Delete "$SMPROGRAMS\ZChat\Room Control.lnk"
    Delete "$SMPROGRAMS\ZChat\Chat IM Private.lnk"
    Delete "$SMPROGRAMS\ZChat\Status Random.lnk"
    Delete "$SMPROGRAMS\ZChat\Status Marquee.lnk"
    Delete "$SMPROGRAMS\ZChat\Status Changer.lnk"
    Delete "$SMPROGRAMS\ZChat\IM Autoreply.lnk"
    Delete "$SMPROGRAMS\ZChat\Web Status.lnk"
    Delete "$SMPROGRAMS\ZChat\CloudBot Manager.lnk"
    Delete "$SMPROGRAMS\ZChat\Name Color Changer.lnk"
    Delete "$SMPROGRAMS\ZChat\Icon Changer.lnk"
    Delete "$SMPROGRAMS\ZChat\Name Changer.lnk"
    Delete "$SMPROGRAMS\ZChat\Line Status Changer.lnk"
    Delete "$SMPROGRAMS\ZChat\ZChat Client.lnk"
    Delete "$SMPROGRAMS\ZChat\Uninstall.lnk"
    Delete "$DESKTOP\AutoDJ.lnk"
    RMDir /r "$SMPROGRAMS\ZChat"
FunctionEnd

Function RemoveAutoLaunch
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AutoDJ"
FunctionEnd

Function CheckDependencies
    ; Check for mpv
    ${If} $InstallMPV = 1
        ExecWait '"$INSTDIR\tools\check_mpv.bat"'
        ${If} $0 != 0
            MessageBox MB_ICONWARNING|MB_YESNO "mpv not found. AutoDJ requires mpv for audio playback.$\n$\nDownload mpv from ${DEP_MPV_URL} and ensure it's in PATH, or install via installer." IDYES NoMPV
            Abort
        NoMPV:
    ${EndIf}

    ; Check for yt-dlp
    ${If} $InstallYTDLP = 1
        ExecWait '"$INSTDIR\tools\check_ytdlp.bat"'
        ${If} $0 != 0
            MessageBox MB_ICONWARNING|MB_YESNO "yt-dlp not found. AutoDJ requires yt-dlp for YouTube playback.$\n$\nDownload from ${DEP_YTDLP_URL} and place next to mpv.exe, or install via installer." IDYES NoYTDLP
            Abort
        NoYTDLP:
    ${EndIf}
FunctionEnd

; ============================================================================
; SECTIONS
; ============================================================================

; ----------------------------------------------------------------------------
; CORE (Required)
; ----------------------------------------------------------------------------
Section "${COMPONENT_CORE} (Required)" SEC_CORE
    SectionIn RO
    SetOutPath "$INSTDIR"

    ; Core files
    File "app.ico"
    File "config.example.toml"
    File "LICENSE.txt"
    File "README.md"

    ; Config directory
    CreateDirectory "$INSTDIR\config"
    CreateDirectory "$INSTDIR\logs"
    CreateDirectory "$INSTDIR\data"

    ; Tools
    CreateDirectory "$INSTDIR\tools"
    File /r "tools\*.bat"
    File /r "tools\*.ps1"
    File /r "tools\*.py"

    ; Assets
    CreateDirectory "$INSTDIR\assets"
    File /r "assets\*.png"

    ; Write uninstall registry
    Call WriteUninstallRegistry

    ; Write version info
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "InstallDate" "$(GetDate)"

SectionEnd

; ----------------------------------------------------------------------------
; AutoDJ
; ----------------------------------------------------------------------------
Section "${COMPONENT_AUTODJ}" SEC_AUTODJ
    SetOutPath "$INSTDIR\auto-dj"
    File /r "dist\auto-dj\*.exe"
    File /r "dist\auto-dj\*.dll"
    File /r "dist\auto-dj\*.pyd"
    File /r "dist\auto-dj\*.json"
    File /r "dist\auto-dj\*.toml"
SectionEnd

; ----------------------------------------------------------------------------
; Room Control
; ----------------------------------------------------------------------------
Section "${COMPONENT_ROOM_CONTROL}" SEC_ROOM_CONTROL
    SetOutPath "$INSTDIR\room-control"
    File /r "dist\room-control\*.exe"
    File /r "dist\room-control\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Chat IM Private
; ----------------------------------------------------------------------------
Section "${COMPONENT_CHAT_IM}" SEC_CHAT_IM
    SetOutPath "$INSTDIR\chat-im-private"
    File /r "dist\chat-im-private\*.exe"
    File /r "dist\chat-im-private\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Status Random
; ----------------------------------------------------------------------------
Section "${COMPONENT_STATUS_RANDOM}" SEC_STATUS_RANDOM
    SetOutPath "$INSTDIR\status-random"
    File /r "dist\status-random\*.exe"
    File /r "dist\status-random\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Status Marquee
; ----------------------------------------------------------------------------
Section "${COMPONENT_STATUS_MARQUEE}" SEC_STATUS_MARQUEE
    SetOutPath "$INSTDIR\status-marquee"
    File /r "dist\status-marquee\*.exe"
    File /r "dist\status-marquee\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Status Changer
; ----------------------------------------------------------------------------
Section "${COMPONENT_STATUS_CHANGER}" SEC_STATUS_CHANGER
    SetOutPath "$INSTDIR\status-changer"
    File /r "dist\status-changer\*.exe"
    File /r "dist\status-changer\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; IM Autoreply
; ----------------------------------------------------------------------------
Section "${COMPONENT_IM_AUTOREPLY}" SEC_IM_AUTOREPLY
    SetOutPath "$INSTDIR\im-autoreply"
    File /r "dist\im-autoreply\*.exe"
    File /r "dist\im-autoreply\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Web Status
; ----------------------------------------------------------------------------
Section "${COMPONENT_WEB_STATUS}" SEC_WEB_STATUS
    SetOutPath "$INSTDIR\web-status"
    File /r "dist\web-status\*.exe"
    File /r "dist\web-status\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; CloudBot Manager
; ----------------------------------------------------------------------------
Section "${COMPONENT_CLOUDBOT}" SEC_CLOUDBOT
    SetOutPath "$INSTDIR\cloudbot-manager"
    File /r "dist\cloudbot-manager\*.exe"
    File /r "dist\cloudbot-manager\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Name Color Changer
; ----------------------------------------------------------------------------
Section "${COMPONENT_NAME_COLOR}" SEC_NAME_COLOR
    SetOutPath "$INSTDIR\name-color-changer"
    File /r "dist\name-color-changer\*.exe"
    File /r "dist\name-color-changer\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Icon Changer
; ----------------------------------------------------------------------------
Section "${COMPONENT_ICON_CHANGER}" SEC_ICON_CHANGER
    SetOutPath "$INSTDIR\icon-changer"
    File /r "dist\icon-changer\*.exe"
    File /r "dist\icon-changer\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Name Changer
; ----------------------------------------------------------------------------
Section "${COMPONENT_NAME_CHANGER}" SEC_NAME_CHANGER
    SetOutPath "$INSTDIR\name-changer"
    File /r "dist\name-changer\*.exe"
    File /r "dist\name-changer\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Line Status Changer
; ----------------------------------------------------------------------------
Section "${COMPONENT_LINE_STATUS}" SEC_LINE_STATUS
    SetOutPath "$INSTDIR\line"
    File /r "dist\line\*.exe"
    File /r "dist\line\*.dll"
    File /r "line\dist\*.exe"
    File /r "line\dist\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; ZChat Client
; ----------------------------------------------------------------------------
Section "${COMPONENT_ZCHAT_CLIENT}" SEC_ZCHAT_CLIENT
    SetOutPath "$INSTDIR\zchat-client"
    File /r "dist\zchat-client\*.exe"
    File /r "dist\zchat-client\*.dll"
SectionEnd

; ----------------------------------------------------------------------------
; Start Menu Shortcuts
; ----------------------------------------------------------------------------
Section "Start Menu Shortcuts" SEC_SHORTCUTS
    Call CreateShortcuts
    WriteRegStr HKLM "${INSTALL_DIR_REG_KEY}" "StartMenuFolder" "$StartMenuFolder"
SectionEnd

; ----------------------------------------------------------------------------
; Desktop Shortcut
; ----------------------------------------------------------------------------
Section "Desktop Shortcut" SEC_DESKTOP
    StrCpy $DesktopShortcut 1
SectionEnd

; ----------------------------------------------------------------------------
; Auto-launch on Windows Startup
; ----------------------------------------------------------------------------
Section "Auto-launch on Startup" SEC_AUTOLAUNCH
    StrCpy $AutoLaunch 1
SectionEnd

; ----------------------------------------------------------------------------
; mpv (External Dependency)
; ----------------------------------------------------------------------------
Section "mpv Media Player (Required for AutoDJ)" SEC_MPV
    StrCpy $InstallMPV 1
SectionEnd

; ----------------------------------------------------------------------------
; yt-dlp (External Dependency)
; ----------------------------------------------------------------------------
Section "yt-dlp (Required for YouTube Playback)" SEC_YTDLP
    StrCpy $InstallYTDLP 1
SectionEnd

; ----------------------------------------------------------------------------
; Post-Install
; ----------------------------------------------------------------------------
Section -PostInstall
    Call CheckDependencies
    Call CreateAutoLaunch

    ; Create default config.toml if not exists
    IfFileExists "$INSTDIR\config.toml" 0 +2
    CopyFiles "$INSTDIR\config.example.toml" "$INSTDIR\config.toml"

    ; Set permissions for data directories
    ExecWait 'icacls "$INSTDIR\data" /grant "Users:(OI)(CI)F" /T'
    ExecWait 'icacls "$INSTDIR\logs" /grant "Users:(OI)(CI)F" /T'
    ExecWait 'icacls "$INSTDIR\config" /grant "Users:(OI)(CI)F" /T'

    ; Register file associations (optional)
    ; WriteRegStr HKCR ".autodj" "" "AutoDJ.Playlist"
    ; WriteRegStr HKCR "AutoDJ.Playlist" "" "AutoDJ Playlist"
    ; WriteRegStr HKCR "AutoDJ.Playlist\shell\open\command" "" '"$INSTDIR\auto-dj\AutoDJ.exe" --load "%1"'
SectionEnd

; ============================================================================
; UNINSTALLER
; ============================================================================

Section Uninstall
    ; Stop any running instances
    ExecWait 'taskkill /f /im "AutoDJ.exe" /im "RoomControl.exe" /im "ChatIMPrivate.exe" /im "StatusRandom.exe" /im "StatusMarquee.exe" /im "StatusChanger.exe" /im "IMAutoreply.exe" /im "WebStatus.exe" /im "CloudBot-Manager.exe" /im "NameColorChanger.exe" /im "IconChanger.exe" /im "NameChanger.exe" /im "line-status-changer.exe" /im "ZChat.exe" 2>NUL'

    ; Remove auto-launch
    Call RemoveAutoLaunch

    ; Remove shortcuts
    Call RemoveShortcuts

    ; Remove files
    RMDir /r "$INSTDIR\auto-dj"
    RMDir /r "$INSTDIR\room-control"
    RMDir /r "$INSTDIR\chat-im-private"
    RMDir /r "$INSTDIR\status-random"
    RMDir /r "$INSTDIR\status-marquee"
    RMDir /r "$INSTDIR\status-changer"
    RMDir /r "$INSTDIR\im-autoreply"
    RMDir /r "$INSTDIR\web-status"
    RMDir /r "$INSTDIR\cloudbot-manager"
    RMDir /r "$INSTDIR\name-color-changer"
    RMDir /r "$INSTDIR\icon-changer"
    RMDir /r "$INSTDIR\name-changer"
    RMDir /r "$INSTDIR\line"
    RMDir /r "$INSTDIR\zchat-client"
    RMDir /r "$INSTDIR\tools"
    RMDir /r "$INSTDIR\assets"
    RMDir /r "$INSTDIR\config"
    RMDir /r "$INSTDIR\logs"
    RMDir /r "$INSTDIR\data"
    Delete "$INSTDIR\*.*"
    RMDir "$INSTDIR"

    ; Remove registry
    DeleteRegKey HKLM "${INSTALL_DIR_REG_KEY}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AutoDJ"

    ; Remove shortcuts
    Call RemoveShortcuts
SectionEnd

; ============================================================================
; SILENT INSTALL SUPPORT
; ============================================================================

Function .onUserAbort
    Abort
FunctionEnd

; Silent install: /S /D=C:\Path\To\ZChat
; Silent uninstall: /S
; Component selection: /COMPONENTS="AutoDJ,RoomControl,CloudBotManager"
!define MUI_SILENTINSTALL_REGISTRY_KEY "${INSTALL_DIR_REG_KEY}"
!define MUI_SILENTINSTALL_REGISTRY_VALUENAME "DisplayVersion"

; ============================================================================
; CODE SIGNING (Post-build)
; ============================================================================

; After installer creation, sign with:
; signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a "ZChat-${APP_VERSION}-Setup.exe"

; ============================================================================
; END
; ============================================================================