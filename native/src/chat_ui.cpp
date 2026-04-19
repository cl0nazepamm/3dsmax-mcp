#include "mcp_bridge/chat_ui.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/llm_client.h"
#include <commctrl.h>
#include <richedit.h>
#include <shellapi.h>
#include <shlobj.h>
#include <string>

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "shell32.lib")

using namespace HandlerHelpers;

// ── State ───────────────────────────────────────────────────────
static HWND g_chatWnd      = nullptr;
static HWND g_historyEdit  = nullptr;   // RichEdit 4.1
static HWND g_inputEdit    = nullptr;   // multi-line EDIT
static HWND g_sendBtn      = nullptr;
static HWND g_analyzeBtn   = nullptr;
static HWND g_captureBtn   = nullptr;
static HWND g_configBtn    = nullptr;
static HWND g_statusBar    = nullptr;

static HFONT  g_font       = nullptr;
static HFONT  g_monoFont   = nullptr;
static HBRUSH g_bgBrush    = nullptr;
static HBRUSH g_editBrush  = nullptr;
static HMODULE g_richedMod = nullptr;

static HINSTANCE     g_hInst = nullptr;
static MCPBridgeGUP* g_gup   = nullptr;

static MCPChatUI::MessageCallback g_onMessage;
static MCPChatUI::ActionCallback  g_onAction;

static WNDPROC g_origInputProc = nullptr;

// Control IDs
static const int ID_SEND     = 1001;
static const int ID_ANALYZE  = 1002;
static const int ID_CAPTURE  = 1003;
static const int ID_CONFIG   = 1004;
static const int ID_INPUT    = 1005;
static const int ID_HISTORY  = 1006;
static const int ID_STATUS   = 1007;

// Layout constants
static const int MARGIN       = 8;
static const int INPUT_HEIGHT = 84;  // ~5 lines of text
static const int BTNBAR_H     = 30;
static const int SEND_W       = 80;
static const int STATUS_H     = 22;

// ── Colors (dark theme) ─────────────────────────────────────────
static const COLORREF COL_BG        = RGB(28, 28, 30);
static const COLORREF COL_EDIT_BG   = RGB(38, 38, 42);
static const COLORREF COL_FG        = RGB(220, 220, 220);
static const COLORREF COL_USER      = RGB(120, 200, 255);
static const COLORREF COL_AI        = RGB(230, 230, 230);
static const COLORREF COL_TOOL      = RGB(160, 160, 160);
static const COLORREF COL_ERR       = RGB(255, 110, 110);
static const COLORREF COL_DIM       = RGB(150, 150, 155);

// ── Append colored text to RichEdit ─────────────────────────────

static void AppendColoredText(HWND re, const std::wstring& text, COLORREF color, bool bold = false) {
    if (!re) return;

    // Move caret to end
    int len = GetWindowTextLengthW(re);
    CHARRANGE cr = { len, len };
    SendMessageW(re, EM_EXSETSEL, 0, (LPARAM)&cr);

    CHARFORMAT2W cf = {};
    cf.cbSize = sizeof(cf);
    cf.dwMask = CFM_COLOR | CFM_BOLD | CFM_FACE | CFM_SIZE;
    cf.crTextColor = color;
    cf.dwEffects = bold ? CFE_BOLD : 0;
    cf.yHeight = 200;  // ~10pt
    wcscpy_s(cf.szFaceName, L"Segoe UI");
    SendMessageW(re, EM_SETCHARFORMAT, SCF_SELECTION, (LPARAM)&cf);

    SendMessageW(re, EM_REPLACESEL, FALSE, (LPARAM)text.c_str());

    // Scroll to bottom
    SendMessageW(re, EM_SCROLLCARET, 0, 0);
    SendMessageW(re, WM_VSCROLL, SB_BOTTOM, 0);
}

static COLORREF RoleColor(const std::string& role) {
    if (role == "user")   return COL_USER;
    if (role == "tool")   return COL_TOOL;
    if (role == "error")  return COL_ERR;
    if (role == "system") return COL_DIM;
    return COL_AI;
}

static const wchar_t* RolePrefix(const std::string& role) {
    if (role == "user")   return L"You  ";
    if (role == "tool")   return L"Tool ";
    if (role == "error")  return L"Err  ";
    if (role == "system") return L"Sys  ";
    return L"AI   ";
}

void MCPChatUI::AppendMessage(const std::string& role, const std::string& text) {
    if (!g_historyEdit) return;

    COLORREF color = RoleColor(role);
    std::wstring prefix = RolePrefix(role);
    std::wstring body = Utf8ToWide(text);

    // Prefix bold + colored, body in same color (tool results dimmer)
    AppendColoredText(g_historyEdit, prefix, color, true);
    AppendColoredText(g_historyEdit, L"  ", color, false);
    AppendColoredText(g_historyEdit, body + L"\r\n\r\n", color, false);
}

// ── Status bar ──────────────────────────────────────────────────

static std::string g_baseStatus;  // "model: foo" — rebuilt on /reload

static void RebuildBaseStatus() {
    if (LLMClient::IsConfigured()) {
        g_baseStatus = "Model: " + LLMClient::GetConfig().model;
    } else {
        g_baseStatus = "No API key — edit mcp_config.ini [llm]";
    }
}

void MCPChatUI::SetStatus(const std::string& status) {
    if (!g_statusBar) {
        // Legacy: update title bar if status bar not yet alive
        if (g_chatWnd) {
            std::string title = "MCP Chat";
            if (!status.empty()) title += " — " + status;
            SetWindowTextW(g_chatWnd, Utf8ToWide(title).c_str());
        }
        return;
    }
    if (g_baseStatus.empty()) RebuildBaseStatus();
    std::string line = g_baseStatus;
    if (!status.empty()) line += "   •   " + status;
    SendMessageW(g_statusBar, SB_SETTEXTW, 0, (LPARAM)Utf8ToWide(line).c_str());
}

// ── Send / actions ──────────────────────────────────────────────

static void DoSend() {
    int len = GetWindowTextLengthW(g_inputEdit);
    if (len <= 0) return;
    std::wstring buf(len + 1, L'\0');
    GetWindowTextW(g_inputEdit, buf.data(), len + 1);
    buf.resize(len);

    // Trim trailing whitespace
    while (!buf.empty() && (buf.back() == L' ' || buf.back() == L'\r' ||
                            buf.back() == L'\n' || buf.back() == L'\t')) {
        buf.pop_back();
    }
    if (buf.empty()) return;

    std::string text = WideToUtf8(buf.c_str());

    MCPChatUI::AppendMessage("user", text);
    SetWindowTextW(g_inputEdit, L"");
    SetFocus(g_inputEdit);

    if (g_onMessage) g_onMessage(text);
}

static void DoAction(const std::string& action) {
    std::string detail;
    if (action == "analyze_selection") {
        Interface* ip = GetCOREInterface();
        int count = ip->GetSelNodeCount();
        for (int i = 0; i < count && i < 10; i++) {
            if (i > 0) detail += ",";
            detail += WideToUtf8(ip->GetSelNode(i)->GetName());
        }
        MCPChatUI::AppendMessage("user",
            "[Analyze: " + (detail.empty() ? "nothing selected" : detail) + "]");
    } else if (action == "capture") {
        MCPChatUI::AppendMessage("user", "[Capture viewport]");
    }

    if (g_onAction) g_onAction(action, detail);
}

static void OpenConfigFolder() {
    char localAppData[MAX_PATH];
    if (FAILED(SHGetFolderPathA(nullptr, CSIDL_LOCAL_APPDATA, nullptr, 0, localAppData)))
        return;
    std::string dir = std::string(localAppData) + "\\3dsmax-mcp";
    // Open the folder so the user can edit .env or mcp_config.ini
    ShellExecuteA(nullptr, "open", dir.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

// ── Input box keyboard handling ─────────────────────────────────
// Enter         → Send
// Shift+Enter   → newline (default EDIT behavior)
// Ctrl+Enter    → newline (alt)
// Ctrl+L        → /clear
// Ctrl+R        → /reload

static LRESULT CALLBACK InputEditProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == WM_KEYDOWN) {
        bool shift = (GetKeyState(VK_SHIFT) & 0x8000) != 0;
        bool ctrl  = (GetKeyState(VK_CONTROL) & 0x8000) != 0;

        if (wParam == VK_RETURN) {
            if (shift || ctrl) {
                // fall through to default (newline)
            } else {
                DoSend();
                return 0;
            }
        }
        if (ctrl && wParam == 'L') {
            if (g_onMessage) g_onMessage("/clear");
            return 0;
        }
        if (ctrl && wParam == 'R') {
            if (g_onMessage) g_onMessage("/reload");
            return 0;
        }
    }
    // Block the ding on Enter (the edit control would otherwise MessageBeep)
    if (msg == WM_CHAR && wParam == VK_RETURN) {
        bool shift = (GetKeyState(VK_SHIFT) & 0x8000) != 0;
        bool ctrl  = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
        if (!shift && !ctrl) return 0;
    }
    return CallWindowProcW(g_origInputProc, hWnd, msg, wParam, lParam);
}

// ── Layout ──────────────────────────────────────────────────────

static void LayoutChildren(int w, int h) {
    if (h <= 0) return;
    int statusY = h - STATUS_H;
    int inputY  = statusY - INPUT_HEIGHT - MARGIN;
    int btnY    = inputY - BTNBAR_H - MARGIN;
    int histH   = btnY - MARGIN - MARGIN;

    if (g_historyEdit) MoveWindow(g_historyEdit, MARGIN, MARGIN, w - 2*MARGIN, histH, TRUE);

    int btnW = (w - 2*MARGIN - 2*MARGIN) / 3;  // 3 buttons, 2 gaps
    if (g_analyzeBtn) MoveWindow(g_analyzeBtn, MARGIN, btnY, btnW, BTNBAR_H - 4, TRUE);
    if (g_captureBtn) MoveWindow(g_captureBtn, MARGIN + btnW + MARGIN, btnY, btnW, BTNBAR_H - 4, TRUE);
    if (g_configBtn)  MoveWindow(g_configBtn,  MARGIN + 2*(btnW + MARGIN), btnY, btnW, BTNBAR_H - 4, TRUE);

    if (g_inputEdit) MoveWindow(g_inputEdit, MARGIN, inputY, w - 2*MARGIN - SEND_W - MARGIN, INPUT_HEIGHT, TRUE);
    if (g_sendBtn)   MoveWindow(g_sendBtn,   w - MARGIN - SEND_W, inputY, SEND_W, INPUT_HEIGHT, TRUE);

    if (g_statusBar) SendMessageW(g_statusBar, WM_SIZE, 0, 0);
}

// ── Window proc ─────────────────────────────────────────────────

static LRESULT CALLBACK ChatWndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_CREATE: {
        g_bgBrush   = CreateSolidBrush(COL_BG);
        g_editBrush = CreateSolidBrush(COL_EDIT_BG);
        g_font = CreateFontW(-13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                             DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        g_monoFont = CreateFontW(-13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                                 DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                 CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Consolas");

        // History: RichEdit 4.1 (msftedit.dll)
        g_historyEdit = CreateWindowExW(0, MSFTEDIT_CLASS, L"",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL |
            ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_NOHIDESEL,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_HISTORY, g_hInst, nullptr);
        if (g_historyEdit) {
            SendMessageW(g_historyEdit, EM_SETBKGNDCOLOR, 0, (LPARAM)COL_EDIT_BG);
            SendMessageW(g_historyEdit, EM_SETTYPOGRAPHYOPTIONS, TO_ADVANCEDTYPOGRAPHY, TO_ADVANCEDTYPOGRAPHY);
            // Default char format
            CHARFORMAT2W cf = {};
            cf.cbSize = sizeof(cf);
            cf.dwMask = CFM_COLOR | CFM_FACE | CFM_SIZE;
            cf.crTextColor = COL_FG;
            cf.yHeight = 200;
            wcscpy_s(cf.szFaceName, L"Segoe UI");
            SendMessageW(g_historyEdit, EM_SETCHARFORMAT, SCF_DEFAULT, (LPARAM)&cf);
            // Enable Ctrl+C / Ctrl+V
            SendMessageW(g_historyEdit, EM_SETEVENTMASK, 0, ENM_KEYEVENTS);
        }

        // Input: multi-line EDIT
        g_inputEdit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL |
            ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_INPUT, g_hInst, nullptr);
        SendMessageW(g_inputEdit, WM_SETFONT, (WPARAM)g_font, TRUE);
        // Subclass to intercept Enter / Ctrl+L / Ctrl+R
        g_origInputProc = (WNDPROC)SetWindowLongPtrW(
            g_inputEdit, GWLP_WNDPROC, (LONG_PTR)InputEditProc);

        g_sendBtn = CreateWindowW(L"BUTTON", L"Send  ⏎",
            WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_SEND, g_hInst, nullptr);
        SendMessageW(g_sendBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_analyzeBtn = CreateWindowW(L"BUTTON", L"Analyze Selection",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_ANALYZE, g_hInst, nullptr);
        SendMessageW(g_analyzeBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_captureBtn = CreateWindowW(L"BUTTON", L"Capture Viewport",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_CAPTURE, g_hInst, nullptr);
        SendMessageW(g_captureBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_configBtn = CreateWindowW(L"BUTTON", L"Config Folder",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_CONFIG, g_hInst, nullptr);
        SendMessageW(g_configBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        // Status bar
        g_statusBar = CreateWindowExW(0, STATUSCLASSNAMEW, nullptr,
            WS_CHILD | WS_VISIBLE | SBARS_SIZEGRIP,
            0, 0, 0, 0, hWnd, (HMENU)(INT_PTR)ID_STATUS, g_hInst, nullptr);

        RebuildBaseStatus();
        MCPChatUI::SetStatus("");  // paint initial

        // Initial help hint — only once per window create
        if (g_historyEdit) {
            AppendColoredText(g_historyEdit,
                L"MCP Chat  —  Enter sends, Shift+Enter newline, Ctrl+L clear, Ctrl+R reload, /help for more\r\n\r\n",
                COL_DIM, false);
        }
        return 0;
    }

    case WM_SIZE: {
        int w = LOWORD(lParam), h = HIWORD(lParam);
        LayoutChildren(w, h);
        return 0;
    }

    case WM_ACTIVATE:
        if (LOWORD(wParam) != WA_INACTIVE)
            DisableAccelerators();
        else
            EnableAccelerators();
        return 0;

    case WM_SETFOCUS:
        if (g_inputEdit) SetFocus(g_inputEdit);
        return 0;

    case WM_COMMAND: {
        int id = LOWORD(wParam);
        if (id == ID_SEND)    { DoSend(); return 0; }
        if (id == ID_ANALYZE) { DoAction("analyze_selection"); return 0; }
        if (id == ID_CAPTURE) { DoAction("capture"); return 0; }
        if (id == ID_CONFIG)  { OpenConfigFolder(); return 0; }
        return 0;
    }

    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, COL_FG);
        SetBkColor(hdc, COL_EDIT_BG);
        return (LRESULT)g_editBrush;
    }

    case WM_CTLCOLORSTATIC: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, COL_FG);
        SetBkColor(hdc, COL_BG);
        return (LRESULT)g_bgBrush;
    }

    case WM_CTLCOLORBTN: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, COL_FG);
        return (LRESULT)g_bgBrush;
    }

    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(hWnd, &rc);
        FillRect((HDC)wParam, &rc, g_bgBrush);
        return 1;
    }

    case WM_CLOSE:
        EnableAccelerators();
        ShowWindow(hWnd, SW_HIDE);
        return 0;

    case WM_DESTROY:
        if (g_font)      { DeleteObject(g_font); g_font = nullptr; }
        if (g_monoFont)  { DeleteObject(g_monoFont); g_monoFont = nullptr; }
        if (g_bgBrush)   { DeleteObject(g_bgBrush); g_bgBrush = nullptr; }
        if (g_editBrush) { DeleteObject(g_editBrush); g_editBrush = nullptr; }
        g_chatWnd = g_historyEdit = g_inputEdit = nullptr;
        g_sendBtn = g_analyzeBtn = g_captureBtn = g_configBtn = g_statusBar = nullptr;
        g_origInputProc = nullptr;
        return 0;
    }
    return DefWindowProcW(hWnd, msg, wParam, lParam);
}

// ── Public API ──────────────────────────────────────────────────

void MCPChatUI::Init(HINSTANCE hInst) {
    g_hInst = hInst;

    // Load RichEdit 4.1 (msftedit.dll exports RICHEDIT50W window class)
    if (!g_richedMod) g_richedMod = LoadLibraryW(L"msftedit.dll");

    INITCOMMONCONTROLSEX icc = { sizeof(icc), ICC_BAR_CLASSES | ICC_STANDARD_CLASSES };
    InitCommonControlsEx(&icc);

    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = ChatWndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr;   // we paint WM_ERASEBKGND ourselves
    wc.lpszClassName = L"MCPChatWindow";
    RegisterClassExW(&wc);
}

void MCPChatUI::Show(MCPBridgeGUP* gup) {
    g_gup = gup;
    if (g_chatWnd) {
        ShowWindow(g_chatWnd, SW_SHOW);
        SetForegroundWindow(g_chatWnd);
        if (g_inputEdit) SetFocus(g_inputEdit);
        return;
    }

    HWND maxWnd = GetCOREInterface()->GetMAXHWnd();
    g_chatWnd = CreateWindowExW(
        WS_EX_TOOLWINDOW,
        L"MCPChatWindow", L"MCP Chat",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 720, 640,
        maxWnd, nullptr, g_hInst, nullptr);

    if (g_chatWnd && g_inputEdit) SetFocus(g_inputEdit);
}

void MCPChatUI::Hide() {
    if (g_chatWnd) {
        EnableAccelerators();
        ShowWindow(g_chatWnd, SW_HIDE);
    }
}

void MCPChatUI::Destroy() {
    if (g_chatWnd) {
        EnableAccelerators();
        DestroyWindow(g_chatWnd);
        g_chatWnd = nullptr;
    }
    if (g_richedMod) { FreeLibrary(g_richedMod); g_richedMod = nullptr; }
}

bool MCPChatUI::IsVisible() {
    return g_chatWnd && IsWindowVisible(g_chatWnd);
}

void MCPChatUI::SetMessageCallback(MessageCallback cb) { g_onMessage = cb; }
void MCPChatUI::SetActionCallback(ActionCallback cb)  { g_onAction = cb;  }
