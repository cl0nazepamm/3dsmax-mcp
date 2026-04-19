#include "mcp_bridge/chat_ui.h"
#include "mcp_bridge/handler_helpers.h"
#include <commctrl.h>

using namespace HandlerHelpers;

// ── State ───────────────────────────────────────────────────────
static HWND g_chatWnd = nullptr;
static HWND g_historyEdit = nullptr;
static HWND g_inputEdit = nullptr;
static HWND g_sendBtn = nullptr;
static HWND g_analyzeBtn = nullptr;
static HWND g_captureBtn = nullptr;
static HWND g_doneBtn = nullptr;
static HFONT g_font = nullptr;
static HBRUSH g_bgBrush = nullptr;
static HBRUSH g_editBrush = nullptr;
static HINSTANCE g_hInst = nullptr;
static MCPBridgeGUP* g_gup = nullptr;

static MCPChatUI::MessageCallback g_onMessage;
static MCPChatUI::ActionCallback g_onAction;

static const int ID_SEND = 1001;
static const int ID_ANALYZE = 1002;
static const int ID_CAPTURE = 1003;
static const int ID_DONE = 1004;
static const int ID_INPUT = 1005;
static const int ID_HISTORY = 1006;

// ── Helpers ─────────────────────────────────────────────────────

void MCPChatUI::AppendMessage(const std::string& role, const std::string& text) {
    if (!g_historyEdit) return;
    std::string prefix = (role == "user") ? "You: " :
                         (role == "tool") ? "[Tool]: " : "AI: ";
    std::string line = prefix + text + "\r\n";
    std::wstring wline = Utf8ToWide(line);

    int len = GetWindowTextLengthW(g_historyEdit);
    SendMessageW(g_historyEdit, EM_SETSEL, len, len);
    SendMessageW(g_historyEdit, EM_REPLACESEL, FALSE, (LPARAM)wline.c_str());
    SendMessageW(g_historyEdit, EM_SCROLLCARET, 0, 0);
}

void MCPChatUI::SetStatus(const std::string& status) {
    if (!g_chatWnd) return;
    std::string title = "MCP Chat";
    if (!status.empty()) title += " - " + status;
    SetWindowTextW(g_chatWnd, Utf8ToWide(title).c_str());
}

static void DoSend() {
    wchar_t buf[4096];
    GetWindowTextW(g_inputEdit, buf, 4096);
    std::string text = WideToUtf8(buf);
    if (text.empty()) return;

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
        MCPChatUI::AppendMessage("user", "[Analyze: " + (detail.empty() ? "nothing selected" : detail) + "]");
    } else if (action == "capture") {
        MCPChatUI::AppendMessage("user", "[Capture & Send]");
    } else if (action == "done") {
        MCPChatUI::AppendMessage("user", "[Done]");
    }

    if (g_onAction) g_onAction(action, detail);
}

// ── Window proc ─────────────────────────────────────────────────

static LRESULT CALLBACK ChatWndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_CREATE: {
        g_bgBrush = CreateSolidBrush(RGB(30, 30, 30));
        g_editBrush = CreateSolidBrush(RGB(40, 40, 40));
        g_font = CreateFontW(-14, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                             DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Consolas");

        RECT rc;
        GetClientRect(hWnd, &rc);
        int w = rc.right, h = rc.bottom;

        g_historyEdit = CreateWindowExW(0, L"EDIT", L"",
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
            5, 5, w - 10, h - 80, hWnd, (HMENU)(INT_PTR)ID_HISTORY, g_hInst, nullptr);
        SendMessageW(g_historyEdit, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_inputEdit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            5, h - 70, w - 80, 25, hWnd, (HMENU)(INT_PTR)ID_INPUT, g_hInst, nullptr);
        SendMessageW(g_inputEdit, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_sendBtn = CreateWindowW(L"BUTTON", L"Send",
            WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
            w - 70, h - 70, 65, 25, hWnd, (HMENU)(INT_PTR)ID_SEND, g_hInst, nullptr);
        SendMessageW(g_sendBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        int btnW = (w - 20) / 3;
        g_analyzeBtn = CreateWindowW(L"BUTTON", L"Analyze Selection",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            5, h - 38, btnW, 28, hWnd, (HMENU)(INT_PTR)ID_ANALYZE, g_hInst, nullptr);
        SendMessageW(g_analyzeBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_captureBtn = CreateWindowW(L"BUTTON", L"Capture & Send",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            10 + btnW, h - 38, btnW, 28, hWnd, (HMENU)(INT_PTR)ID_CAPTURE, g_hInst, nullptr);
        SendMessageW(g_captureBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        g_doneBtn = CreateWindowW(L"BUTTON", L"Done",
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            15 + btnW * 2, h - 38, btnW, 28, hWnd, (HMENU)(INT_PTR)ID_DONE, g_hInst, nullptr);
        SendMessageW(g_doneBtn, WM_SETFONT, (WPARAM)g_font, TRUE);

        return 0;
    }

    case WM_SIZE: {
        int w = LOWORD(lParam), h = HIWORD(lParam);
        if (g_historyEdit) MoveWindow(g_historyEdit, 5, 5, w - 10, h - 80, TRUE);
        if (g_inputEdit) MoveWindow(g_inputEdit, 5, h - 70, w - 80, 25, TRUE);
        if (g_sendBtn) MoveWindow(g_sendBtn, w - 70, h - 70, 65, 25, TRUE);
        int btnW = (w - 20) / 3;
        if (g_analyzeBtn) MoveWindow(g_analyzeBtn, 5, h - 38, btnW, 28, TRUE);
        if (g_captureBtn) MoveWindow(g_captureBtn, 10 + btnW, h - 38, btnW, 28, TRUE);
        if (g_doneBtn) MoveWindow(g_doneBtn, 15 + btnW * 2, h - 38, btnW, 28, TRUE);
        return 0;
    }

    case WM_ACTIVATE:
        if (LOWORD(wParam) != WA_INACTIVE)
            DisableAccelerators();
        else
            EnableAccelerators();
        return 0;

    case WM_COMMAND: {
        int id = LOWORD(wParam);
        if (id == ID_SEND) DoSend();
        else if (id == ID_ANALYZE) DoAction("analyze_selection");
        else if (id == ID_CAPTURE) DoAction("capture");
        else if (id == ID_DONE) DoAction("done");
        return 0;
    }

    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, RGB(220, 220, 220));
        SetBkColor(hdc, RGB(40, 40, 40));
        return (LRESULT)g_editBrush;
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
        if (g_font) { DeleteObject(g_font); g_font = nullptr; }
        if (g_bgBrush) { DeleteObject(g_bgBrush); g_bgBrush = nullptr; }
        if (g_editBrush) { DeleteObject(g_editBrush); g_editBrush = nullptr; }
        g_chatWnd = nullptr;
        g_historyEdit = nullptr;
        g_inputEdit = nullptr;
        return 0;
    }
    return DefWindowProcW(hWnd, msg, wParam, lParam);
}

// ── Public API ──────────────────────────────────────────────────

void MCPChatUI::Init(HINSTANCE hInst) {
    g_hInst = hInst;
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = ChatWndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MCPChatWindow";
    RegisterClassExW(&wc);
}

void MCPChatUI::Show(MCPBridgeGUP* gup) {
    g_gup = gup;
    if (g_chatWnd) {
        ShowWindow(g_chatWnd, SW_SHOW);
        SetForegroundWindow(g_chatWnd);
        return;
    }

    HWND maxWnd = GetCOREInterface()->GetMAXHWnd();
    g_chatWnd = CreateWindowExW(
        WS_EX_TOOLWINDOW,
        L"MCPChatWindow", L"MCP Chat",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 420, 520,
        maxWnd, nullptr, g_hInst, nullptr);
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
}

bool MCPChatUI::IsVisible() {
    return g_chatWnd && IsWindowVisible(g_chatWnd);
}

void MCPChatUI::SetMessageCallback(MessageCallback cb) { g_onMessage = cb; }
void MCPChatUI::SetActionCallback(ActionCallback cb) { g_onAction = cb; }
